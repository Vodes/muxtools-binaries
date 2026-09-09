import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from muxtools_binaries.build import BuildContext
from muxtools_binaries.checks import CheckSuite, VideoCheck, default_checks
from muxtools_binaries.models import Package
from muxtools_binaries.updates import UpdateContext

BASE_URL = "https://artifacts.videolan.org/x264/"
PLATFORMS = {
    "linux-x86_64": ("release-debian-amd64", ""),
    "windows-x86_64": ("release-win64", ".exe"),
}


class ArtifactLinks(HTMLParser):
    def __init__(self, extension: str) -> None:
        super().__init__()
        self.pattern = re.compile(r"x264-r(\d+)-([0-9a-f]{7,40})" + re.escape(extension))
        self.versions: set[tuple[int, str]] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            match = self.pattern.fullmatch(dict(attrs).get("href") or "")
            if match:
                self.versions.add((int(match[1]), match[2]))


def build(ctx: BuildContext) -> None:
    ctx.stage_binary(ctx.asset(), "x264")
    shutil.copytree(Path(__file__).with_name("licenses"), ctx.stage / "licenses", dirs_exist_ok=True)


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    suite.smoke["x264"].stdout_prefix = "x264 "
    suite.functional = [
        VideoCheck(
            command="x264",
            suffix="264",
            bit_depth=depth,
            args=[
                "--demuxer",
                "raw",
                "--input-res",
                "{width}x{height}",
                "--input-csp",
                "i420",
                "--input-depth",
                "{bit_depth}",
                "--output-depth",
                "{bit_depth}",
                "--fps",
                "24",
                "--frames",
                "1",
                "--preset",
                "ultrafast",
                "-o",
                "{output}",
                "{source}",
            ],
        )
        for depth in (8, 10)
    ]
    return suite


def discover_update(ctx: UpdateContext) -> dict[str, Any]:
    available = []
    for target in ctx.data["targets"]:
        directory, extension = PLATFORMS[target]
        parser = ArtifactLinks(extension)
        parser.feed(ctx.get_text(f"{BASE_URL}{directory}/"))
        available.append(parser.versions)
    common = set.intersection(*available)
    if not common:
        raise ValueError("No common x264 revision available for all targets")
    revision = max(number for number, _ in common)
    candidates = [commit for number, commit in common if number == revision]
    if len(candidates) != 1:
        raise ValueError(f"Ambiguous x264 revision r{revision}")
    current = re.fullmatch(r"r(\d+)-[0-9a-f]{7,40}", ctx.original["version"])
    if current is None:
        raise ValueError("Expected an x264 version of the form r<revision>-<commit>")
    if revision <= int(current[1]):
        return ctx.original
    version = f"r{revision}-{candidates[0]}"
    for target, config in ctx.data["targets"].items():
        directory, extension = PLATFORMS[target]
        url = f"{BASE_URL}{directory}/x264-{version}{extension}"
        config["asset"].update(url=url, sha256=ctx.remote_hash(url))
    ctx.data["version"] = version
    return ctx.data


__all__ = ["build", "checks", "discover_update"]
