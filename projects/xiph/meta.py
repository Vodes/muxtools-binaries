from pathlib import Path
import os
import sys
import traceback
import subprocess

cwd = Path(__file__).parent

env = os.environ.copy()
env.update(
    # Git branches
    TAG_OGG="v1.3.6",
    TAG_OPUS="v1.5.2",
    TAG_OPUSFILE="v0.12",
    TAG_LIBOPUSENC="v0.2.1",
    TAG_FLAC="1.5.0",
    TAG_OPUSTOOLS="v0.2",
    # Build env
    BUILD_DIR="/tmp/opus-build",
    PKG_CONFIG_PATH="/tmp/opus-build/lib/pkgconfig",
    OPT_FLAGS="-fno-strict-aliasing -O2",
    # Release
    OPUS_VERSION_NAME="0.2-libopus-1.5.2",
    OPUS_VERSION_CODE="0",
    FLAC_VERSION_NAME="1.5.0",
    FLAC_VERSION_CODE="0",
)


def run(cmd: str, check: bool = True):
    try:
        subprocess.run(cmd, shell=True, check=check, encoding="utf-8", env=env, cwd=cwd)
    except:
        traceback.print_exc()
        sys.exit(1)
