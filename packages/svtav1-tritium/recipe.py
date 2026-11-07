import re
from datetime import date, datetime
from typing import Any

from muxtools_binaries.build import BuildContext
from muxtools_binaries.svtav1 import build_encoder, build_ffms2_dependencies, build_hdr_dependencies, checks
from muxtools_binaries.updates import UpdateContext

API = "https://api.github.com/repos/Uranite/svt-av1-tritium"
BUILD_TAG = re.compile(r"pgo-build-(\d{4}-\d{2}-\d{2})-([0-9a-f]{7,40})")


def build(ctx: BuildContext) -> None:
    build_hdr_dependencies(ctx)
    build_ffms2_dependencies(ctx)
    build_encoder(ctx, ffms2=True, version_tag=ctx.package.version)


def discover_update(ctx: UpdateContext) -> dict[str, Any]:
    source = ctx.data["source"]
    current = BUILD_TAG.fullmatch(source["tag"])
    if current is None or not source["commit"].startswith(current[2]):
        raise ValueError("Tritium source pin must match its dated PGO tag suffix")
    current_date = date.fromisoformat(current[1])
    candidates: list[tuple[date, datetime, str]] = []
    page = 1
    while True:
        releases = ctx.get_json(f"{API}/releases?per_page=100&page={page}")
        if not releases:
            break
        for release in releases:
            match = BUILD_TAG.fullmatch(release.get("tag_name", ""))
            if release.get("draft") or match is None:
                continue
            published = release.get("published_at")
            if not published:
                raise ValueError(f"Missing publication timestamp for {match[0]}")
            timestamp = datetime.fromisoformat(published)
            if timestamp.tzinfo is None:
                raise ValueError(f"Missing publication timezone for {match[0]}")
            candidates.append((date.fromisoformat(match[1]), timestamp, match[0]))
        page += 1
    if not candidates:
        raise ValueError("No published Tritium PGO releases found")
    newest = max(candidates, key=lambda candidate: candidate[:2])
    if len({tag for day, timestamp, tag in candidates if (day, timestamp) == newest[:2]}) != 1:
        raise ValueError("Ambiguous Tritium PGO release ordering")
    day, _, tag = newest
    if day < current_date or tag == source["tag"]:
        return ctx.data

    # Upstream tags contain bookkeeping trees, not the encoder revision.
    # Resolve the source hash embedded in the tag instead of peeling the tag.
    suffix = tag.rsplit("-", 1)[1]
    commit = ctx.get_json(f"{API}/commits/{suffix}").get("sha", "")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None or not commit.startswith(suffix):
        raise ValueError(f"Invalid source commit for {tag}")
    source.update(tag=tag, commit=commit)
    ctx.data["version"] = tag.removeprefix("pgo-build-")
    return ctx.data


__all__ = ["build", "checks", "discover_update"]
