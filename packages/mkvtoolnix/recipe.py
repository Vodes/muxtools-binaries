import re
import shutil
from typing import Any

from packaging.version import Version

from muxtools_binaries.build import BuildContext
from muxtools_binaries.checks import CheckSuite, default_checks
from muxtools_binaries.io import extract
from muxtools_binaries.models import Package
from muxtools_binaries.updates import UpdateContext


def build(ctx: BuildContext) -> None:
    if ctx.config.asset is None:
        raise ValueError("MKVToolNix requires an imported asset")
    asset = ctx.asset()
    if ctx.config.asset.format == "appimage":
        destination = ctx.stage / "MKVToolNix.AppImage"
        shutil.copy2(asset, destination)
        destination.chmod(0o755)
        for name in ctx.package.executables:
            wrapper = ctx.stage / name
            wrapper.write_text(
                '#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                + 'exec bash -c \'exec -a "$0" "$@"\' '
                + name
                + ' "$base/MKVToolNix.AppImage" "$@"\n'
            )
            wrapper.chmod(0o755)
        return
    unpacked = ctx.work / "import"
    extract(asset, unpacked, ctx.config.asset.format)
    matches = list(unpacked.rglob("mkvmerge.exe"))
    if len(matches) != 1:
        raise ValueError("Expected exactly one MKVToolNix installation")
    shutil.copytree(matches[0].parent, ctx.stage, dirs_exist_ok=True)
    for name in ctx.package.executables:
        (ctx.stage / (name + ".exe")).chmod(0o755)


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    for name, check in suite.smoke.items():
        check.stdout_prefix = name + " v"
    if target.startswith("linux"):
        suite.files = {name: "script" for name in package.executables}
        suite.files["MKVToolNix.AppImage"] = "elf"
    return suite


def discover_update(ctx: UpdateContext) -> dict[str, Any]:
    data, updated = ctx.original, ctx.data
    entries = ctx.get_json("https://mkvtoolnix.download/windows/releases/")
    versions = [
        Version(entry["name"].rstrip("/")) for entry in entries if re.fullmatch(r"\d+\.\d+(?:\.\d+)?/", entry["name"])
    ]
    version = str(max(versions))
    if Version(version) <= Version(data["version"]):
        return data
    for target, config in updated["targets"].items():
        url = (
            f"https://mkvtoolnix.download/appimage/MKVToolNix_GUI-{version}-x86_64.AppImage"
            if target.startswith("linux")
            else f"https://mkvtoolnix.download/windows/releases/{version}/mkvtoolnix-64-bit-{version}.zip"
        )
        config["asset"].update(url=url, sha256=ctx.remote_hash(url))
    updated["version"] = version
    return updated


__all__ = ["build", "checks", "discover_update"]
