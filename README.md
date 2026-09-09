# muxtools-binaries

Portable command-line tools for Linux and Windows x86-64.<br>
Available packages are listed on the documention linked below. (Or just check the packages folder)

[Documentation](https://vodes.github.io/muxtools-binaries/) ·
[Downloads](https://github.com/Vodes/muxtools-binaries/releases) ·
[Package setup](docs/packages/adding-a-package.md)

Source builds use a shared Linux builder image. Windows builds use MinGW.
Linux source builds require x86-64-v2 and glibc 2.34. x265 also includes
AVX2, AVX512, and Zen 4 variants. Imported tools can have extra runtime requirements.

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
