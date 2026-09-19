---
render_macros: true
---

# muxtools-binaries

This project builds and packages portable command-line tools for Linux and Windows
x86-64, with opt-in Linux ARM64 targets.

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

{{ available_packages() }}

Source-built Linux executables target glibc 2.34. The x86-64 baseline is
x86-64-v2; ARM64 packages use the generic ARMv8-A baseline.
x265 also supplies AVX2, AVX512, and Zen 4 variants.
See [runtime requirements](architecture/builder.md#imported-runtime-requirements)
before using imported tools.

The [catalog contract](architecture/artifacts.md#version-catalog) describes how
applications can find published versions and executable paths.
