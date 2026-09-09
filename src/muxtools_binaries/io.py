import hashlib
import os
import shutil
import stat
import subprocess
import tarfile
import time
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx2
import py7zr
import zstandard

from .models import safe_path

type Command = Sequence[str | Path | int]


def run(
    args: Command,
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    capture: bool = False,
    timeout: float | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(arg) for arg in args]
    print("+ " + " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
        timeout=timeout,
        stdin=subprocess.DEVNULL if input is None else None,
        input=input,
    )


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url: str, digest: str, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / digest
    if path.exists():
        if sha256(path) != digest:
            raise ValueError(f"Corrupted cached download: {path}")
        return path
    temporary = path.with_suffix(f".{os.getpid()}.part")
    try:
        with httpx2.stream("GET", url, follow_redirects=True, timeout=httpx2.Timeout(120, connect=30)) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    output.write(chunk)
        if sha256(temporary) != digest:
            raise ValueError(f"SHA-256 mismatch for {url}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def extract(archive: Path, destination: Path, kind: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Extraction destination must be empty")
    directory_times: list[tuple[Path, float]] = []
    if kind == "zip":
        with zipfile.ZipFile(archive) as source:
            seen = set()
            for member in source.infolist():
                name = safe_path(member.filename.rstrip("/"))
                if name.casefold() in seen or stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError(f"Duplicate or linked archive entry: {name}")
                seen.add(name.casefold())
                output = destination / name
                if member.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    directory_times.append((output, time.mktime((*member.date_time, 0, 0, -1))))
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(member) as src, output.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    output.chmod(0o755 if member.external_attr >> 16 & 0o111 else 0o644)
                    modified = time.mktime((*member.date_time, 0, 0, -1))
                    os.utime(output, (modified, modified))
    elif kind == "7z":
        with py7zr.SevenZipFile(archive) as source:
            seen = set()
            for item in source.list():
                name = safe_path(item.filename)
                if item.is_symlink or name.casefold() in seen:
                    raise ValueError("Duplicate or linked 7z archive entry")
                seen.add(name.casefold())
            source.extractall(destination)
    else:
        with archive.open("rb") as raw:
            reader = zstandard.ZstdDecompressor().stream_reader(raw) if kind == "tar.zst" else raw
            with tarfile.open(fileobj=reader, mode="r|*") as source:
                seen = set()
                for member in source:
                    if member.name in (".", "./"):
                        continue
                    name = safe_path(member.name)
                    if name.casefold() in seen or not (member.isfile() or member.isdir()):
                        raise ValueError(f"Duplicate or non-regular archive entry: {name}")
                    seen.add(name.casefold())
                    source.extract(member, destination, filter="data")
                    if member.isdir() and member.mtime is not None:
                        directory_times.append((destination / name, member.mtime))
    for path, modified in reversed(directory_times):
        os.utime(path, (modified, modified))
