from muxtools_binaries.build import BuildContext
from muxtools_binaries.recipes import x265


def build(ctx: BuildContext) -> None:
    x265(ctx)
