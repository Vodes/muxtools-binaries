from pathlib import Path
import os
from utils import run_cmd as rncmd

cwd = Path(__file__).parent

meta_env = dict(
    # Git and msys versioning
    TAG_LIBFDK="v2.0.3",
    TAG_FDKAAC="v1.0.6",
    LIBFDK_MSYS_FILE="mingw-w64-x86_64-fdk-aac-2.0.3-1-any.pkg.tar.zst",
    # Build env
    BUILD_DIR="/tmp/fdkaac-build",
    BUILD_DIR_WIN64="/tmp/fdkaac-build-win64",
    OPT_FLAGS="-fno-strict-aliasing -O2",
    # Release
    VERSION_NAME="1.0.6-libfdk-2.0.3",
    VERSION_CODE="0",
)

env = os.environ.copy()
env.update(**meta_env)

out_dir = Path("/tmp/builds")
out_dir.mkdir(exist_ok=True, parents=True)

release_zip_win64 = out_dir / "fdkaac" / f"fdkaac-{env['VERSION_NAME']}-windows-amd64.zip"
release_zip_win64.parent.mkdir(exist_ok=True, parents=True)

release_zip_linux64 = out_dir / "fdkaac" / f"fdkaac-{env['VERSION_NAME']}-linux-x86_64.zip"


def run_cmd(cmd: str, check: bool = True):
    rncmd(cmd, check, env, cwd)
