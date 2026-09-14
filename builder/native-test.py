"""Portable orchestration for downloaded native-test archives."""

import runpy
import sys
from pathlib import Path

from muxtools_binaries.testing import test_archive


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    archives = sorted((root / "dist").glob("*.tar.zst"))
    if not archives:
        raise ValueError("No native-test archives downloaded")
    for archive in archives:
        test_archive(
            archive, root=root, results=root / "native-results", audio_source=root / "tests/data/audio/wav_source.wav"
        )
    if sys.platform == "linux":
        sys.argv = ["baseline-test.py", *map(str, archives)]
        runpy.run_path(str(root / "builder/baseline-test.py"), run_name="__main__")


if __name__ == "__main__":
    main()
