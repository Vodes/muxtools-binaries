"""Validated handoff from native execution to aggregate audio comparison."""

import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .artifacts import read_metadata
from .binary_checks import structural
from .checks import AudioCheck, CheckSuite
from .io import extract, sha256
from .models import Model, safe_path
from .recipes import checks_for_archive


class CaseResult(Model):
    case: str
    status: Literal["completed", "skipped"]
    index: int | None = None
    tier: str
    output: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class NativeResult(Model):
    schema_version: Literal[1] = 1
    archive: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: str
    target: str
    fixture_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    structure: Literal[True] = True
    cases: list[CaseResult]


class ComparisonResult(Model):
    completed: list[str]
    fixture_sha256: str | None = None


class CompletionReport(Model):
    schema_version: Literal[2] = 2
    archive: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native: NativeResult
    comparison: ComparisonResult


def revision(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def expected_cases(data: dict[str, Any], suite: CheckSuite) -> dict[str, tuple[int | None, str]]:
    cases: dict[str, tuple[int | None, str]] = {
        f"run:{name}:{tier}": (None, tier) for name, variants in data["binaries"].items() for tier in variants
    }
    cases.update(
        {
            f"functional:{index}:{tier}": (index, tier)
            for index, check in enumerate(suite.functional)
            for tier in data["binaries"][check.command]
        }
    )
    return cases


def validate_native(
    native: NativeResult,
    archive: Path,
    data: dict[str, Any],
    suite: CheckSuite,
    checkout: str,
    fixture: Path,
    directory: Path | None = None,
) -> list[CaseResult]:
    if (
        native.archive != archive.name
        or native.sha256 != sha256(archive)
        or native.target != data["target"]
        or native.revision != checkout
        or data.get("builder", {}).get("revision", checkout) != checkout
    ):
        raise ValueError("Native evidence archive, target, or revision mismatch")
    audio = any(isinstance(check, AudioCheck) for check in suite.functional)
    if native.fixture_sha256 != (sha256(fixture) if audio else None):
        raise ValueError("Native evidence fixture mismatch")
    expected = expected_cases(data, suite)
    actual = {case.case: case for case in native.cases}
    if len(actual) != len(native.cases) or actual.keys() != expected.keys():
        raise ValueError("Missing, duplicate, or unexpected native cases")
    outputs: set[str] = set()
    audio_cases = []
    for name, (index, tier) in expected.items():
        case = actual[name]
        if (case.index, case.tier) != (index, tier) or (tier == "baseline" and case.status != "completed"):
            raise ValueError(f"Invalid native case: {name}")
        if index is not None:
            command = suite.functional[index].command
            if case.status != actual[f"run:{command}:{tier}"].status:
                raise ValueError(f"Functional coverage differs from smoke coverage: {name}")
        needs_output = index is not None and case.status == "completed"
        if needs_output != (case.output is not None and case.sha256 is not None):
            raise ValueError(f"Missing or unexpected output evidence: {name}")
        if not needs_output and (case.output is not None or case.sha256 is not None):
            raise ValueError(f"Unexpected output evidence: {name}")
        if case.output is not None:
            relative = safe_path(case.output)
            expected_output = f"check-{index}/{tier}.{suite.functional[index].suffix}" if index is not None else None
            if relative in outputs or relative != case.output or relative != expected_output:
                raise ValueError("Duplicate or noncanonical output path")
            outputs.add(relative)
            if directory is not None:
                path = directory / relative
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or not path.resolve().is_relative_to(directory.resolve())
                    or not path.stat().st_size
                    or sha256(path) != case.sha256
                ):
                    raise ValueError(f"Missing or altered native output: {name}")
            if index is not None and isinstance(suite.functional[index], AudioCheck):
                audio_cases.append(case)
    return audio_cases


def compare_bundle(archive: Path, bundle: Path, root: Path, fixture: Path) -> CompletionReport:
    native = NativeResult.model_validate_json((bundle / "native.json").read_text())
    with tempfile.TemporaryDirectory(prefix="muxtools compare ") as temporary:
        stage = Path(temporary)
        extract(archive, stage, "tar.zst")
        data = read_metadata(stage)
        suite = checks_for_archive(data, root)
        structural(stage, data, suite)
        cases = validate_native(native, archive, data, suite, revision(root), fixture, bundle)
        if cases:
            from .audio_testing import compare_audio, decode_audio

            reference = decode_audio(fixture)
            for case in cases:
                assert case.index is not None and case.output is not None
                check = suite.functional[case.index]
                assert isinstance(check, AudioCheck)
                compare_audio(check, case.tier, bundle / case.output, reference)
    return CompletionReport(
        archive=archive.name,
        sha256=sha256(archive),
        native=native,
        comparison=ComparisonResult(completed=[case.case for case in cases], fixture_sha256=native.fixture_sha256),
    )


def compare_artifacts(artifacts: Path, reports: Path, root: Path, fixture: Path) -> list[Path]:
    archives = [path for path in artifacts.rglob("*.tar.zst") if path.is_file()]
    bundles = list(artifacts.rglob("native.json"))
    if not archives:
        raise ValueError("No archives for comparison")
    by_archive = {}
    for path in bundles:
        native = NativeResult.model_validate_json(path.read_text())
        if native.archive in by_archive:
            raise ValueError("Duplicate native bundle")
        by_archive[native.archive] = path.parent
    if len({a.name for a in archives}) != len(archives) or set(by_archive) != {a.name for a in archives}:
        raise ValueError("Missing, duplicate, or unmatched archives and native bundles")
    # Write completion reports only after the entire aggregate has passed.
    completed = [compare_bundle(a, by_archive[a.name], root, fixture) for a in sorted(archives)]
    reports.mkdir(parents=True, exist_ok=True)
    outputs = []
    for report in completed:
        path = reports / (report.archive + ".report.json")
        path.write_text(report.model_dump_json(indent=2) + "\n")
        outputs.append(path)
    return outputs


def validate_completion(
    report: CompletionReport, archive: Path, data: dict[str, Any], suite: CheckSuite, checkout: str, fixture: Path
) -> None:
    cases = validate_native(report.native, archive, data, suite, checkout, fixture)
    if (
        report.archive != archive.name
        or report.sha256 != report.native.sha256
        or report.comparison.fixture_sha256 != report.native.fixture_sha256
        or sorted(report.comparison.completed) != sorted(case.case for case in cases)
    ):
        raise ValueError("Missing or mismatched comparison evidence")
