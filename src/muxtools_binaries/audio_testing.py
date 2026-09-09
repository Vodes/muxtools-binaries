"""Encode the real audio fixture and check the decoded signal for gross damage."""

import math
from pathlib import Path

import av
import numpy as np
from numpy.typing import NDArray
from zimtohrli import mos_from_signals

from .checks import AudioCheck
from .io import run

SAMPLE_RATE = 48000
# This is a corruption check, not a codec quality benchmark.
MIN_MOS = 4.5
# Allow codec delay/padding, but reject truncated or stretched audio.
MAX_LENGTH_DIFFERENCE = SAMPLE_RATE // 10


def decode_audio(path: Path) -> NDArray[np.float32]:
    """Decode to planar float samples at Zimtohrli's required 48 kHz."""
    try:
        with av.open(str(path)) as container:
            if len(container.streams.audio) != 1:
                raise ValueError(f"Expected one audio stream in {path}")
            stream = container.streams.audio[0]
            if stream.codec_context.sample_rate != SAMPLE_RATE:
                raise ValueError(f"Expected {SAMPLE_RATE} Hz audio in {path}")
            resampler = av.AudioResampler(format="fltp", rate=SAMPLE_RATE)
            chunks = [
                converted.to_ndarray() for frame in container.decode(stream) for converted in resampler.resample(frame)
            ]
            chunks.extend(frame.to_ndarray() for frame in resampler.resample(None))
    except av.FFmpegError as error:
        raise ValueError(f"Cannot decode audio in {path}: {error}") from error
    if not chunks:
        raise ValueError(f"No decoded audio in {path}")
    samples = np.asarray(np.concatenate(chunks, axis=1), dtype=np.float32)
    if samples.shape[1] == 0 or not np.isfinite(samples).all():
        raise ValueError(f"Empty or non-finite audio in {path}")
    return samples


def audio_quality(reference: NDArray[np.float32], decoded: NDArray[np.float32]) -> list[float]:
    """Check duration and each channel independently, preserving signal levels."""
    if decoded.shape[0] != reference.shape[0]:
        raise ValueError("Encoded audio changed the channel count")
    if abs(decoded.shape[1] - reference.shape[1]) > MAX_LENGTH_DIFFERENCE:
        raise ValueError("Encoded audio changed the duration by more than 100 ms")
    scores = []
    for channel, (source, result) in enumerate(zip(reference, decoded, strict=True), start=1):
        if not np.isfinite(result).all() or not np.any(result):
            raise ValueError(f"Audio channel {channel} is silent or non-finite")
        # Zimtohrli aligns small timing differences internally. Do not trim or normalize damage away.
        score = float(mos_from_signals(np.ascontiguousarray(source), np.ascontiguousarray(result)))
        if not math.isfinite(score) or score < MIN_MOS:
            raise ValueError(f"Audio channel {channel}: Zimtohrli MOS {score:.3f} is below {MIN_MOS}")
        scores.append(score)
    return scores


def exercise_audio(
    stage: Path, encoder: AudioCheck, variants: dict[str, str], cwd: Path, tiers: set[str], source: Path
) -> None:
    source = source.resolve()
    if not source.is_file():
        raise ValueError(f"Missing audio fixture: {source}. Run git submodule update --init --recursive.")
    reference = decode_audio(source)
    mode = "lossless" if encoder.lossless else "lossy"
    for tier, filename in variants.items():
        if tier not in tiers:
            continue
        label = f"{encoder.command}:{tier}:{mode}"
        output = cwd / f"{encoder.command}-{tier}-{mode}.{encoder.suffix}"
        args = [arg.format(source=source, output=output) for arg in encoder.args]
        run([stage / filename, *args], cwd=cwd, timeout=120)
        if not output.is_file() or not output.stat().st_size:
            raise ValueError(f"{label} produced no audio output")
        try:
            scores = audio_quality(reference, decode_audio(output))
        except ValueError as error:
            raise ValueError(f"{label}: {error}") from error
        print(f"{label}: Zimtohrli MOS per channel: {', '.join(f'{score:.3f}' for score in scores)}")
