import os
import platform
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

from cpuinfo import get_cpu_info

from .artifacts import read_metadata, write_report
from .audio_testing import exercise_audio
from .checks import AudioCheck, CheckSuite, CommandCheck
from .io import Command, extract, run
from .recipes import checks_for_archive


def supports(tier: str, features: set[str], xcr0: int) -> bool:
    v2 = {"cx16", "lahf_lm", "popcnt", "sse3", "ssse3", "sse4_1", "sse4_2"}
    v3 = v2 | {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "lzcnt", "movbe", "xsave"}
    v4 = v3 | {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"}
    # znver4 enables more than v4; SSE4a and AVX512BF16 are deliberately disabled.
    zn4 = v4 | {
        "aes",
        "pclmul",
        "rdrand",
        "rdseed",
        "adx",
        "sha",
        "clflushopt",
        "clwb",
        "fsgsbase",
        "avx512ifma",
        "avx512vbmi",
        "avx512vbmi2",
        "avx512vnni",
        "avx512bitalg",
        "avx512vpopcntdq",
        "gfni",
        "vaes",
        "vpclmulqdq",
        "rdpid",
        "wbnoinvd",
        "clzero",
        "prfchw",
        "mwaitx",
    }
    requirements = {"baseline": v2, "avx2": v3, "avx512": v4, "zn4": zn4}
    return (
        requirements[tier] <= features
        and (tier == "baseline" or xcr0 & 6 == 6)
        and (tier not in ("avx512", "zn4") or xcr0 & 0xE6 == 0xE6)
    )


def cpu_state() -> tuple[set[str], int]:
    aliases = {
        "pni": "sse3",
        "abm": "lzcnt",
        "pclmulqdq": "pclmul",
        "rdrnd": "rdrand",
        "sha_ni": "sha",
        "3dnowprefetch": "prfchw",
    }
    try:
        features = {aliases.get(flag, flag).replace("avx512_", "avx512") for flag in get_cpu_info().get("flags", [])}
        # cpuinfo merges CPUID and OS flags; hardware support alone cannot establish OS vector support.
        if sys.platform == "win32":
            import ctypes

            enabled = ctypes.WinDLL("kernel32").GetEnabledXStateFeatures
            enabled.restype = ctypes.c_uint64
            enabled.argtypes = []
            return features, enabled()
        if platform.system() == "Linux":
            flags = re.findall(r"^flags\s*:\s*(.*)$", Path("/proc/cpuinfo").read_text(), re.MULTILINE)
            enabled = set.intersection(*(set(line.split()) for line in flags)) if flags else set()
            return features, (6 if "avx" in enabled else 0) | (0xE0 if "avx512f" in enabled else 0)
    except Exception as error:
        print(f"CPU detection unavailable; skipping optimized variants: {error}")
    return set(), 0


def binary_format(path: Path) -> Literal["elf", "pe", "script"]:
    with path.open("rb") as stream:
        header = stream.read(64)
        if header.startswith(b"\x7fELF"):
            if len(header) < 20 or header[4:6] != b"\x02\x01" or struct.unpack_from("<H", header, 18)[0] != 62:
                raise ValueError(f"Expected ELF x86-64: {path}")
            return "elf"
        if header.startswith(b"MZ"):
            if len(header) < 64:
                raise ValueError(f"Truncated PE: {path}")
            stream.seek(struct.unpack_from("<I", header, 60)[0])
            pe = stream.read(6)
            if pe != b"PE\0\0d\x86":
                raise ValueError(f"Expected PE x86-64: {path}")
            return "pe"
        if header.startswith(b"#!/bin/sh\n"):
            return "script"
    raise ValueError(f"Unknown executable format: {path}")


def structural(stage: Path, data: dict[str, Any], suite: CheckSuite | None = None) -> None:
    expected = "pe" if data["target"].startswith("windows") else "elf"
    files = suite.files if suite else {}
    for variants in data["binaries"].values():
        for filename in variants.values():
            kind = binary_format(stage / filename)
            if kind != files.get(filename, expected):
                raise ValueError(f"Wrong target format: {filename}")
    for filename, expected_format in files.items():
        if binary_format(stage / filename) != expected_format:
            raise ValueError(f"Wrong required file format: {filename}")
    if os.name == "nt":
        return
    minimum = tuple(map(int, data.get("runtime", {}).get("glibc", "2.34").split(".")))
    allowed = {
        "libc.so.6",
        "libm.so.6",
        "libmvec.so.1",
        "libdl.so.2",
        "libpthread.so.0",
        "librt.so.1",
        "libresolv.so.2",
        "ld-linux-x86-64.so.2",
        "libutil.so.1",
    }
    allowed.update(data.get("runtime", {}).get("requirements", []))
    for path in stage.rglob("*"):
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic == b"\x7fELF":
            binary_format(path)
            details = run(["readelf", "--version-info", "--dynamic", path], capture=True).stdout
            versions = [
                tuple(map(int, version.split("."))) for version in re.findall(r"GLIBC_(\d+\.\d+(?:\.\d+)?)", details)
            ]
            if versions and max(versions) > minimum:
                raise ValueError(f"{path.name} needs glibc {max(versions)}, configured minimum is {minimum}")
            for library in re.findall(r"Shared library: \[(.*?)\]", details):
                if library not in allowed and not list(stage.rglob(library)):
                    raise ValueError(f"Unbundled runtime dependency: {library} in {path.name}")
            if re.search(r"\((?:RUNPATH|RPATH)\).*\[(?:/(?:tmp|opt|work)|.*?/build/)", details):
                raise ValueError(f"Build directory runtime path in {path.name}")
        elif magic[:2] == b"MZ":
            binary_format(path)
            details = run(["objdump", "-p", path], capture=True).stdout
            for library in re.findall(r"DLL Name: (\S+)", details):
                if library.lower().startswith(("libgcc", "libstdc++", "libwinpthread")):
                    if not any(p.name.lower() == library.lower() for p in stage.rglob("*.dll")):
                        raise ValueError(f"Unbundled compiler runtime: {library}")


def exercise(
    stage: Path, data: dict[str, Any], suite: CheckSuite, cwd: Path, tiers: set[str], audio_source: Path
) -> None:
    for index, check in enumerate(suite.functional):
        work = cwd / f"check-{index}"
        work.mkdir()
        variants = data["binaries"][check.command]
        if isinstance(check, AudioCheck):
            exercise_audio(stage, check, variants, work, tiers, audio_source)
            continue
        source = work / "input.yuv"
        source.write_bytes(bytes(check.width * check.height * 3 // 2 * (1 if check.bit_depth == 8 else 2)))
        for tier, filename in variants.items():
            if tier not in tiers:
                continue
            output = work / f"{tier}.{check.suffix}"
            args = [
                arg.format(
                    source=source, output=output, width=check.width, height=check.height, bit_depth=check.bit_depth
                )
                for arg in check.args
            ]
            run([stage / filename, *args], cwd=work, timeout=120)
            if not output.is_file() or not output.stat().st_size:
                raise ValueError(f"{check.command}:{tier} produced no video output")


def run_smoke(command: Command, check: CommandCheck, *, cwd: Path | None = None) -> None:
    try:
        result = run(command, cwd=cwd, timeout=check.timeout, capture=True)
        code, stdout = result.returncode, result.stdout
    except subprocess.CalledProcessError as error:
        code, stdout = error.returncode, error.stdout or ""
        if code not in check.exit_codes:
            raise
    if code not in check.exit_codes:
        raise ValueError(f"Unexpected exit code {code}: {command}")
    if not stdout.startswith(check.stdout_prefix) or any(text not in stdout for text in check.stdout_contains):
        raise ValueError(f"Unexpected smoke output for {command}: {stdout}")


def test_archive(
    archive: Path,
    *,
    root: Path = Path("."),
    smoke: bool = True,
    report: Path | None = None,
    audio_source: Path = Path("tests/data/audio/wav_source.wav"),
) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="muxtools test ") as temporary:
        work = Path(temporary)
        stage = work / "package with spaces"
        extract(archive, stage, "tar.zst")
        data = read_metadata(stage)
        suite = checks_for_archive(data, root) if data["schema_version"] != 1 else None
        structural(stage, data, suite)
        checks = ["structure"]
        if smoke:
            target_os = "windows" if os.name == "nt" else "linux"
            if not data["target"].startswith(target_os) or platform.machine().lower() not in ("amd64", "x86_64"):
                raise ValueError("Smoke tests require the native target runner")
            suite = suite or checks_for_archive(data, root)
            features, xcr0 = cpu_state()
            tested = {"baseline"}
            cwd = work / "unrelated directory"
            cwd.mkdir()
            for executable, variants in data["binaries"].items():
                for tier, filename in variants.items():
                    if tier != "baseline" and not supports(tier, features, xcr0):
                        checks.append(f"skip:{executable}:{tier}")
                        continue
                    run_smoke(
                        [stage / filename, *suite.smoke[executable].args],
                        suite.smoke[executable],
                        cwd=cwd,
                    )
                    tested.add(tier)
                    checks.append(f"run:{executable}:{tier}")
            exercise(stage, data, suite, cwd, tested, audio_source)
            checks.append("smoke")
        if report:
            write_report(archive, checks, report)
        return checks
