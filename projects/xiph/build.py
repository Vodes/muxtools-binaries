import os
import subprocess
import platform
import pathlib

cwd = pathlib.Path(__file__).parent

env = os.environ.copy()
env.update(
    TAG_OGG="v1.3.6",
    TAG_OPUS="v1.5.2",
    TAG_OPUSFILE="v0.12",
    TAG_LIBOPUSENC="v0.2.1",
    TAG_FLAC="1.5.0",
    TAG_OPUSTOOLS="v0.2",
    BUILD_DIR="/tmp/opus-build",
    PKG_CONFIG_PATH="/tmp/opus-build/lib/pkgconfig",
    OPT_FLAGS="-fno-strict-aliasing -O2",
)

if "x86" in platform.machine().lower():
    env.update(OPT_FLAGS="-fno-strict-aliasing -O2 -march=x86-64-v3")


commands = ["chmod u+x build-linux.sh", "./build-linux.sh"]
for cmd in commands:
    subprocess.run(cmd, shell=True, check=True, encoding="utf-8", env=env, cwd=cwd)
