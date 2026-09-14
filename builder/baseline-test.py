"""Launch source-built baselines on AlmaLinux 9 without builder dependencies."""

import sys
import tempfile
from pathlib import Path

from muxtools_binaries.artifacts import read_metadata
from muxtools_binaries.io import extract
from muxtools_binaries.recipes import checks_for_archive
from muxtools_binaries.testing import native_target, run_smoke


def main() -> None:
    for argument in sys.argv[1:]:
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            extract(Path(argument), stage, "tar.zst")
            data = read_metadata(stage)
            if data["provenance"]["type"] != "source-build":
                continue
            if data["target"] != native_target() or not data["target"].startswith("linux-"):
                raise ValueError("AlmaLinux baseline tests require the matching native Linux architecture")
            suite = checks_for_archive(data, Path(__file__).resolve().parents[1])
            for name, variants in data["binaries"].items():
                run_smoke(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network=none",
                        "--platform",
                        "linux/arm64" if data["target"] == "linux-arm64" else "linux/amd64",
                        "-v",
                        f"{stage}:/package:ro",
                        "almalinux:9",
                        f"/package/{variants['baseline']}",
                        *suite.smoke[name].args,
                    ],
                    suite.smoke[name],
                )


if __name__ == "__main__":
    main()
