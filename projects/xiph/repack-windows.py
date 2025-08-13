# ruff: noqa: F405 F403
from meta import *
import requests
import zipfile
import json
from utils import run_cmd

OPUS_URL = "https://www.rarewares.org/files/opus/opus-tools%200.2-34-g98f3ddc-x64.zip"
FLAC_URL = "https://www.rarewares.org/files/lossless/flac-1.5.0-AVX2.zip"

Path(R"C:\tmp\dl").mkdir(exist_ok=True, parents=True)

with requests.get(OPUS_URL, stream=True) as response:
    response.raise_for_status()
    opus_file = Path(R"C:\tmp\dl\opus.zip")
    with open(opus_file, "wb") as out:
        for part in response.iter_content(1024 * 1024):
            out.write(part)
    run_cmd(f'7z x "{opus_file.resolve()}"')
    with zipfile.ZipFile(opus_zip, "w") as f:
        for bin in Path().rglob("*opus*.exe"):
            f.write(bin, bin.name)

        f.writestr(".metadata.json", json.dumps(dict(NAME="opus-tools", VERSION=env["OPUS_VERSION_NAME"], VERSION_CODE=env["OPUS_VERSION_CODE"])))

with requests.get(FLAC_URL, stream=True) as response:
    response.raise_for_status()
    flac_file = Path(R"C:\tmp\dl\flac.zip")
    with open(flac_file, "wb") as out:
        for part in response.iter_content(1024 * 1024):
            out.write(part)
    run_cmd(f'7z x "{flac_file.resolve()}"')
    with zipfile.ZipFile(flac_zip, "w") as f:
        for bin in Path().rglob("*flac*.exe"):
            f.write(bin, bin.name)

        f.writestr(".metadata.json", json.dumps(dict(NAME="flac", VERSION=env["FLAC_VERSION_NAME"], VERSION_CODE=env["FLAC_VERSION_CODE"])))
