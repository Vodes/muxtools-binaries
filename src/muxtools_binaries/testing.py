import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from cpuinfo import get_cpu_info

from .artifacts import read_metadata, write_report
from .binary_checks import binary_format, runtime_audit, structural  # noqa: F401
from .checks import AudioCheck, CheckSuite, CommandCheck
from .evidence import CaseResult, NativeResult, compare_bundle, revision
from .io import Command, extract, run, sha256
from .models import TARGETS
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


def native_target() -> str:
    system = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}.get(platform.system())
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    target = f"{system}-{arch}"
    if target not in TARGETS:
        raise ValueError(f"Unsupported native platform: {platform.system()} {platform.machine()}")
    return target


def exercise(
    stage: Path, data: dict[str, Any], suite: CheckSuite, cwd: Path, tiers: set[str], audio_source: Path
) -> list[CaseResult]:
    results = []
    for index, check in enumerate(suite.functional):
        work = cwd / f"check-{index}"
        work.mkdir()
        source = audio_source.resolve()
        if isinstance(check, AudioCheck):
            if not source.is_file():
                raise ValueError(f"Missing audio fixture: {source}. Run git submodule update --init --recursive.")
            fields: dict[str, Any] = {}
        else:
            source = work / "input.yuv"
            source.write_bytes(bytes(check.width * check.height * 3 // 2 * (1 if check.bit_depth == 8 else 2)))
            fields = dict(width=check.width, height=check.height, bit_depth=check.bit_depth)
        for tier, filename in data["binaries"][check.command].items():
            case = CaseResult(
                case=f"functional:{index}:{tier}",
                index=index,
                tier=tier,
                status="completed" if tier in tiers else "skipped",
            )
            if tier in tiers:
                output = work / f"{tier}.{check.suffix}"
                args = [arg.format(source=source, output=output, **fields) for arg in check.args]
                run([stage / filename, *args], cwd=work, timeout=120)
                if not output.is_file() or not output.stat().st_size:
                    raise ValueError(f"{check.command}:{tier} produced no {check.kind} output")
                case.output = output.relative_to(cwd).as_posix()
                case.sha256 = sha256(output)
            results.append(case)
    return results


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
    results: Path | None = None,
    audio_source: Path = Path("tests/data/audio/wav_source.wav"),
) -> list[str]:
    if results is not None and (not smoke or report is not None):
        raise ValueError("--results requires native execution and cannot write a completion report")
    archive, root = archive.resolve(), root.resolve()
    with tempfile.TemporaryDirectory(prefix="muxtools test ") as temporary:
        work = Path(temporary)
        stage = work / "package with spaces"
        extract(archive, stage, "tar.zst")
        data = read_metadata(stage)
        suite = checks_for_archive(data, root) if data["schema_version"] != 1 else None
        structural(stage, data, suite)
        checks = ["structure"]
        if not smoke:
            if report:
                write_report(archive, checks, report)
            return checks
        target = native_target()
        if target != data["target"]:
            raise ValueError("Smoke tests require the native target runner")
        suite = suite or checks_for_archive(data, root)
        checkout = revision(root)
        if data.get("builder", {}).get("revision", checkout) != checkout:
            raise ValueError("Use the checkout recorded in builder.revision")
        features, xcr0 = cpu_state() if TARGETS[target].arch == "x86_64" else (set(), 0)
        tiers = {
            tier
            for variants in data["binaries"].values()
            for tier in variants
            if tier == "baseline" or supports(tier, features, xcr0)
        }
        bundle = results.resolve() / archive.name if results else work / "native results"
        bundle.mkdir(parents=True, exist_ok=False)
        cases = []
        for executable, variants in data["binaries"].items():
            for tier, filename in variants.items():
                completed = tier in tiers
                if completed:
                    run_smoke([stage / filename, *suite.smoke[executable].args], suite.smoke[executable], cwd=bundle)
                checks.append(f"{'run' if completed else 'skip'}:{executable}:{tier}")
                cases.append(
                    CaseResult(
                        case=f"run:{executable}:{tier}", tier=tier, status="completed" if completed else "skipped"
                    )
                )
        cases.extend(exercise(stage, data, suite, bundle, tiers, audio_source))
        checks.append("smoke")
        native = NativeResult(
            archive=archive.name,
            sha256=sha256(archive),
            revision=checkout,
            target=target,
            cases=cases,
            fixture_sha256=sha256(audio_source) if any(isinstance(c, AudioCheck) for c in suite.functional) else None,
        )
        (bundle / "native.json").write_text(native.model_dump_json(indent=2) + "\n")
        if results is None:
            completion = compare_bundle(archive, bundle, root, audio_source)
            if report:
                report.write_text(completion.model_dump_json(indent=2) + "\n")
        return checks
