import shlex
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from muxtools_binaries.build import BuildContext, builder_configuration
from muxtools_binaries.checks import reject_static_windows_runtimes
from muxtools_binaries.models import Package
from muxtools_binaries.targets import TARGETS, toolchain_spec


def windows_package(package: Package, target: str, toolchain: str, **settings) -> Package:
    definition = package.model_dump()
    definition["targets"] = {target: {"toolchain": toolchain, **settings}}
    return Package.model_validate(definition)


def context(tmp_path: Path, package: Package, target: str, toolchain: str) -> BuildContext:
    return BuildContext(
        tmp_path,
        windows_package(package, target, toolchain),
        target,
        tmp_path / "work",
        tmp_path / "stage",
        2,
    )


def test_windows_registry_matrix_and_arm_baseline(package):
    assert TARGETS["windows-arm64"].builder == "manylinux-arm64"
    assert TARGETS["windows-arm64"].build_runner == "ubuntu-24.04-arm"
    assert TARGETS["windows-arm64"].smoke_runner == "windows-11-arm"
    assert set(TARGETS["windows-arm64"].cpu_flags) == {"baseline"}
    assert toolchain_spec("windows-x86_64", "gcc").cc == "x86_64-w64-mingw32-gcc"
    with pytest.raises(ValueError, match="Unsupported toolchain gcc for windows-arm64"):
        toolchain_spec("windows-arm64", "gcc")
    with pytest.raises(ValidationError, match="Unsupported CPU level for windows-arm64"):
        windows_package(package, "windows-arm64", "clang", cpu_levels=["baseline", "avx2"])


@pytest.mark.parametrize(
    ("target", "triple", "arch_flag"),
    [
        ("windows-x86_64", "x86_64-w64-mingw32", "-march=x86-64-v2"),
        ("windows-arm64", "aarch64-w64-mingw32", "-march=armv8-a"),
    ],
)
def test_llvm_mingw_environment_never_queries_gcc(package, tmp_path, monkeypatch, target, triple, arch_flag):
    ctx = context(tmp_path, package, target, "clang")
    monkeypatch.setattr(
        "muxtools_binaries.build.run",
        lambda *args, **kwargs: pytest.fail(f"Windows Clang queried an external compiler: {args}"),
    )
    env = ctx.build_environment()
    cflags = shlex.split(env["CFLAGS"])
    ldflags = shlex.split(env["LDFLAGS"])

    assert env["CC"] == f"/opt/llvm-mingw/bin/{triple}-clang"
    assert env["CXX"] == f"/opt/llvm-mingw/bin/{triple}-clang++"
    assert env["AR"] == f"/opt/llvm-mingw/bin/{triple}-ar"
    assert cflags == [arch_flag, f"--target={triple}", f"--sysroot=/opt/llvm-mingw/{triple}"]
    assert {"-static-libstdc++", "-static-libgcc", "-static", "-Wl,-Bstatic", "-pthread", "-fuse-ld=lld"} <= set(
        ldflags
    )
    assert env["PKG_CONFIG_LIBDIR"] == str(ctx.prefix / "lib/pkgconfig")
    assert "/opt/xwin" not in " ".join(env.values())


@pytest.mark.parametrize(
    ("target", "triple", "ms_arch"),
    [
        ("windows-x86_64", "x86_64-pc-windows-msvc", "x64"),
        ("windows-arm64", "aarch64-pc-windows-msvc", "arm64"),
    ],
)
def test_clang_msvc_defaults_to_gnu_frontend(package, tmp_path, target, triple, ms_arch):
    ctx = context(tmp_path, package, target, "clang-msvc")
    env = ctx.build_environment()
    cflags = shlex.split(env["CFLAGS"])
    ldflags = shlex.split(env["LDFLAGS"])

    assert env["CC"] == "/opt/llvm-mingw/bin/clang"
    assert env["CXX"] == "/opt/llvm-mingw/bin/clang++"
    assert env["AR"] == "/opt/llvm-mingw/bin/llvm-ar"
    assert env["RC"] == "/opt/llvm-mingw/bin/llvm-windres"
    assert f"--target={triple}" in cflags
    assert "-fms-runtime-lib=static" in cflags
    assert f"-L/opt/xwin/VC/Tools/MSVC/14.44.17.14/lib/{ms_arch}" in ldflags
    assert "/opt/llvm-mingw/x86_64-w64-mingw32" not in " ".join(env.values())
    assert env["PKG_CONFIG_LIBDIR"] == str(ctx.prefix / "lib/pkgconfig")


def test_clang_msvc_host_environment_drops_target_resource_tools(package, tmp_path):
    env = context(tmp_path, package, "windows-arm64", "clang-msvc").host_environment()

    assert "WINDRES" not in env
    assert "RC" not in env
    assert "RCFLAGS" not in env


