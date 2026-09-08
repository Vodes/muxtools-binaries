# muxtools-binaries

Build and package portable tools for Linux and Windows x86-64.

Supported packages: fdkaac, FLAC, Opus tools, FFmpeg/FFprobe, MKVToolNix, and multilib x265.

Source builds use one manylinux_2_34-derived Linux image; Windows builds cross-compile through MinGW.
Build tools, LLVM, and MinGW come from the image's repositories.
The final image digest freezes their versions.

- Source-built Linux executables target x86-64-v2 and glibc 2.34.
- x265 also provides explicit AVX2, AVX512, and zn4 variants, each supporting 8/10/12-bit encoding.
- Imported packages declare additional runtime requirements where necessary.

## Local development

Install Docker and uv, then set up the project and run the checks:

```sh
git submodule update --init --recursive
uv sync --frozen
uv run muxtools-build validate
uv run pytest -q
uv run mypy
uv run tombi format --check
uv run tombi lint --error-on-warnings
```

Build the local image, then build and test a package:

```sh
docker build -t muxtools-builder:local -f builder/Dockerfile .
uv run muxtools-build build flac --target linux-x86_64 --image muxtools-builder:local
uv run muxtools-build test dist/flac-1.5.0-linux-x86_64.tar.zst
```

Builds always run when requested, regardless of published versions.
Publishing skips versions that are already published. To release a changed build of the same upstream source,
use a new package version (for example `4.2.post1`) and increment `version_code`.
Windows artifacts must be smoke-tested on Windows; CI supplies native runners.

Audio smoke tests encode `tests/data/audio/wav_source.wav` with each runnable FLAC,
fdkaac, and Opus encoder variant. PyAV decodes the output, and Zimtohrli compares
each channel at 48 kHz. Tests reject empty or undecodable output, changed channel
counts, duration differences over 100 ms, silence, and per-channel MOS below 4.5.
This is a coarse corruption check, not a codec quality benchmark. Scores are printed
in the test log. When running outside the checkout, pass `--root /path/to/checkout`
before the `test` command so the fixture can be found.

### Working directories

These directories stay gitignored:

- `build/`: builds, downloads, and source checkouts.
- `dist/`: final archives.
- `context/`: local reference material.

### Formatting and commands

Use `uv run tombi format` to format repository TOML files.
Update PRs format and lint their manifest before opening;
packaging formats and lints `.metadata.toml` offline before archiving it.

Use `uv run muxtools-build --help` for matrix generation, repackaging, update discovery, and publishing commands.

See [the architecture and artifact contracts](docs/architecture.md) for recipe authoring,
runtime requirements, and catalog recovery.

## Releases

PRs and pushes to `main` build test artifacts only. No automatic upstream update publishes binaries.
Automatic builds select packages changed under `packages/<name>/`, comparing PRs with their base
and pushes with the previous branch commit. Documentation and editor-only changes do not select builds.
Shared changes (including `src/`, `tests/`, `builder/`, workflows, and dependency configuration)
rebuild every package. If the previous push commit is unavailable, every package is built.
Manual runs build all packages unless the `packages` input selects specific names.

Before the first release:

1. Run **Qualify builder**, enabling its publish checkbox to publish the tested image.
2. Adopt its GHCR digest in `builder/lock.toml`.
3. Manually run **Build and test** on `main` with **publish** checked.

For subsequent releases, repeat step 3 using the adopted builder.
Publication defaults to off in both workflows.

### Published artifacts

- Archives use `.tar.zst` on both platforms and contain `.metadata.toml`.
- File modification times are preserved, including dates from imported archives.
- The published `versions.json` lives on the `catalog-v1` release.

This is a clean break from the old ZIP/JSON contract; historical releases remain available.
