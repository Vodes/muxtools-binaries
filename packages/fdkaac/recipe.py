from muxtools_binaries.build import BuildContext
from muxtools_binaries.recipes import audio


def build(ctx: BuildContext) -> None:
    audio(ctx)