def test_autotools_keeps_normal_clang_for_msvc(package, tmp_path, monkeypatch):
    ctx = context(tmp_path, package, "windows-arm64", "clang-msvc")
    source = tmp_path / "source"
    source.mkdir()
    (source / "configure").touch()
    calls = []
    monkeypatch.setattr("muxtools_binaries.build.run", lambda args, **kwargs: calls.append((args, kwargs)))

    ctx.autotools("example", source)

    configure_env = calls[0][1]["env"]
    assert configure_env["CC"].startswith("/opt/llvm-mingw/bin/clang ")
    assert "clang-cl" not in configure_env["CC"]
    assert "--host=aarch64-pc-windows-msvc" in calls[0][0]
    assert configure_env["RC"] == "/opt/llvm-mingw/bin/llvm-windres"


@pytest.mark.parametrize("configure_input", ["configure.ac", "configure.in"])
def test_autotools_uses_mingw_host_for_mingw_only_windows_detection(package, tmp_path, monkeypatch, configure_input):
    ctx = context(tmp_path, package, "windows-arm64", "clang-msvc")
    source = tmp_path / "source"
    source.mkdir()
    (source / "configure").touch()
    (source / configure_input).write_text("AS_CASE([${host_os}], [*mingw*], [windows_host=yes])\n")
    calls = []
    monkeypatch.setattr("muxtools_binaries.build.run", lambda args, **kwargs: calls.append((args, kwargs)))

    ctx.autotools("example", source)

    assert "--host=aarch64-w64-mingw32" in calls[0][0]
    assert "--target=aarch64-pc-windows-msvc" in calls[0][1]["env"]["CC"]


def test_cmake_clang_cl_is_local_to_msvc(package, tmp_path, monkeypatch):
    ctx = context(tmp_path, package, "windows-arm64", "clang-msvc")
    calls = []
    monkeypatch.setattr("muxtools_binaries.build.run", lambda args, **kwargs: calls.append((args, kwargs)))
    ctx.cmake("example", tmp_path / "source", {}, clang_cl=True)

    configure, configure_env = calls[0][0], calls[0][1]["env"]
    assert configure_env["CC"] == "/opt/llvm-mingw/bin/clang-cl"
    assert {"/MT", "/winsysroot", "/opt/xwin", "--target=aarch64-pc-windows-msvc"} <= set(
        shlex.split(configure_env["CFLAGS"])
    )
    assert "-DCMAKE_AR=/opt/llvm-mingw/bin/llvm-lib" in configure
    assert "-DCMAKE_LINKER=/opt/llvm-mingw/bin/lld-link" in configure
    assert "-DCMAKE_MT=/usr/bin/llvm-mt" in configure
    assert "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded" in configure
    assert "/libpath:/opt/xwin/VC/Tools/MSVC/14.44.17.14/lib/arm64" in configure_env["LDFLAGS"]
    assert "/libpath:/opt/xwin/WindowsKits/10/Lib/10.0.26100/ucrt/arm64" in configure_env["LDFLAGS"]

    normal = context(tmp_path, package, "windows-arm64", "clang")
    with pytest.raises(ValueError, match="requires the clang-msvc toolchain"):
        normal.cmake("invalid", tmp_path / "source", {}, clang_cl=True)


@pytest.mark.parametrize(
    ("abi", "library"),
    [
        ("mingw-ucrt", "libc++.dll"),
        ("mingw-ucrt", "libunwind.dll"),
        ("msvc", "vcruntime140.dll"),
        ("msvc", "msvcp140.dll"),
        ("msvc", "ucrtbase.dll"),
    ],
)
def test_static_windows_runtime_imports_are_rejected(abi, library):
    with pytest.raises(ValueError, match="Imported .* runtime"):
        reject_static_windows_runtimes(["kernel32.dll", library], abi, "hello.exe")
    reject_static_windows_runtimes(["kernel32.dll"], abi, "hello.exe")


def test_builder_lock_schema_and_pins():
    root = Path(__file__).parents[1]
    lock = tomllib.loads((root / "builder/lock.toml").read_text())

    assert lock["schema_version"] == 3
    assert lock["toolchains"]["llvm-mingw"]["version"] == "20260616"
    assert set(lock["toolchains"]["llvm-mingw"]["hosts"]) == {"x86_64", "arm64"}
    assert lock["toolchains"]["xwin"]["version"] == "0.9.0"
    assert lock["toolchains"]["xwin"]["sdk"] == "10.0.26100"
    assert lock["toolchains"]["xwin"]["crt"] == "14.44.17.14"
    assert lock["toolchains"]["xwin"]["toolset"] == "14.44.35220"
    assert builder_configuration(root, "windows-x86_64")["name"] == "manylinux-x86_64"
    assert builder_configuration(root, "windows-arm64")["name"] == "manylinux-arm64"
    assert builder_configuration(root, "macos-arm64") == {
        "name": "native-macos-arm64",
        "kind": "native",
        "platform": None,
    }
