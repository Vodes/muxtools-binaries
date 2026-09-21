# muxtools-binaries

Portable command-line tools for Linux and Windows on x86-64 and ARM64, plus macOS on ARM64.<br>
Linux and Windows (x86_64) are the only first-class targets. Everything else is provided on a best effort basis.<br>
Available packages are listed on the documention linked below. (Or just check the packages folder)

[Documentation](https://vodes.github.io/muxtools-binaries/) ·
[Downloads](https://github.com/Vodes/muxtools-binaries/releases) ·
[Package setup](docs/packages/adding-a-package.md)

Source builds use architecture-specific Linux builder images. Windows builds use
MinGW or the MSVC ABI through Clang. Packages declaring `macos-arm64` build natively.
Linux source builds require x86-64-v2 and glibc 2.34; macOS ARM64 builds target macOS 12.0.<br>
Some builds, x265 for example, also include AVX2, AVX512, and (a cut down) Zen 4 variants.<br>

Imported tools can have extra runtime requirements.<br>
This is, in most cases, going to be limited to `libgcc_s` which should not be a problem.

## Development

Install uv and Docker, then run:

```sh
git submodule update --init --recursive
uv sync --frozen
uv run muxtools-build validate
uv run pytest -q
```

See the [build guide](docs/getting-started.md) for build commands and checks.
Releases require a manual workflow run; normal CI only builds and tests packages.

## Documentation preview

```sh
uv sync --frozen --only-group docs
uv run --frozen --only-group docs zensical serve
```

Open <http://localhost:8000>. See [the docs guide](docs/contributing-docs.md)
for site configuration and GitHub Pages setup.
