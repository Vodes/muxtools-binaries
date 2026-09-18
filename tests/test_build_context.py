import subprocess
from pathlib import Path

from muxtools_binaries.build import BuildContext
from muxtools_binaries.models import Source


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
