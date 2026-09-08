from pathlib import Path

import av
import numpy as np
import pytest

from muxtools_binaries.audio_testing import (
    AUDIO_ENCODERS,
    MIN_MOS,
    SAMPLE_RATE,
    AudioEncoder,
    audio_quality,
    decode_audio,
    exercise_audio,
)

SOURCE = Path(__file__).parent / "data/audio/wav_source.wav"


@pytest.fixture(
    params=AUDIO_ENCODERS, ids=lambda encoder: f"{encoder.package}-{'lossless' if encoder.lossless else 'lossy'}"
)
def encoder(request) -> AudioEncoder:
    return request.param


@pytest.fixture(scope="module")
def reference():
    return decode_audio(SOURCE)


@pytest.mark.parametrize(
    "encoder", AUDIO_ENCODERS, ids=lambda encoder: f"{encoder.package}-{'lossless' if encoder.lossless else 'lossy'}"
)
def test_audio_quality_roundtrip(reference, tmp_path, encoder: AudioEncoder):
    """Exercise real decoding and scoring with PyAV's default encoder for each output format."""
    output = tmp_path / f"encoded.{encoder.suffix}"
    with av.open(str(SOURCE)) as source, av.open(str(output), "w") as encoded:
        stream = encoded.add_stream(encoded.default_audio_codec, rate=SAMPLE_RATE)
        assert isinstance(stream, av.AudioStream)
        stream.layout = "stereo"
        formats = ("s32", "s32p") if encoder.lossless else ("s16", "s16p", "flt", "fltp")
        stream.codec_context.format = next(
            format.name for format in stream.codec_context.codec.audio_formats or [] if format.name in formats
        )
        if not encoder.lossless:
            stream.bit_rate = 128000
        for frame in source.decode(audio=0):
            encoded.mux(stream.encode(frame))
        encoded.mux(stream.encode(None))
    scores = audio_quality(reference, decode_audio(output))
    assert len(scores) == 2
    assert min(scores) >= MIN_MOS
    if encoder.lossless:
        assert scores == pytest.approx([5.0, 5.0])


@pytest.mark.parametrize("damage", ["silence", "silent-channel", "noise", "truncated", "mono", "nan"])
def test_audio_quality_rejects_damage(reference, damage):
    broken = reference.copy()
    expected = "silent or non-finite"
    if damage == "silence":
        broken[:] = 0
    elif damage == "silent-channel":
        broken[1] = 0
    elif damage == "noise":
        broken[:] = np.random.default_rng(0).uniform(-0.5, 0.5, broken.shape)
        expected = "Zimtohrli MOS"
    elif damage == "truncated":
        broken = broken[:, : SAMPLE_RATE * 10]
        expected = "duration"
    elif damage == "mono":
        broken = broken[:1]
        expected = "channel count"
    else:
        broken[1, 100] = np.nan
    with pytest.raises(ValueError, match=expected):
        audio_quality(reference, broken)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), MIN_MOS - 0.01])
def test_audio_quality_rejects_bad_score(reference, monkeypatch, score):
    monkeypatch.setattr("muxtools_binaries.audio_testing.mos_from_signals", lambda *_: score)
    with pytest.raises(ValueError, match="Zimtohrli MOS"):
        audio_quality(reference, reference)


def test_decode_invalid_audio(tmp_path):
    path = tmp_path / "invalid.m4a"
    path.write_bytes(b"not an audio file")
    with pytest.raises(ValueError, match="Cannot decode audio"):
        decode_audio(path)


def test_missing_audio_fixture(tmp_path, encoder):
    with pytest.raises(ValueError, match="git submodule update --init"):
        exercise_audio(tmp_path, encoder, {}, tmp_path, {"baseline"}, tmp_path / "missing.wav")


def test_encoder_uses_fixture_and_checks_output(tmp_path, monkeypatch, encoder):
    """A successful encoder exit must not hide corrupt output; unsupported tiers stay unused."""
    stage = tmp_path / "package with spaces"
    cwd = tmp_path / "unrelated directory"
    cwd.mkdir()
    variants = {"baseline": encoder.command, "avx2": f"{encoder.command}-avx2"}
    calls = []

    def encode(args, **kwargs):
        calls.append(args)
        assert args[0] == stage / encoder.command
        assert str(SOURCE.resolve()) in args
        assert kwargs["cwd"] == cwd
        output = Path(args[args.index("-o") + 1]) if "-o" in args else Path(args[-1])
        output.write_bytes(b"corrupt output")

    monkeypatch.setattr("muxtools_binaries.audio_testing.run", encode)
    with pytest.raises(ValueError, match=f"{encoder.command}:baseline:"):
        exercise_audio(stage, encoder, variants, cwd, {"baseline"}, SOURCE)
    assert len(calls) == 1


@pytest.mark.parametrize("create_empty_file", [False, True])
def test_encoder_must_produce_output(tmp_path, monkeypatch, create_empty_file, encoder):
    variants = {"baseline": encoder.command}

    def encode(args, **kwargs):
        if create_empty_file:
            output = Path(args[args.index("-o") + 1]) if "-o" in args else Path(args[-1])
            output.touch()

    monkeypatch.setattr("muxtools_binaries.audio_testing.run", encode)
    with pytest.raises(ValueError, match="produced no audio output"):
        exercise_audio(tmp_path, encoder, variants, tmp_path, {"baseline"}, SOURCE)
