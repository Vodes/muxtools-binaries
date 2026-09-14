"""Portable binary validation and builder-only runtime audits."""

import os
import re
import struct
from pathlib import Path
from typing import Any, Literal

from .checks import CheckSuite
from .io import run
from .models import TARGETS

type BinaryFormat = Literal["elf", "pe", "macho", "script"]


def binary_identity(path: Path) -> tuple[BinaryFormat, str | None]:
    with path.open("rb") as stream:
        header = stream.read(64)
        if header.startswith(b"\x7fELF"):
            if len(header) < 20 or header[4:7] != b"\x02\x01\x01":
                raise ValueError(f"Malformed ELF header: {path}")
            machine = struct.unpack_from("<H", header, 18)[0]
            arch = {62: "x86_64", 183: "arm64"}.get(machine)
            if arch is None:
                raise ValueError(f"Malformed or unsupported ELF: {path}")
            return "elf", arch
        if header.startswith(b"MZ"):
            if len(header) < 64:
                raise ValueError(f"Truncated PE: {path}")
            offset = struct.unpack_from("<I", header, 60)[0]
            if offset < 64:
                raise ValueError(f"Malformed PE offset: {path}")
            stream.seek(offset)
            pe = stream.read(6)
            if len(pe) != 6 or pe[:4] != b"PE\0\0":
                raise ValueError(f"Malformed PE header: {path}")
            machine = struct.unpack_from("<H", pe, 4)[0]
            arch = {0x8664: "x86_64", 0xAA64: "arm64"}.get(machine)
            if arch is None:
                raise ValueError(f"Malformed or unsupported PE: {path}")
            return "pe", arch
        if header[:4] in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"):
            raise ValueError(f"Universal binaries are unsupported: {path}")
        if header[:4] == b"\xcf\xfa\xed\xfe":
            if len(header) < 12:
                raise ValueError(f"Truncated Mach-O: {path}")
            machine, subtype = struct.unpack_from("<II", header, 4)
            arch = {0x01000007: "x86_64", 0x0100000C: "arm64"}.get(machine)
            if arch is None:
                raise ValueError(f"Malformed or unsupported Mach-O: {path}")
            # Reject arm64e and x86_64h, which exceed the registered baseline architectures.
            if subtype & 0xFFFFFF != (3 if arch == "x86_64" else 0):
                raise ValueError(f"Unsupported Mach-O CPU subtype: {path}")
            return "macho", arch
        if header.startswith(b"#!/bin/sh\n"):
            return "script", None
    raise ValueError(f"Unknown executable format: {path}")


def binary_format(path: Path) -> BinaryFormat:
    return binary_identity(path)[0]


def structural(stage: Path, data: dict[str, Any], suite: CheckSuite | None = None) -> None:
    info = TARGETS[data["target"]]
    files = dict(suite.files) if suite else {}
    for variants in data["binaries"].values():
        for filename in variants.values():
            files.setdefault(filename, info.binary_format)
    for filename, expected in files.items():
        path = stage / filename
        kind, arch = binary_identity(path)
        if kind != expected or (arch is not None and arch != info.arch):
            raise ValueError(f"Wrong target format or architecture: {filename}")
        if info.os != "windows" and os.name != "nt" and not path.stat().st_mode & 0o111:
            raise ValueError(f"Missing executable mode: {filename}")
    # Inspect bundled libraries and private payloads as well as public entry points.
    for path in stage.rglob("*"):
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if (
            magic[:2] == b"MZ"
            or magic == b"\x7fELF"
            or magic
            in (
                b"\xcf\xfa\xed\xfe",
                b"\xca\xfe\xba\xbe",
                b"\xbe\xba\xfe\xca",
                b"\xca\xfe\xba\xbf",
                b"\xbf\xba\xfe\xca",
            )
        ):
            kind, arch = binary_identity(path)
            if (kind, arch) != (info.binary_format, info.arch):
                raise ValueError(f"Wrong target format or architecture: {path.name}")


