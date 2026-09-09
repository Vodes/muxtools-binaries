from typing import Any

from muxtools_binaries.build import AutotoolsOptions as Options
from muxtools_binaries.build import build_autotools as build
from muxtools_binaries.checks import AudioCheck, CheckSuite, CommandCheck, default_checks
from muxtools_binaries.models import Package
from muxtools_binaries.updates import UpdateContext


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    suite.smoke["fdkaac"] = CommandCheck(
        args=package.executables["fdkaac"], exit_codes=[1], stdout_prefix="fdkaac ", stdout_contains=["Usage: fdkaac"]
    )
    suite.functional = [AudioCheck(command="fdkaac", args=["-b", "128", "-o", "{output}", "{source}"], suffix="m4a")]
    return suite


def discover_update(ctx: UpdateContext) -> dict[str, Any]:
    data = ctx.source_tags()
    if ctx.pins_changed:
        data["version"] += "-libfdk-" + data["dependencies"]["fdk"]["tag"].removeprefix("v")
    return data


__all__ = ["Options", "build", "checks", "discover_update"]
