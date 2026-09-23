import re
import subprocess
from typing import Any

from packaging.version import Version

from muxtools_binaries.build import BuildContext
from muxtools_binaries.svtav1 import build_encoder, build_ffms2_dependencies, build_hdr_dependencies, checks
from muxtools_binaries.updates import UpdateContext


def build(ctx: BuildContext) -> None:
    build_hdr_dependencies(ctx)
    build_ffms2_dependencies(ctx)
    build_encoder(ctx, essential=True)


def discover_update(ctx: UpdateContext) -> dict[str, Any]:
    source = ctx.data["source"]
    result = subprocess.run(
        ["git", "ls-remote", "--tags", source["repository"]], check=True, text=True, capture_output=True
    )
    refs = dict(line.split()[::-1] for line in result.stdout.splitlines())
    candidates = []
    for ref, commit in refs.items():
        match = re.fullmatch(r"refs/tags/(v(\d+(?:\.\d+)+)-Essential)", ref)
        if match:
            candidates.append((Version(match[2]), match[1], refs.get(ref + "^{}", commit)))
    if not candidates:
        raise ValueError("No Essential release tags found")
    version, tag, commit = max(candidates)
    if version > Version(source["tag"].removeprefix("v").removesuffix("-Essential")):
        source.update(tag=tag, commit=commit)
        ctx.data["version"] = str(version)
    return ctx.data


__all__ = ["build", "checks", "discover_update"]
