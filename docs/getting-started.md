# Build a package

Run these commands from the repository root. Install Git and uv; container targets
also require Docker.
The Python project requires Python 3.12 or later. Docker must run Linux containers,
including when you build Windows packages.

## Prepare the checkout

```sh
git clone https://github.com/Vodes/muxtools-binaries.git
cd muxtools-binaries
git submodule update --init --recursive
uv sync --frozen
uv run muxtools-build validate
```

The submodule provides the audio test fixture. `validate` checks every manifest,
loads each recipe, and validates its options and check definitions. It does not
download package sources or compile executables.

## Build and test

Build a local builder image, then build FLAC:

```sh
docker build -t muxtools-builder:local -f builder/Dockerfile .
uv run muxtools-build build flac --target linux-x86_64 --image muxtools-builder:local
```

The build command prints the archive path. Use that path to test the package
on Linux, for example:

```sh
uv run muxtools-build test dist/flac-1.5.0-linux-x86_64.tar.zst
```

To use the adopted image from `builder/lock.toml`, omit `--image`. Use `--jobs 4`
to limit parallel compilation.

Build the Windows target in the same Linux container:

```sh
uv run muxtools-build build flac --target windows-x86_64 --image muxtools-builder:local
```

Copy the resulting archive to Windows and run `muxtools-build test` there.
CI supplies native runners for every declared target.
`--structural-only` checks archive structure without running the tools;
it does not replace native tests.

FLAC also declares `linux-arm64`. On an x86-64 Docker host with ARM emulation,
build and use the ARM builder explicitly:

```sh
docker buildx build --platform linux/arm64 --build-arg BUILDER_ID=manylinux-arm64 \
  --build-arg BASE=quay.io/pypa/manylinux_2_34_aarch64@sha256:db1a485b015c1d9a6d7e2929367a69a6f772964be06cca8fe585f959a47ec0b7 \
  --load -t muxtools-builder:arm64 -f builder/Dockerfile .
uv run muxtools-build build flac --target linux-arm64 --image muxtools-builder:arm64
```

This runs through QEMU locally and is slower than the native ARM64 CI runner.

On an ARM64 Mac, any package that declares a `macos-arm64` target builds without
Docker. Install the native build tools, then substitute its package name below:

```sh
brew install autoconf automake libtool pkg-config cmake ninja docbook-xsl libxslt
uv run muxtools-build build PACKAGE --target macos-arm64
uv run muxtools-build test dist/PACKAGE-VERSION-macos-arm64.tar.zst
```

The SVT-AV1, dovi-tool, and hdr10plus-tool packages require Rust 1.95.0.
For the SVT-AV1 packages, also install `cargo-c` with Homebrew.
Build dates use the current time by default. Set `SOURCE_DATE_EPOCH` to a Unix
timestamp to use a specific date and time in a rebuild.

The build command prints the exact archive path. Native builds enforce macOS 12.0
compatibility and reject non-system dylib dependencies.

If you invoke the test command outside the checkout, put `--root /path/to/checkout`
before `test`. This lets the runner find `tests/data/audio/wav_source.wav`.
See [runtime requirements](architecture/builder.md#imported-runtime-requirements)
for imported tools and [Checks and tests](architecture/testing.md) for test behavior.

## Run the development checks

```sh
uv run just
```

The default recipe, `check`, runs manifest and recipe validation, Python and TOML
formatting and lint checks, pyrefly, and pytest. CI runs the same recipe.
The `lint` recipe includes package validation and all formatting, linting, and type checks.
Pyrefly checks the shared code, tests, builder scripts, and all package recipes together.

Run individual checks or apply formatting with:

```sh
uv run just lint
uv run just test
uv run just test -k test_archive_roundtrip
uv run just format
```

The test recipe includes package-specific tests in `packages/<name>/tests/` and
forwards extra arguments to pytest. The format recipe formats Python and TOML
files and applies automatic Ruff fixes. Use `uv run just --list` to list recipes.

Build files go in `build/`; archives and checksums go in `dist/`. Both are gitignored.
Use `uv run muxtools-build --help` for other commands, or see
[Releases and updates](architecture/releases.md) for publication.
