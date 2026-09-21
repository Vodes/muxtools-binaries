"""Install checksum-pinned Windows cross toolchains into the builder image."""

import hashlib
import json
import platform
import shutil
import subprocess
import tarfile
import tomllib
import urllib.request
from pathlib import Path
from typing import Any

LOCK_PATH = Path("/opt/builder/lock.toml")
WORK = Path("/tmp/windows-toolchains")


def download(url: str, digest: str, name: str) -> Path:
    destination = WORK / name
    urllib.request.urlretrieve(url, destination)
    with destination.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != digest:
        raise ValueError(f"Toolchain hash mismatch for {url}: {actual}")
    return destination


def unpack(archive: Path, destination: Path) -> Path:
    destination.mkdir()
    with tarfile.open(archive) as source:
        source.extractall(destination, filter="data")
    children = list(destination.iterdir())
    if len(children) != 1:
        raise ValueError(f"Expected one top-level archive entry in {archive.name}")
    return children[0]


def install_llvm_mingw(config: dict[str, Any], host: dict[str, str]) -> None:
    archive = download(host["url"], host["sha256"], "llvm-mingw.tar.xz")
    source = unpack(archive, WORK / "llvm-mingw")
    shutil.move(source, "/opt/llvm-mingw")
    Path("/opt/llvm-mingw/bin/clang-cl").symlink_to("clang")
    windows_header = Path("/opt/llvm-mingw/generic-w64-mingw32/include/Windows.h")
    windows_header.symlink_to("windows.h")
    if config["version"] not in host["url"]:
        raise ValueError("The llvm-mingw archive URL does not match its locked version")


def install_xwin(config: dict[str, Any], host: dict[str, str]) -> None:
    archive = download(host["url"], host["sha256"], "xwin.tar.gz")
    source = unpack(archive, WORK / "xwin")
    executable = source if source.is_file() else next(source.rglob("xwin"))
    installed = Path("/opt/xwin-tools/xwin")
    installed.parent.mkdir()
    shutil.copy2(executable, installed)
    installed.chmod(0o755)

    manifest = download(config["manifest_url"], config["manifest_sha256"], "VisualStudio.vsman")
    packages = json.loads(manifest.read_text())["packages"]
    crt_headers = next(
        (package for package in packages if package["id"] == f"Microsoft.VC.{config['crt']}.CRT.Headers.base"),
        None,
    )
    if not crt_headers or crt_headers["version"] != config["toolset"]:
        raise ValueError("The frozen manifest does not contain the locked MSVC toolset")
    cache = WORK / "xwin-cache"
    (cache / "dl").mkdir(parents=True)
    shutil.copy2(manifest, cache / "dl" / f"pkg_manifest_{config['manifest_sha256']}.vsman")
    channel = WORK / "xwin-channel.json"
    channel.write_text(
        json.dumps(
            {
                "channelItems": [
                    {
                        "id": "Microsoft.VisualStudio.Manifests.VisualStudio",
                        "version": "frozen",
                        "type": "Manifest",
                        "payloads": [
                            {
                                "fileName": "VisualStudio.vsman",
                                "sha256": config["manifest_sha256"],
                                "size": manifest.stat().st_size,
                                "url": config["manifest_url"],
                            }
                        ],
                    }
                ]
            }
        )
    )
    shutil.copy2(manifest, "/opt/builder/VisualStudio.vsman")
    shutil.copy2(channel, "/opt/builder/xwin-channel.json")
    subprocess.run(
        [
            installed,
            "--accept-license",
            "--manifest",
            channel,
            "--cache-dir",
            cache,
            "--sdk-version",
            config["sdk"],
            "--crt-version",
            config["crt"],
            "--arch",
            host["xwin_arch"],
            "--http-retry",
            "3",
            "--timeout",
            "180s",
            "splat",
            "--output",
            "/opt/xwin",
            "--use-winsysroot-style",
            "--preserve-ms-arch-notation",
        ],
        check=True,
    )
    expected = (
        Path("/opt/xwin/VC/Tools/MSVC") / config["crt"],
        Path("/opt/xwin/Windows Kits/10/Include") / config["sdk"],
        Path("/opt/xwin/Windows Kits/10/Lib") / config["sdk"],
    )
    if not all(path.is_dir() for path in expected):
        raise ValueError("xwin did not install the locked SDK and MSVC toolset")
    Path("/opt/xwin/WindowsKits").symlink_to("Windows Kits", target_is_directory=True)


def main() -> None:
    lock = tomllib.loads(LOCK_PATH.read_text())
    if lock.get("schema_version") != 3:
        raise ValueError("Unsupported builder lock schema")
    aliases = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}
    try:
        host_arch = aliases[platform.machine().casefold()]
    except KeyError as error:
        raise ValueError(f"Unsupported builder architecture: {platform.machine()}") from error
    WORK.mkdir()
    toolchains = lock["toolchains"]
    llvm_mingw = toolchains["llvm-mingw"]
    install_llvm_mingw(llvm_mingw, llvm_mingw["hosts"][host_arch])
    xwin = toolchains["xwin"]
    install_xwin(xwin, xwin["hosts"][host_arch])
    shutil.rmtree(WORK)


if __name__ == "__main__":
    main()
