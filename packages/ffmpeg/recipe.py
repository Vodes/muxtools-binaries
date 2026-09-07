from muxtools_binaries.build import BuildContext
from muxtools_binaries.recipes import imported


def build(ctx: BuildContext) -> None:
    imported(ctx)
