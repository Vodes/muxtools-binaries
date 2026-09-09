# Architecture

Package definitions describe what to build. The public catalog records what was
published. A requested build always runs, even if that version is already published.

## Build flow

1. Load `packages/<name>/package.toml` and validate its recipe options and checks.
2. Run `recipe.py` in the builder image to compile or import the executables.
3. Check the staged files and create a `.tar.zst` archive with `.metadata.toml`.
4. Test the archive on its target operating system.
5. On a manual release run, publish all targets and update `versions.json`.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| `packages/<name>/package.toml` | Source pins, target settings, imported assets, and recipe options. |
| `packages/<name>/recipe.py` | Build steps, check definitions, and update discovery. |
| `packages/<name>/tests/` | Optional tests for package-specific behavior. |
| `src/muxtools_binaries/` | Toolchains, build helpers, archive checks, and release operations. |
| `builder/` | Builder image, adopted digest, and qualification tests. |
| `.github/workflows/` | Build selection, native tests, update PRs, releases, and documentation. |

## Detailed contracts

- [Builder and toolchains](architecture/builder.md): image qualification, CPU levels, and compiler runtimes.
- [Checks and tests](architecture/testing.md): structural checks, native execution, and CPU eligibility.
- [Archives and catalog](architecture/artifacts.md): archive layout, metadata, and the public catalog.
- [Releases and updates](architecture/releases.md): version identity, publication gates, and recovery.

To change or add a package, start with [Add a package](packages/adding-a-package.md).
Use the [manifest reference](packages/manifest.md) for accepted TOML fields and defaults.

## Compatibility

The current `.tar.zst` archives and catalog require consumer changes from the old
ZIP/JSON contract. Historical releases remain available and are not rewritten.
