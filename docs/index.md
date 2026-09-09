# muxtools-binaries

This project builds and packages portable command-line tools for Linux and Windows
x86-64.

| Task | Start here |
| --- | --- |
| Download a tool | [GitHub releases](https://github.com/Vodes/muxtools-binaries/releases) |
| Build and test locally | [Build a package](getting-started.md) |
| Add a tool to the project | [Add a package](packages/adding-a-package.md) |
| Change a TOML setting | [Manifest reference](packages/manifest.md) |
| Change build steps or checks | [Recipe reference](packages/recipes.md) |
| Understand the pipeline | [Architecture](architecture.md) |
| Publish a release | [Releases and updates](architecture/releases.md) |

## Available packages

| Package | Executables | Build method |
| --- | --- | --- |
| fdkaac | `fdkaac` | Source build with FDK AAC. |
| FLAC | `flac`, `metaflac` | Source build with Ogg. |
| Opus tools | `opusenc`, `opusdec`, `opusinfo` | Source build with audio dependencies and FLAC input support. |
| WavPack | `wavpack`, `wvunpack`, `wvgain`, `wvtag` | Source build. |
| x265 | `x265` | Source build with 8-, 10-, and 12-bit support in each CPU variant. |
| FFmpeg | `ffmpeg`, `ffprobe` | Import from Vodes/FFmpeg-Builds. |
| MKVToolNix | `mkvmerge`, `mkvextract`, `mkvinfo`, `mkvpropedit` | Import upstream binaries. |

All packages declare Linux and Windows targets. Source-built Linux executables
target x86-64-v2 and glibc 2.34. x265 also supplies AVX2, AVX512, and Zen 4 variants.
See [runtime requirements](architecture/builder.md#imported-runtime-requirements)
before using imported tools.

The [catalog contract](architecture/artifacts.md#version-catalog) describes how
applications can find published versions and executable paths.
