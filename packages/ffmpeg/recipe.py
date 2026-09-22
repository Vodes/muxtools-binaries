import re
from typing import Any

from packaging.version import Version

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
    extension = ctx.target_info.executable_suffix

    for name in ctx.package.executables:
        matches = list(filter(lambda f: f.is_file(), unpacked.rglob(name + extension)))
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one imported {name}")
        ctx.stage_binary(matches[0], name)


def discover_update(ctx: UpdateContext) -> dict[str, Any]:
    data, updated = ctx.original, ctx.data
    repository = UpdateOptions.model_validate(data["update"]).repository
    releases = ctx.get_json(f"https://api.github.com/repos/{repository}/releases?per_page=100")
    candidates = []
    for release in releases:
        if release["draft"] or release["prerelease"]:
            continue
        match = re.fullmatch(r"v(\d+(?:\.\d+)+)-r(\d+)-nonfree", release["tag_name"])
        if match:
            candidates.append((Version(match[1]), int(match[2]), release))
    if not candidates:
        raise ValueError("No nonfree FFmpeg release available")
    source_version, revision, release = max(candidates, key=lambda candidate: candidate[:2])

    for target, config in updated["targets"].items():
        name = f"ffmpeg-{source_version}-{target}-nonfree.tar.zst"
        matches = [asset for asset in release["assets"] if asset["name"] == name]
        if len(matches) != 1:
            raise ValueError(f"Ambiguous or missing FFmpeg artifact for {target}")
        asset = matches[0]
        url = asset["browser_download_url"]
        digest = (asset.get("digest") or "").removeprefix("sha256:")
        config["asset"]["sha256"] = digest or (
            config["asset"]["sha256"] if url == config["asset"]["url"] else ctx.remote_hash(url)
        )
        config["asset"]["url"] = url
        config["asset"]["format"] = "tar.zst"
    updated["version"] = f"{source_version}-r{revision}"
    return updated


__all__ = ["build", "checks", "discover_update"]