def runtime_audit(stage: Path, data: dict[str, Any]) -> None:
    info = TARGETS[data["target"]]
    minimum = tuple(map(int, data.get("runtime", {}).get("glibc", "2.34").split(".")))
    loader = "ld-linux-aarch64.so.1" if info.arch == "arm64" else "ld-linux-x86-64.so.2"
    allowed = {
        "libc.so.6",
        "libm.so.6",
        "libmvec.so.1",
        "libdl.so.2",
        "libpthread.so.0",
        "librt.so.1",
        "libresolv.so.2",
        "libutil.so.1",
        loader,
    }
    allowed.update(data.get("runtime", {}).get("requirements", []))
    bundled = {path.name.casefold() for path in stage.rglob("*") if path.is_file()}
    for path in stage.rglob("*"):
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic == b"\x7fELF" and info.os == "linux":
            details = run(["readelf", "--version-info", "--dynamic", "--program-headers", path], capture=True).stdout
            versions = [
                tuple(map(int, version.split("."))) for version in re.findall(r"GLIBC_(\d+\.\d+(?:\.\d+)?)", details)
            ]
            if versions and max(versions) > minimum:
                raise ValueError(f"{path.name} needs glibc {max(versions)}, configured minimum is {minimum}")
            for library in re.findall(r"Shared library: \[(.*?)\]", details):
                if library not in allowed and not list(stage.rglob(library)):
                    raise ValueError(f"Unbundled runtime dependency: {library} in {path.name}")
            for interpreter in re.findall(r"Requesting program interpreter: (.*?)\]", details):
                expected = f"/lib/{loader}" if info.arch == "arm64" else f"/lib64/{loader}"
                if interpreter != expected:
                    raise ValueError(f"Unexpected ELF loader: {interpreter}")
            if re.search(r"\((?:RUNPATH|RPATH)\).*\[(?:/(?:tmp|opt|work)|.*?/build/)", details):
                raise ValueError(f"Build directory runtime path in {path.name}")
        elif magic[:2] == b"MZ" and info.os == "windows":
            details = run(["llvm-readobj", "--coff-imports", path], capture=True).stdout
            for library in re.findall(r"Name: (\S+\.dll)", details, re.I):
                lower = library.casefold()
                if lower.startswith(
                    ("libgcc", "libstdc++", "libwinpthread", "libc++", "libunwind", "vcruntime", "msvcp", "ucrtbased")
                ):
                    if lower not in bundled:
                        raise ValueError(f"Unbundled compiler runtime: {library}")
                if data.get("build", {}).get("compiler") == "clang-msvc" and lower.startswith(
                    ("vcruntime", "msvcp", "ucrtbase", "api-ms-win-crt")
                ):
                    raise ValueError(f"Expected static release CRT: {library}")
        elif magic == b"\xcf\xfa\xed\xfe" and info.os == "macos":
            audit_macho(path)


def audit_macho(path: Path) -> None:
    binary_identity(path)
    contents = path.read_bytes()
    if len(contents) < 32:
        raise ValueError(f"Truncated Mach-O header: {path}")
    count, command_size = struct.unpack_from("<II", contents, 16)
    end = 32 + command_size
    if end > len(contents):
        raise ValueError(f"Truncated Mach-O load commands: {path}")
    cursor = 32
    minimums = []
    for _ in range(count):
        if cursor + 8 > end:
            raise ValueError(f"Truncated Mach-O load command: {path}")
        command, length = struct.unpack_from("<II", contents, cursor)
        if length < 8 or cursor + length > end:
            raise ValueError(f"Malformed Mach-O load command: {path}")
        payload = contents[cursor : cursor + length]
        if command in (0x24, 0x32):
            if len(payload) < (24 if command == 0x32 else 16):
                raise ValueError(f"Malformed deployment command: {path}")
            if command == 0x32 and struct.unpack_from("<I", payload, 8)[0] != 1:
                raise ValueError(f"Expected macOS platform: {path}")
            version = struct.unpack_from("<I", payload, 12 if command == 0x32 else 8)[0]
            minimums.append(version)
        if command in (0xC, 0x80000018, 0x8000001F, 0x20, 0x80000023, 0x8000001C):
            if len(payload) < 12:
                raise ValueError(f"Malformed dependency command: {path}")
            offset = struct.unpack_from("<I", payload, 8)[0]
            if offset < 12 or offset >= len(payload) or b"\0" not in payload[offset:]:
                raise ValueError(f"Malformed dependency path: {path}")
            dependency = payload[offset:].split(b"\0", 1)[0].decode()
            if command == 0x8000001C or not dependency.startswith(("/usr/lib/", "/System/Library/Frameworks/")):
                raise ValueError(f"Non-system macOS dependency or rpath: {dependency}")
        cursor += length
    if not minimums or any(version > 13 << 16 for version in minimums):
        raise ValueError(f"Missing or excessive macOS deployment minimum: {path}")
