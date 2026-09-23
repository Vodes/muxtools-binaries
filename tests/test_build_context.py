import shlex
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

from muxtools_binaries.build import BuildContext
from muxtools_binaries.cli import _build_in_container
from muxtools_binaries.models import ArchiveSource, Package, Source


def _git(directory: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


def _commit(directory: Path, message: str) -> str:
    _git(directory, "add", ".")
    _git(directory, "commit", "-qm", message)
    return _git(directory, "rev-parse", "HEAD")


def _repository(path: Path, license_name: str) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "tests@example.test")
    _git(path, "config", "user.name", "Build context tests")
    (path / license_name).write_text("license")
    _commit(path, "initial")
    return path


def test_recursive_source_collects_nested_submodule_notices(package, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    nested = _repository(tmp_path / "nested", "COPYING")
    child = _repository(tmp_path / "child", "LICENSE.txt")
    _git(child, "submodule", "add", str(nested), "third_party/nested")
    _commit(child, "add nested submodule")
    parent = _repository(tmp_path / "parent", "NOTICE")
    _git(parent, "submodule", "add", str(child), "lib/child")
    commit = _commit(parent, "add child submodule")

    stage = tmp_path / "stage"
    stage.mkdir()
    ctx = BuildContext(tmp_path, package, "linux-x86_64", tmp_path / "work", stage, 1)
    pin = Source.model_construct(repository=str(parent), tag="v1", commit=commit, recursive=True)
    source = ctx.source("example", pin)

    assert _git(source, "remote", "get-url", "origin") == str(parent)
    assert (stage / "licenses/example/NOTICE").read_text() == "license"
    assert (stage / "licenses/example/lib/child/LICENSE.txt").read_text() == "license"
    assert (stage / "licenses/example/lib/child/third_party/nested/COPYING").read_text() == "license"


def test_archive_source_extracts_one_directory_and_collects_notices(package, tmp_path, monkeypatch):
    source = tmp_path / "example-1.0"
    source.mkdir()
    (source / "LICENSE").write_text("license")
    archive = tmp_path / "example.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(source, arcname=source.name)

    stage = tmp_path / "stage"
    stage.mkdir()
    ctx = BuildContext(tmp_path, package, "linux-x86_64", tmp_path / "work", stage, 1)
    pin = ArchiveSource(url="https://example.test/example.tar.gz", sha256="0" * 64, format="tar.gz")
    downloads = []
    monkeypatch.setattr("muxtools_binaries.build.download", lambda *args: downloads.append(args) or archive)

    unpacked = ctx.source("example", pin)

    assert unpacked.name == "example-1.0"
    assert (stage / "licenses/example/LICENSE").read_text() == "license"
    assert downloads == [(pin.url, pin.sha256, ctx.cache)]


def test_cmake_defaults_overrides_and_install_environment(package, tmp_path, monkeypatch):
    ctx = BuildContext(tmp_path, package, "linux-x86_64", tmp_path / "work", tmp_path / "stage", 2)
    calls = []
    monkeypatch.setattr("muxtools_binaries.build.run", lambda args, **kwargs: calls.append((args, kwargs)))
    build = ctx.cmake(
        "library", tmp_path / "source", {"CMAKE_BUILD_TYPE": "Debug", "BUILD_TESTING": False}, install=True
    )

    configure = calls[0][0]
    assert "-DBUILD_SHARED_LIBS=OFF" in configure
    assert "-DCMAKE_BUILD_TYPE=Debug" in configure
    assert f"-DCMAKE_PREFIX_PATH={ctx.prefix}" in configure
    assert "-DCMAKE_INSTALL_LIBDIR=lib" in configure
    assert "-DBUILD_TESTING=OFF" in configure
    assert calls[1][0] == ["cmake", "--build", build, "--parallel", 2]
    assert calls[2][0] == ["cmake", "--install", build]
    assert calls[2][1]["env"] == calls[0][1]["env"]

    calls.clear()
    ctx.cmake("library", tmp_path / "source", {})
    assert "-DCMAKE_BUILD_TYPE=Release" in calls[0][0]
    assert len(calls) == 2


def test_native_macos_environment_and_cmake_keep_private_prefix_without_cross_isolation(package, tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "946684800")
    definition = package.model_dump()
    definition["targets"] = {"macos-arm64": {"toolchain": "clang"}}
    native = Package.model_validate(definition)
    ctx = BuildContext(tmp_path, native, "macos-arm64", tmp_path / "work", tmp_path / "stage", 2)

    env = ctx.build_environment()
    assert env["MACOSX_DEPLOYMENT_TARGET"] == "12.0"
    assert env["SOURCE_DATE_EPOCH"] == "946684800"
    assert not any(flag.startswith("-march") for flag in shlex.split(env["CFLAGS"]))

    calls = []
    monkeypatch.setattr("muxtools_binaries.build.run", lambda args, **kwargs: calls.append((args, kwargs)))
    ctx.cmake("library", tmp_path / "source", {})
    configure = calls[0][0]
    assert f"-DCMAKE_PREFIX_PATH={ctx.prefix}" in configure
    assert not any("CMAKE_FIND_ROOT_PATH" in str(argument) for argument in configure)


def test_build_epoch_defaults_to_current_time_and_accepts_override(package, tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.setattr("muxtools_binaries.build.time.time", lambda: 1_234_567_890)
    ctx = BuildContext(tmp_path, package, "linux-x86_64", tmp_path / "work", tmp_path / "stage", 1)
    assert ctx.build_epoch == 1_234_567_890
    assert ctx.build_environment()["SOURCE_DATE_EPOCH"] == "1234567890"

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "946684800")
    ctx = BuildContext(tmp_path, package, "linux-x86_64", tmp_path / "work", tmp_path / "stage", 1)
    assert ctx.build_epoch == 946_684_800
    assert ctx.build_environment()["SOURCE_DATE_EPOCH"] == "946684800"


def test_container_build_forwards_source_date_epoch(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("muxtools_binaries.cli.run", lambda args: calls.append(args))
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "946684800")
    args = SimpleNamespace(target="linux-x86_64", package="example", channel="test", jobs=1)

    _build_in_container(tmp_path, args, "builder:test", "revision")

    epoch_index = calls[0].index("SOURCE_DATE_EPOCH=946684800")
    assert calls[0][epoch_index - 1] == "-e"
