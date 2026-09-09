from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator

from muxtools_binaries.build import BuildContext
from muxtools_binaries.checks import CheckSuite, VideoCheck, default_checks
from muxtools_binaries.models import Model, Package
from muxtools_binaries.recipes import recipe_options
from muxtools_binaries.updates import source_update as discover_update


class Options(Model):
    bit_depths: list[Literal[8, 10, 12]]
    cmake: dict[str, str | bool | int]

    @model_validator(mode="after")
    def multilib(self) -> Self:
        if 8 not in self.bit_depths or len(set(self.bit_depths)) != len(self.bit_depths):
            raise ValueError("x265 needs an 8-bit CLI and unique bit depths")
        reserved = {
            "HIGH_BIT_DEPTH",
            "MAIN12",
            "EXPORT_C_API",
            "ENABLE_CLI",
            "LINKED_10BIT",
            "LINKED_12BIT",
            "EXTRA_LIB",
        }
        if reserved & self.cmake.keys():
            raise ValueError("Configure x265 bit_depths instead of overriding multilib wiring")
        return self


def multilib_definitions(depth: int, libraries: dict[int, Path]) -> dict[str, str | bool | int]:
    definitions: dict[str, str | bool | int] = dict(
        HIGH_BIT_DEPTH=depth > 8,
        MAIN12=depth == 12,
        EXPORT_C_API=depth == 8,
        ENABLE_CLI=depth == 8,
    )
    if depth == 8:
        definitions.update(
            LINKED_10BIT=10 in libraries, LINKED_12BIT=12 in libraries, EXTRA_LIB=";".join(map(str, libraries.values()))
        )
    return definitions


def build(ctx: BuildContext) -> None:
    options = recipe_options(ctx.package, ctx.target, Options)
    if ctx.package.source is None:
        raise ValueError("x265 requires a source pin")
    for tier in ctx.config.cpu_levels:
        ctx.tier = tier
        source = ctx.source("x265", ctx.package.source) / "source"
        libraries: dict[int, Path] = {}
        for depth in sorted(options.bit_depths, reverse=True):
            result = ctx.cmake(f"x265-{depth}", source, options.cmake | multilib_definitions(depth, libraries))
            if depth == 8:
                ctx.stage_binary(result / ("x265.exe" if ctx.windows else "x265"), "x265")
            else:
                libraries[depth] = result / "libx265.a"


def checks(package: Package, target: str) -> CheckSuite:
    options = recipe_options(package, target, Options)
    suite = default_checks(package)
    suite.functional = [
        VideoCheck(
            command="x265",
            suffix="hevc",
            bit_depth=depth,
            args=[
                "--input",
                "{source}",
                "--input-res",
                "{width}x{height}",
                "--fps",
                "24",
                "--input-depth",
                "{bit_depth}",
                "--output-depth",
                "{bit_depth}",
                "--frames",
                "1",
                "--preset",
                "ultrafast",
                "-o",
                "{output}",
            ],
        )
        for depth in options.bit_depths
    ]
    return suite


__all__ = ["Options", "build", "checks", "discover_update"]
