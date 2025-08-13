# ruff: noqa: F405 F403
from meta import *
import zipfile
import json
from utils import check_version_exists, run_cmd

if check_version_exists("opus-tools", env["OPUS_VERSION_NAME"]) and check_version_exists("flac", env["FLAC_VERSION_NAME"]):
    print("Skipping flac and opus builds! (Versions already exist)")
    exit(0)

if "x86" in platform.machine().lower():
    env.update(OPT_FLAGS="-fno-strict-aliasing -O2 -march=x86-64-v3")


if os.name == "nt":
    run_cmd("uv run repack-windows.py")
    exit(1)

commands = ["chmod u+x build-linux.sh", "./build-linux.sh"]
for cmd in commands:
    run_cmd(cmd)


with zipfile.ZipFile(opus_zip, "w") as f:
    for bin in (Path(env["BUILD_DIR"]) / "bin").glob("*opus*"):
        f.write(bin, bin.name)

    f.writestr(".metadata.json", json.dumps(dict(NAME="opus-tools", VERSION=env["OPUS_VERSION_NAME"], VERSION_CODE=env["OPUS_VERSION_CODE"])))

with zipfile.ZipFile(flac_zip, "w") as f:
    for bin in (Path(env["BUILD_DIR"]) / "bin").glob("*flac*"):
        f.write(bin, bin.name)

    f.writestr(".metadata.json", json.dumps(dict(NAME="flac", VERSION=env["FLAC_VERSION_NAME"], VERSION_CODE=env["FLAC_VERSION_CODE"])))
