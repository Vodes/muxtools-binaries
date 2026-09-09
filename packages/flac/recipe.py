from muxtools_binaries.build import AutotoolsOptions as Options
from muxtools_binaries.build import build_autotools as build
from muxtools_binaries.checks import AudioCheck, CheckSuite, default_checks
from muxtools_binaries.models import Package
from muxtools_binaries.updates import source_update as discover_update


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    suite.functional = [
        AudioCheck(command="flac", args=["-f", "-o", "{output}", "{source}"], suffix="flac", lossless=True)
    ]
    return suite


__all__ = ["Options", "build", "checks", "discover_update"]
