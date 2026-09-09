from typing import Any

from muxtools_binaries.build import AutotoolsOptions as Options
from muxtools_binaries.build import build_autotools as build
from muxtools_binaries.checks import AudioCheck, CheckSuite, default_checks
from muxtools_binaries.models import Package
from muxtools_binaries.updates import UpdateContext


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    suite.functional = [AudioCheck(command="opusenc", args=["--bitrate", "128", "{source}", "{output}"], suffix="opus")]
    return suite


def discover_update(ctx: UpdateContext) -> dict[str, Any]:
    data = ctx.source_tags()
    if ctx.pins_changed:
        data["version"] += "-libopus-" + data["dependencies"]["opus"]["tag"].removeprefix("v")
    return data


__all__ = ["Options", "build", "checks", "discover_update"]
