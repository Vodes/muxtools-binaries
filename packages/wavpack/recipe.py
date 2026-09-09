from muxtools_binaries.build import AutotoolsOptions as Options
from muxtools_binaries.build import build_autotools as build
from muxtools_binaries.checks import AudioCheck, CheckSuite, default_checks
from muxtools_binaries.models import Package
from muxtools_binaries.updates import source_update as discover_update


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    suite.functional = [
        AudioCheck(command="wavpack", args=["-y", "{source}", "-o", "{output}"], suffix="wv", lossless=True),
        AudioCheck(command="wavpack", args=["-b128", "-y", "{source}", "-o", "{output}"], suffix="wv"),
    ]
    return suite


__all__ = ["Options", "build", "checks", "discover_update"]
