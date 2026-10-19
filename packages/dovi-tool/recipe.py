from muxtools_binaries.build import BuildContext
from muxtools_binaries.checks import default_checks as checks
from muxtools_binaries.updates import source_update as discover_update


def build(ctx: BuildContext) -> None:
    if ctx.package.source is None:
        raise ValueError("dovi_tool requires a source pin")
    for tier in ctx.config.cpu_levels:
        ctx.tier = tier
        source = ctx.source(ctx.package.name, ctx.package.source)
        ctx.cargo_install(source, features=("internal-font",), default_features=False)
        ctx.stage_binaries()


__all__ = ["build", "checks", "discover_update"]
