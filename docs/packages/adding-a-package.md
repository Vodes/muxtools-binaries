# Add a package

A package needs two files: `package.toml` declares its inputs and settings;
`recipe.py` defines how to build, test, and update it.

```text
packages/example/
├── package.toml
├── recipe.py
└── tests/             # Optional package-specific unit tests
    └── test_recipe.py
```

The directory name must match the manifest's `name`. CI discovers the package
and its declared targets automatically.

## 1. Choose a build method

| Method | Manifest type | Starting example |
| --- | --- | --- |
| Compile an Autotools project | `source-build` | `packages/flac/` or `packages/wavpack/` |
| Compile with custom steps | `source-build` | `packages/x265/` |
| Import a third-party build | `external-build` | `packages/ffmpeg/` |
| Import an upstream release | `upstream-binary` | `packages/mkvtoolnix/` |

All methods need a recipe. The manifest selects the inputs and settings;
the recipe supplies the build steps.

## 2. Write the manifest

This WavPack example shows a complete source-build manifest. Replace the identity,
source pin, and executable names for your tool.

```toml
schema_version = 1
name = "wavpack"
version = "5.9.0"
version_code = 1
type = "source-build"

[source]
repository = "https://github.com/dbry/WavPack.git"
tag = "5.9.0"
commit = "5803634a030e2a11dba602ba057b89cc34486c67"

[targets.linux-x86_64]

[targets.windows-x86_64]

[executables]
wavpack = ["--version"]
wvunpack = ["--version"]
wvgain = ["-v"]
wvtag = ["--version"]
```

Empty target tables select GCC, no LTO, and the baseline CPU level.
Declare only targets that the recipe supports. Executable keys are logical names
without `.exe` or CPU suffixes. Their arrays contain smoke-test arguments.

For imports, set `type` to `external-build` or `upstream-binary` and specify a
verified [asset per target](manifest.md#imported-asset-table) instead of `[source]`.

## 3. Write the recipe

An Autotools recipe can reuse the shared build and update helpers. This example
also adds a functional check for the WavPack encoder:

```python
from muxtools_binaries.build import AutotoolsOptions as Options
from muxtools_binaries.build import build_autotools as build
from muxtools_binaries.checks import AudioCheck, CheckSuite, default_checks
from muxtools_binaries.models import Package
from muxtools_binaries.updates import source_update as discover_update


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    suite.functional = [
        AudioCheck(
            command="wavpack",
            args=["-y", "{source}", "-o", "{output}"],
            suffix="wv",
            lossless=True,
        )
    ]
    return suite


__all__ = ["Options", "build", "checks", "discover_update"]
```

All three hooks are required: `build`, `checks`, and `discover_update`.
In this example, the shared helpers provide `build` and `discover_update`.
`default_checks` creates a smoke check for each executable, expecting exit code 0.
Adjust the [smoke checks](recipes.md#smoke-commands) if a command returns a different
exit code or needs an output check.

For a custom recipe, `build(ctx)` must place every declared executable variant in
`ctx.stage`. It must also include licenses and any private runtime files.
Use `ctx.stage_binary()` to apply the standard executable names and modes.
See the [recipe reference](recipes.md) for helpers and check types.

Use `[build]` for configure arguments or other options accepted by the recipe's
`Options` model. Use `[targets.<target>.build]` to override them for one target.
The [manifest reference](manifest.md#recipe-build-options) explains the accepted
options and how overrides merge.

## 4. Validate and test

Run the [development checks](../getting-started.md#run-the-development-checks).
Build each declared target and run its archive tests on the matching operating
system. Add unit tests under the package directory for custom build or update logic.

Confirm that all executable names and variants are present, the licenses are
included, and required runtime libraries are declared. If the recipe imports
wrappers or private executables, declare their formats in `CheckSuite.files`.

Open a PR with the package directory and any needed documentation changes.
For updates to an existing package, follow the
[version rules](../architecture/releases.md#version-identity).
