# Build a package

Run these commands from the repository root. Install Git, uv, and Docker first.
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
CI supplies native runners for both targets.
`--structural-only` checks archive structure without running the tools;
it does not replace native tests.

If you invoke the test command outside the checkout, put `--root /path/to/checkout`
before `test`. This lets the runner find `tests/data/audio/wav_source.wav`.
See [runtime requirements](architecture/builder.md#imported-runtime-requirements)
for imported tools and [Checks and tests](architecture/testing.md) for test behavior.

## Run the development checks

```sh
uv run muxtools-build validate
uv run ruff check src tests builder packages
uv run pytest -q
uv run mypy
uv run tombi format --check
uv run tombi lint --error-on-warnings
```

Use `uv run tombi format` to format TOML files. Package-specific tests in
`packages/<name>/tests/` run with the normal pytest command.

Build files go in `build/`; archives and checksums go in `dist/`. Both are gitignored.
Use `uv run muxtools-build --help` for other commands, or see
[Releases and updates](architecture/releases.md) for publication.
