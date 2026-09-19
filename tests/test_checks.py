import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from muxtools_binaries.artifacts import pack
from muxtools_binaries.checks import AudioCheck, CheckSuite, CommandCheck, VideoCheck
from muxtools_binaries.testing import exercise, run_smoke, structural
from muxtools_binaries.testing import test_archive as check_archive


@pytest.mark.parametrize("code", [0, 1])
def test_command_checks_enforce_exit_and_output(code):
    command = [sys.executable, "-c", f"print('example version 1'); raise SystemExit({code})"]
    run_smoke(command, CommandCheck(exit_codes=[code], stdout_prefix="example ", stdout_contains=["version"]))
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        run_smoke(command, CommandCheck(exit_codes=[1 - code]))
    with pytest.raises(ValueError, match="Unexpected smoke output"):
        run_smoke(command, CommandCheck(exit_codes=[code], stdout_prefix="other"))
    with pytest.raises(ValueError, match="Unexpected smoke output"):
        run_smoke(command, CommandCheck(exit_codes=[code], stdout_contains=["missing"]))


@pytest.fixture
def archive_data():
    suite = CheckSuite(
        smoke={"example": CommandCheck(args=["--version"], stdout_prefix="example ")},
        functional=[VideoCheck(command="example", suffix="bin", args=["--encode", "{source}", "{output}"])],
        files={"example": "script"},
    )
    return dict(
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX wrapper fixture")
def test_archive_executes_checks_and_skips_unsupported_variants(tmp_path, monkeypatch, archive_data):
    stage = tmp_path / "stage"
    stage.mkdir()
    binary = stage / "example"
    binary.write_text(
        '#!/bin/sh\nif [ "$1" = --version ]; then echo "example version 1"; else printf encoded > "$3"; fi\n'
    )
    binary.chmod(0o755)
    optimized = stage / "example.avx2"
    optimized.write_text("#!/bin/sh\nexit 99\n")
    optimized.chmod(0o755)
    archive_data["checks"]["files"]["example.avx2"] = "script"
    archive = pack(stage, archive_data, tmp_path / "dist")
    monkeypatch.setattr("muxtools_binaries.testing.cpu_state", set)
    report = check_archive(archive)
    assert {"structure", "smoke", "run:example:baseline", "skip:example:avx2"} <= set(report)


def test_audio_check_selects_flac_fixture(tmp_path, monkeypatch):
    fixture = tmp_path / "fixtures/wav_source.wav"
    fixture.parent.mkdir()
    fixture.touch()
    observed = []
    monkeypatch.setattr("muxtools_binaries.testing.exercise_audio", lambda *args: observed.append(args[-1]))
    suite = CheckSuite(
        smoke={"example": CommandCheck()},
        functional=[
            AudioCheck(
                command="example", source="flac", suffix="mka", args=["-o", "{output}", "{source}"], lossless=True
            )
        ],
    )
    work = tmp_path / "work"
    work.mkdir()
    exercise(
        tmp_path,
        {"binaries": {"example": {"baseline": "example"}}},
        suite,
        work,
        {"baseline"},
        fixture,
    )
    assert observed == [fixture.with_name("flac_source.flac")]


def test_structural_rejects_recipe_forbidden_library(tmp_path, monkeypatch):
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    suite = CheckSuite(
        smoke={"example": CommandCheck()},
        forbidden_libraries=["Qt6Core.dll", "libFLAC*.dll"],
    )
    monkeypatch.setattr("muxtools_binaries.testing.binary_format", lambda path: "pe")
    monkeypatch.setattr(
        "muxtools_binaries.testing.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="DLL Name: Qt6Core.dll\n"),
    )
    with pytest.raises(ValueError, match="Forbidden runtime dependency: Qt6Core.dll"):
        structural(
            tmp_path,
            {
                "target": "windows-x86_64",
                "binaries": {"example": {"baseline": executable.name}},
            },
            suite,
        )
