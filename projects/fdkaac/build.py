# ruff: noqa: F405 F403
from meta import *
import zipfile
import json
import platform
from utils import check_version_exists

if check_version_exists("fdkaac", env["VERSION_NAME"]):
    print("Skipping fdkaac builds! (Versions already exist)")
    exit(0)

is_x86 = "x86" in platform.machine().lower() or "amd64" in platform.machine().lower()
if not is_x86:
    # Needs testing and stuff. I don't really have the time for it.
    print("ARM Builds currently unsupported.")
    exit(1)

json_metadata = json.dumps(dict(NAME="fdkaac", VERSION=env["VERSION_NAME"], VERSION_CODE=env["VERSION_CODE"]))

env.update(OPT_FLAGS="-fno-strict-aliasing -O2 -march=x86-64-v3")
env.update(PKG_CONFIG_PATH=f"{env['BUILD_DIR']}/lib/pkgconfig")

for cmd in ["chmod u+x build-linux.sh", "./build-linux.sh"]:
    run_cmd(cmd)

env.pop("PKG_CONFIG_PATH", None)
for cmd in ["chmod u+x build-win-mingw64.sh", "./build-win-mingw64.sh"]:
    run_cmd(cmd)

with zipfile.ZipFile(release_zip_linux64, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as f:
    bin = Path(env["BUILD_DIR"]) / "bin" / "fdkaac"
    f.write(bin, bin.name)

    f.writestr(".metadata.json", json_metadata)

with zipfile.ZipFile(release_zip_win64, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as f:
    bin = Path(env["BUILD_DIR_WIN64"]) / "bin" / "fdkaac.exe"
    f.write(bin, bin.name)

    f.writestr(".metadata.json", json_metadata)
