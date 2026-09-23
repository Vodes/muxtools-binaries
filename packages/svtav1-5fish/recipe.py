from muxtools_binaries.build import BuildContext
from muxtools_binaries.svtav1 import build_encoder, build_hdr_dependencies, checks
from muxtools_binaries.updates import UpdateContext, latest_tag


def build(ctx: BuildContext) -> None:
    build_hdr_dependencies(ctx)
    build_encoder(ctx)


def discover_update(ctx: UpdateContext) -> dict:
    ctx.data["source"] = latest_tag(ctx.data["source"])
    if ctx.pins_changed:
        ctx.data["version"] = ctx.data["source"]["tag"].removeprefix("v")
    return ctx.data


__all__ = ["build", "checks", "discover_update"]
