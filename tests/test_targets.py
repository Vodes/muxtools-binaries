import os
import struct
from types import SimpleNamespace

import pytest

from muxtools_binaries.binary_checks import audit_macho, binary_identity, runtime_audit, structural
from muxtools_binaries.build import BuildContext, copy_mingw_runtimes
from muxtools_binaries.models import TARGETS, Package


def header(target: str) -> bytes:
    info = TARGETS[target]
    if info.binary_format == "elf":
        data = bytearray(20)
        data[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<H", data, 18, 62 if info.arch == "x86_64" else 183)
    elif info.binary_format == "pe":
        data = bytearray(64 + 6)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<H", data, 68, 0x8664 if info.arch == "x86_64" else 0xAA64)
    else:
        data = bytearray(56)
        struct.pack_into(
            "<III",
            data,
            0,
            0xFEEDFACF,
            0x01000007 if info.arch == "x86_64" else 0x0100000C,
            3 if info.arch == "x86_64" else 0,
        )
        struct.pack_into("<II", data, 16, 1, 24)
        struct.pack_into("<IIIIII", data, 32, 0x32, 24, 1, 13 << 16, (15 << 16) + (5 << 8), 0)
    return bytes(data)


def test_structural_rejects_wrong_architecture_in_bundled_payload(tmp_path):
    path = tmp_path / "program"
    path.write_bytes(header("linux-arm64"))
    path.chmod(0o755)
    (tmp_path / "private.so").write_bytes(header("linux-x86_64"))
    with pytest.raises(ValueError, match="Wrong target"):
        structural(tmp_path, dict(target="linux-arm64", binaries={"program": {"baseline": "program"}}))


def test_binary_identity_rejects_truncated_header(tmp_path):
    path = tmp_path / "program"
    path.write_bytes(header("linux-x86_64")[:19])
    with pytest.raises(ValueError):
        binary_identity(path)


def test_universal_and_private_payload_rejected(tmp_path):
    path = tmp_path / "program"
    path.write_bytes(header("macos-arm64"))
    path.chmod(0o755)
    payload = tmp_path / "private.dylib"
    payload.write_bytes(b"\xca\xfe\xba\xbf" + bytes(64))
    with pytest.raises(ValueError, match="Universal"):
        structural(tmp_path, dict(target="macos-arm64", binaries={"program": {"baseline": "program"}}))


def test_macos_runtime_minimum_and_dependencies(tmp_path):
    path = tmp_path / "program"
    data = bytearray(header("macos-arm64"))
    path.write_bytes(data)
    audit_macho(path)
    struct.pack_into("<I", data, 44, 14 << 16)
    path.write_bytes(data)
    with pytest.raises(ValueError, match="deployment minimum"):
        audit_macho(path)
    data = bytearray(header("macos-arm64"))
    library = b"@rpath/libbroken.dylib\0"
    length = (24 + len(library) + 7) // 8 * 8
    data.extend(struct.pack("<IIIIII", 0xC, length, 24, 0, 0, 0) + library + bytes(length - 24 - len(library)))
    struct.pack_into("<II", data, 16, 2, 24 + length)
    path.write_bytes(data)
    with pytest.raises(ValueError, match="Non-system"):
        audit_macho(path)


def test_runtime_audits_linux_and_static_crt(tmp_path, monkeypatch):
    path = tmp_path / "program"
    path.write_bytes(header("linux-arm64"))
    monkeypatch.setattr(
        "muxtools_binaries.binary_checks.run",
        lambda *a, **kw: SimpleNamespace(stdout="[Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]"),
    )
    with pytest.raises(ValueError, match="loader"):
        runtime_audit(tmp_path, dict(target="linux-arm64"))
    monkeypatch.setattr("muxtools_binaries.binary_checks.run", lambda *a, **kw: SimpleNamespace(stdout="GLIBC_2.35"))
    with pytest.raises(ValueError, match="glibc"):
        runtime_audit(tmp_path, dict(target="linux-arm64"))
    path.write_bytes(header("windows-arm64"))
    monkeypatch.setattr(
        "muxtools_binaries.binary_checks.run", lambda *a, **kw: SimpleNamespace(stdout="Name: ucrtbase.dll")
    )
    with pytest.raises(ValueError, match="static release CRT"):
        runtime_audit(tmp_path, dict(target="windows-arm64", build=dict(compiler="clang-msvc")))


@pytest.mark.parametrize(
    ("target", "compiler"),
    [("linux-arm64", "gcc"), ("windows-arm64", "clang")],
)
def test_cross_dependency_environment_isolation(package, tmp_path, monkeypatch, target, compiler):
    definition = package.model_dump()
    definition["targets"] = {target: {"compiler": compiler}}
    ctx = BuildContext(tmp_path, Package.model_validate(definition), target, tmp_path, tmp_path, 1)
    for key in (
        "CPATH",
        "C_INCLUDE_PATH",
        "CPLUS_INCLUDE_PATH",
        "LIBRARY_PATH",
        "CMAKE_PREFIX_PATH",
        "PKG_CONFIG_PATH",
        "PKG_CONFIG_SYSROOT_DIR",
    ):
        monkeypatch.setenv(key, "/host/leak")
    env = ctx.environment()
    assert not any("/host/leak" in value for value in env.values())
    assert "/usr/lib/pkgconfig" not in env["PKG_CONFIG_LIBDIR"].split(os.pathsep)


@pytest.mark.parametrize("library", ["libc++.dll", "libunwind.dll"])
def test_llvm_mingw_runtime_must_be_bundled(tmp_path, monkeypatch, library):
    (tmp_path / "program.exe").write_bytes(header("windows-arm64"))
    monkeypatch.setattr(
        "muxtools_binaries.binary_checks.run", lambda *a, **kw: SimpleNamespace(stdout=f"Name: {library}")
    )
    data = dict(target="windows-arm64", build=dict(compiler="clang"))
    with pytest.raises(ValueError, match="Unbundled compiler runtime"):
        runtime_audit(tmp_path, data)
    (tmp_path / library).write_bytes(header("windows-arm64"))
    runtime_audit(tmp_path, data)


def test_mingw_runtime_copy_follows_dll_dependencies(tmp_path, monkeypatch):
    stage, sysroot = tmp_path / "stage", tmp_path / "sysroot"
    stage.mkdir()
    sysroot.mkdir()
    (stage / "program.exe").touch()
    for library in ("libc++.dll", "libunwind.dll", "libwinpthread-1.dll"):
        (sysroot / library).write_bytes(library.encode())
    imports = {
        "program.exe": "Name: libc++.dll\nName: kernel32.dll",
        "libc++.dll": "Name: LIBUNWIND.dll\nName: libwinpthread-1.dll",
        "libunwind.dll": "Name: libwinpthread-1.dll",
        "libwinpthread-1.dll": "Name: kernel32.dll",
    }
    monkeypatch.setattr(
        "muxtools_binaries.build.run",
        lambda command, **kw: SimpleNamespace(stdout=imports[command[-1].name.casefold()]),
    )
    copy_mingw_runtimes(stage, sysroot)
    assert {path.name.casefold() for path in stage.iterdir()} == set(imports)
    assert (stage / "LIBUNWIND.dll").read_bytes() == b"libunwind.dll"
