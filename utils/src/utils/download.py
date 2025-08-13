import requests
from pathlib import Path


def download_file(url: str, out_path: str | Path):
    with requests.get(url, stream=True) as response:
        response.raise_for_status()
        out_file = Path(out_path)
        with open(out_file, "wb") as out:
            for part in response.iter_content(1024 * 1024):
                out.write(part)
