"""Install hashed cross toolchains; run only while creating the builder image."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path
from typing import Any

LOCK = tomllib.loads(Path("/opt/builder/lock.toml").read_text())
WORK = Path("/tmp/toolchains")


def run(*args: str | Path, cwd: Path = WORK, **environment: str) -> None:
    subprocess.run(list(map(str, args)), cwd=cwd, env=dict(os.environ, **environment), check=True)


def download(config: dict[str, Any], prefix: str = "") -> Path:
    digest = config[prefix + "sha256"]
    output = WORK / digest
    urllib.request.urlretrieve(config[prefix + "url"], output)
    with output.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError(f"Toolchain hash mismatch: {config[prefix + 'url']}")
    return output


def unpack(archive: Path, destination: Path) -> Path:
    destination.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")
    return next(destination.iterdir())


def main() -> None:
    os.environ.pop("LD_LIBRARY_PATH", None)
    WORK.mkdir(exist_ok=True)
    kind = sys.argv[1]
    config = LOCK[kind]
    source = unpack(download(config), WORK / kind)
    if kind == "linux_arm64":
        run("./configure", "--prefix=/opt/ctng", cwd=source)
        run("make", "-j8", cwd=source)
        run("make", "install", cwd=source)
        build = WORK / "linux-build"
        build.mkdir()
        shutil.copy2("/opt/builder/linux-arm64.config", build / ".config")
        run("/opt/ctng/bin/ct-ng", "olddefconfig", cwd=build)
        resolved = (build / ".config").read_text()
        for key, value in {
            "CT_GCC_VERSION": config["gcc"],
            "CT_GLIBC_VERSION": config["glibc"],
            "CT_LINUX_VERSION": config["linux_headers"],
        }.items():
            if f'{key}="{value}"' not in resolved.splitlines():
                raise ValueError(f"crosstool-NG did not select the locked {key}: {value}")
        run("/opt/ctng/bin/ct-ng", "build.8", cwd=build)
        shutil.copy2(build / ".config", "/opt/builder/linux-arm64.resolved.config")
        # The crosstool-NG release contains the dependency download checksums.
        shutil.copytree(source / "packages", "/opt/builder/crosstool-ng-packages")
    elif kind == "llvm_mingw":
        shutil.move(source, "/opt/llvm-mingw")
        run("/opt/llvm-mingw/bin/aarch64-w64-mingw32-clang", "--version")
    elif kind == "xwin":
        executable = next(source.rglob("xwin")) if source.is_dir() else source
        shutil.copy2(executable, "/usr/local/bin/xwin")
        manifest = download(config, "manifest_")
        shutil.copy2(manifest, "/opt/builder/VisualStudio.vsman")
        # xwin expects a channel manifest and skips verification of its package
        # manifest. Seed its cache with our independently verified frozen bytes.
        cache = WORK / "xwin-cache"
        (cache / "dl").mkdir(parents=True)
        cached = cache / "dl" / f"pkg_manifest_{config['manifest_sha256']}.vsman"
        shutil.copy2(manifest, cached)
        channel = WORK / "channel.json"
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
        shutil.copy2(channel, "/opt/builder/xwin-channel.json")
        run(
            "xwin",
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
            ",".join(config["architectures"]),
            "--http-retry",
            "3",
            "--timeout",
            "180s",
            "splat",
            "--output",
            "/opt/xwin",
            "--use-winsysroot-style",
            "--preserve-ms-arch-notation",
        )
    elif kind == "macos":
        sdk = download(config, "sdk_")
        shutil.copy2(sdk, source / "tarballs" / f"MacOSX{config['sdk']}.sdk.tar.xz")
        run(
            "./build.sh",
            cwd=source,
            UNATTENDED="1",
            BUILD_FLAVOR=config["flavor"],
            TARGET_DIR="/opt/osxcross",
            OSX_VERSION_MIN=config["deployment_target"],
            ENABLE_ARCHS="arm64 x86_64",
            JOBS="8",
            ENABLE_REPLACEMENT_LIPO="0",
        )
    shutil.rmtree(WORK)
    shutil.rmtree("/tmp/ctng-downloads", ignore_errors=True)


if __name__ == "__main__":
    main()
