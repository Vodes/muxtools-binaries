"""Launch source-built baselines on AlmaLinux 9 without builder dependencies."""

import sys
import tempfile
from pathlib import Path

from muxtools_binaries.artifacts import read_metadata
from muxtools_binaries.io import extract
from muxtools_binaries.testing import run_smoke


def main() -> None:
    for argument in sys.argv[1:]:
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            extract(Path(argument), stage, "tar.zst")
            data = read_metadata(stage)
            if data["provenance"]["type"] != "source-build":
                raise ValueError("This check is for source-built packages")
            for name, variants in data["binaries"].items():
                run_smoke(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network=none",
                        "-v",
                        f"{stage}:/package:ro",
                        "almalinux:9",
                        f"/package/{variants['baseline']}",
                        *data["smoke"][name],
                    ],
                    data["name"],
                    name,
                )


if __name__ == "__main__":
    main()
