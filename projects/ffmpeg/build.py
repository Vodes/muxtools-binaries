# ruff: noqa: F405 F403
from meta import *
from utils import check_version_exists, run_cmd, download_file
import platform
import zipfile
import json

if check_version_exists("ffmpeg", env["VERSION_NAME"]):
    print("Skipping ffmpeg builds! (Versions already exist)")
    exit(0)

out_zip = out_dir / "ffmpeg" / f"ffmpeg-{env['VERSION_NAME']}-{platform.system().lower()}-{platform.machine().lower()}.zip"
json_metadata = json.dumps(dict(NAME="ffmpeg", VERSION=env["VERSION_NAME"], VERSION_CODE=env["VERSION_CODE"]))

out_zip.parent.mkdir(exist_ok=True, parents=True)

if os.name == "nt":
    downloaded = Path("ffmpeg.zip")
    download_file(WIN_X86_URL, downloaded)
    run_cmd(f'7z x "{downloaded.resolve()}"', cwd=cwd)
    with zipfile.ZipFile(out_zip, "w") as f:
        for bin in list(Path().rglob("ffmpeg.exe")) + list(Path().rglob("ffprobe.exe")):
            if not bin.is_file():
                continue
            f.write(bin, bin.name)

        f.writestr(".metadata.json", json_metadata)
else:
    downloaded = Path("ffmpeg.tar.xz")
    download_file(LINUX_X86_URL, downloaded)
    run_cmd(f'tar -xvf "{downloaded.resolve()}" --strip-components=1', cwd=cwd)
    with zipfile.ZipFile(out_zip, "w") as f:
        for bin in list(Path().rglob("ffmpeg")) + list(Path().rglob("ffprobe")):
            if not bin.is_file():
                continue
            f.write(bin, bin.name)

        f.writestr(".metadata.json", json_metadata)
