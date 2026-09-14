from muxtools_binaries.build import AutotoolsOptions as Options
from muxtools_binaries.build import BuildContext, build_autotools
from muxtools_binaries.checks import AudioCheck, CheckSuite, default_checks
from muxtools_binaries.models import Package
from muxtools_binaries.updates import source_update as discover_update


def build(ctx: BuildContext) -> None:
    if not ctx.msvc:
        build_autotools(ctx)
        return
    if ctx.package.source is None:
        raise ValueError("FLAC requires a pinned source")
    for tier in ctx.config.cpu_levels:
        ctx.tier = tier
        ctx.cmake(
            "ogg",
            ctx.source("ogg", ctx.package.dependencies["ogg"]),
            {"BUILD_SHARED_LIBS": False, "BUILD_TESTING": False, "INSTALL_DOCS": False},
            install=True,
        )
        source = ctx.source("flac", ctx.package.source)
        getopt = source / "src/share/getopt/getopt.c"
        contents = getopt.read_text()
        guard = "#if !defined __STDC__ || !__STDC__"
        if contents.count(guard) != 1:
            raise ValueError("FLAC getopt compatibility guard changed")
        # clang-cl omits __STDC__; the legacy fallback otherwise erases const
        # from getenv's declaration after the Windows headers were included.
        getopt.write_text(contents.replace(guard, "#if !defined _MSC_VER && (!defined __STDC__ || !__STDC__)"))
        ctx.cmake(
            "flac",
            source,
            {
                "BUILD_SHARED_LIBS": False,
                "BUILD_TESTING": False,
                "BUILD_EXAMPLES": False,
                "BUILD_DOCS": False,
                "BUILD_CXXLIBS": False,
                "WITH_OGG": True,
                "INSTALL_MANPAGES": False,
            },
            install=True,
        )
        ctx.stage_binaries()


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    suite.functional = [
        AudioCheck(command="flac", args=["-f", "-o", "{output}", "{source}"], suffix="flac", lossless=True)
    ]
    return suite


__all__ = ["Options", "build", "checks", "discover_update"]
