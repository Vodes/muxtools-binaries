from pathlib import Path
import os
import platform
from utils import run_cmd as rncmd

cwd = Path(__file__).parent

meta_env = dict(
    # Git branches
    TAG_OGG="v1.3.6",
    TAG_OPUS="v1.6",
    TAG_OPUSFILE="v0.12",
    TAG_LIBOPUSENC="v0.3",
    TAG_FLAC="1.5.0",
    TAG_OPUSTOOLS="v0.2",
    # Build env
    BUILD_DIR="/tmp/opus-build",
    PKG_CONFIG_PATH="/tmp/opus-build/lib/pkgconfig",
    OPT_FLAGS="-fno-strict-aliasing -O2",
    # Release
    OPUS_VERSION_NAME="0.2-libopus-1.6",
    OPUS_VERSION_CODE="1",
    FLAC_VERSION_NAME="1.5.0",
    FLAC_VERSION_CODE="0",
)

env = os.environ.copy()
env.update(**meta_env)

if os.name == "nt":
    out_dir = Path(R"C:\tmp\builds")
else:
    out_dir = Path("/tmp/builds")
out_dir.mkdir(exist_ok=True, parents=True)
opus_zip = out_dir / "opus-tools" / f"opus-tools-{env['OPUS_VERSION_NAME']}-{platform.system().lower()}-{platform.machine().lower()}.zip"
opus_zip.parent.mkdir(exist_ok=True, parents=True)

flac_zip = out_dir / "flac" / f"flac-{env['FLAC_VERSION_NAME']}-{platform.system().lower()}-{platform.machine().lower()}.zip"
flac_zip.parent.mkdir(exist_ok=True, parents=True)


def run_cmd(cmd: str, check: bool = True):
    rncmd(cmd, check, env, cwd)
