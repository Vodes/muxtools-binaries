import re
import shutil
from typing import Any

from muxtools_binaries.build import BuildContext
from muxtools_binaries.checks import default_checks as checks
from muxtools_binaries.io import extract
from muxtools_binaries.models import Model
from muxtools_binaries.updates import UpdateContext


class UpdateOptions(Model):
    repository: str


def build(ctx: BuildContext) -> None:
    if ctx.config.asset is None:
        raise ValueError("FFmpeg requires an imported asset")
    unpacked = ctx.work / "import"
    extract(ctx.asset(), unpacked, ctx.config.asset.format)
    extension = ".exe" if ctx.windows else ""

    for name in ctx.package.executables:
        matches = list(unpacked.rglob(name + extension))
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one imported {name}")
        ctx.stage_binary(matches[0], name)
    for path in unpacked.rglob("*"):
        if path.is_file() and (
            path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")) or "license" in path.parts
        ):
            output = ctx.stage / "licenses" / path.relative_to(unpacked)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)


def discover_update(ctx: UpdateContext) -> dict[str, Any]:
    data, updated = ctx.original, ctx.data
    repository = UpdateOptions.model_validate(data["update"]).repository
    releases = ctx.get_json(f"https://api.github.com/repos/{repository}/releases?per_page=100")
    release = next(
        (r for r in releases if not r["draft"] and not r["prerelease"] and r["tag_name"].startswith("autobuild-")),
        None,
    )
    if not release:
        raise ValueError("No dated FFmpeg release available")
    source_versions = set()
    for target, config in updated["targets"].items():
        suffix = r"linux64-nonfree-[\d.]+\.tar\.xz" if target.startswith("linux") else r"win64-nonfree-[\d.]+\.zip"
        pattern = r"ffmpeg-n(\d+(?:\.\d+)+(?:-\d+-g[0-9a-f]+)?)-" + suffix
        matches = [(a, re.fullmatch(pattern, a["name"])) for a in release["assets"]]
        matches = [(asset, match) for asset, match in matches if match]
        if len(matches) != 1:
            raise ValueError(f"Ambiguous or missing FFmpeg artifact for {target}")
        asset, match = matches[0]
        source_versions.add(match[1])
        config["asset"]["url"] = asset["browser_download_url"]
        config["asset"]["sha256"] = (asset.get("digest") or "").removeprefix("sha256:") or ctx.remote_hash(
            asset["browser_download_url"]
        )
    if len(source_versions) != 1:
        raise ValueError("FFmpeg targets have different source versions")
    updated["version"] = source_versions.pop() + "-" + release["tag_name"][10:20]
    return updated


__all__ = ["build", "checks", "discover_update"]
