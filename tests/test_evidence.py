import json
import shutil
from pathlib import Path

import pytest

from muxtools_binaries.artifacts import pack
from muxtools_binaries.checks import AudioCheck, CheckSuite, CommandCheck
from muxtools_binaries.evidence import CompletionReport, compare_artifacts, validate_completion
from muxtools_binaries.testing import test_archive as check_archive

FIXTURE = Path(__file__).parent / "data/audio/wav_source.wav"


@pytest.fixture
def deferred(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    script = stage / "example"
    script.write_text('#!/bin/sh\nif [ "$1" = --version ]; then echo example; else cp "$1" "$2"; fi\n')
    script.chmod(0o755)
    suite = CheckSuite(
        smoke={"example": CommandCheck(args=["--version"])},
        functional=[AudioCheck(command="example", suffix="wav", args=["{source}", "{output}"])],
        files={"example": "script", "example.avx2": "script"},
    )
    shutil.copy2(script, stage / "example.avx2")
    data = dict(
        schema_version=2,
        name="example",
        version="1",
        version_code=1,
        target="linux-x86_64",
        binaries={"example": {"baseline": "example", "avx2": "example.avx2"}},
        smoke={"example": ["--version"]},
        checks=suite.model_dump(),
        provenance={"channel": "test"},
    )
    artifacts = tmp_path / "artifacts"
    archive = pack(stage, data, artifacts)
    monkeypatch.setattr("muxtools_binaries.testing.cpu_state", lambda: (set(), 0))
    monkeypatch.setattr("muxtools_binaries.testing.native_target", lambda: "linux-x86_64")
    monkeypatch.setattr("muxtools_binaries.testing.revision", lambda _: "fixture-revision")
    monkeypatch.setattr("muxtools_binaries.evidence.revision", lambda _: "fixture-revision")
    check_archive(archive, results=artifacts / "native", audio_source=FIXTURE)
    bundle = artifacts / "native" / archive.name
    return artifacts, archive, bundle, data, suite


def test_deferred_and_combined_equivalence(deferred, tmp_path):
    artifacts, archive, _, data, suite = deferred
    reports = compare_artifacts(artifacts, tmp_path / "reports", Path.cwd(), FIXTURE)
    deferred_report = CompletionReport.model_validate_json(reports[0].read_text())
    combined = tmp_path / "combined.json"
    check_archive(archive, report=combined, audio_source=FIXTURE)
    assert CompletionReport.model_validate_json(combined.read_text()) == deferred_report
    validate_completion(deferred_report, archive, data, suite, "fixture-revision", FIXTURE)
    deferred_report.comparison.completed.clear()
    with pytest.raises(ValueError, match="comparison evidence"):
        validate_completion(deferred_report, archive, data, suite, "fixture-revision", FIXTURE)


@pytest.mark.parametrize(
    "damage",
    [
        "archive",
        "target",
        "revision",
        "fixture",
        "missing-case",
        "duplicate-case",
        "baseline-skip",
        "index",
        "output-path",
        "output-hash",
        "output-missing",
        "duplicate-bundle",
        "missing-bundle",
    ],
)
def test_rejects_invalid_native_handoff(deferred, tmp_path, damage):
    artifacts, archive, bundle, _, _ = deferred
    path = bundle / "native.json"
    native = json.loads(path.read_text())
    functional = native["cases"][2]
    if damage in ("archive", "target", "revision", "fixture"):
        key = {"archive": "sha256", "fixture": "fixture_sha256"}.get(damage, damage)
        native[key] = "0" * 64
    elif damage == "missing-case":
        native["cases"].pop()
    elif damage == "duplicate-case":
        native["cases"].append(native["cases"][0])
    elif damage == "baseline-skip":
        native["cases"][0]["status"] = "skipped"
    elif damage == "index":
        functional["index"] = 55
    elif damage == "output-path":
        functional["output"] = "../outside.wav"
    elif damage == "output-hash":
        functional["sha256"] = "0" * 64
    elif damage == "output-missing":
        (bundle / functional["output"]).unlink()
    elif damage == "duplicate-bundle":
        shutil.copytree(bundle, artifacts / "duplicate")
    elif damage == "missing-bundle":
        path.unlink()
    if damage != "missing-bundle":
        path.write_text(json.dumps(native))
    with pytest.raises(ValueError):
        compare_artifacts(artifacts, tmp_path / "reports", Path.cwd(), FIXTURE)
    assert not (tmp_path / "reports").exists()


def test_corrupt_media_cannot_complete(deferred, tmp_path):
    from muxtools_binaries.io import sha256

    artifacts, _, bundle, _, _ = deferred
    path = bundle / "native.json"
    native = json.loads(path.read_text())
    case = native["cases"][2]
    output = bundle / case["output"]
    output.write_bytes(b"not audio, despite a successful native encoder exit")
    case["sha256"] = sha256(output)
    path.write_text(json.dumps(native))
    with pytest.raises(ValueError, match="Cannot decode audio"):
        compare_artifacts(artifacts, tmp_path / "reports", Path.cwd(), FIXTURE)


def test_arm64_native_execution_bypasses_x86_probe(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    path = stage / "example"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    suite = CheckSuite(smoke={"example": CommandCheck()}, files={"example": "script"})
    data = dict(
        schema_version=2,
        name="example",
        version="1",
        version_code=1,
        target="linux-arm64",
        binaries={"example": {"baseline": "example"}},
        smoke={"example": []},
        checks=suite.model_dump(),
        provenance={"channel": "test"},
    )
    archive = pack(stage, data, tmp_path / "dist")
    monkeypatch.setattr("muxtools_binaries.testing.native_target", lambda: "linux-arm64")

    def probe():
        raise AssertionError("ARM64 must not probe x86 vector state")

    monkeypatch.setattr("muxtools_binaries.testing.cpu_state", probe)
    assert "run:example:baseline" in check_archive(archive, results=tmp_path / "native")
    monkeypatch.setattr("muxtools_binaries.testing.native_target", lambda: "linux-x86_64")
    with pytest.raises(ValueError, match="native target runner"):
        check_archive(archive, results=tmp_path / "wrong")
