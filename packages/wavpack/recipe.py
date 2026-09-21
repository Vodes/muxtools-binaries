from muxtools_binaries.build import BuildContext
from muxtools_binaries.checks import AudioCheck, CheckSuite, default_checks
from muxtools_binaries.models import Model, Package
from muxtools_binaries.updates import source_update as discover_update


class Options(Model):
    pass


def build(ctx: BuildContext) -> None:
    if ctx.package.source is None:
        raise ValueError("WavPack requires a source pin")
    for tier in ctx.config.cpu_levels:
        ctx.tier = tier
        source = ctx.source(ctx.package.name, ctx.package.source)
        ctx.cmake(
            ctx.package.name,
            source,
            {
                "BUILD_TESTING": False,
                "WAVPACK_BUILD_PROGRAMS": True,
                "WAVPACK_BUILD_COOLEDIT_PLUGIN": False,
                "WAVPACK_BUILD_WINAMP_PLUGIN": False,
                "WAVPACK_INSTALL_DOCS": False,
            },
            install=True,
        )
        ctx.stage_binaries()


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    suite.functional = [
        AudioCheck(command="wavpack", args=["-y", "{source}", "-o", "{output}"], suffix="wv", lossless=True),
        AudioCheck(command="wavpack", args=["-b128", "-y", "{source}", "-o", "{output}"], suffix="wv"),
    ]
    return suite


__all__ = ["Options", "build", "checks", "discover_update"]
