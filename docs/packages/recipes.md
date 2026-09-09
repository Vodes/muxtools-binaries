# Recipe reference

Each `packages/<name>/recipe.py` must export three callable hooks:

| Hook | Result | Responsibility |
| --- | --- | --- |
| `build(ctx: BuildContext)` | `None` | Populate `ctx.stage` with the package files. |
| `checks(package: Package, target: str)` | `CheckSuite` | Define smoke, functional, and required-file checks. |
| `discover_update(ctx: UpdateContext)` | Manifest dictionary | Return the proposed package definition. |

Use relative imports such as `from .helpers import build_part` for local helpers.

## Build hook

Use `BuildContext` from `muxtools_binaries.build`. It supplies these attributes:

| Attribute | Purpose |
| --- | --- |
| `root` | Repository root. |
| `package` | Validated package model. |
| `target`, `config`, `windows` | Target name, target settings, and Windows flag. |
| `work`, `stage` | Isolated work directory and final package staging directory. |
| `jobs` | Parallel build limit. |
| `tier` | Current CPU level. Starts as `baseline`; set it before each variant build. |
| `prefix` | Private dependency prefix for the current CPU level. |
| `cache` | Shared verified download cache under `build/downloads`. |

The main helpers are:

| Method | Behavior |
| --- | --- |
| `source(name, pin)` | Fetch and verify the pinned commit, create its local version tag, and copy top-level license notices. Returns the checkout path. |
| `environment()` | Return compiler, linker, CPU, LTO, and private dependency settings for the current target and tier. |
| `autotools(name, source, options=())` | Configure, build, and install into the private prefix. |
| `cmake(name, source, definitions)` | Configure with Ninja and build. Returns the build directory; does not run install. |
| `asset()` | Download the current target asset and verify SHA-256. Returns the cached file path. |
| `stage_binary(source, executable)` | Copy a file to the standard name for that logical executable and current tier; set executable mode. |
| `stage_binaries()` | Stage all declared executables from the private prefix's `bin/` directory. |
| `notices(source, name)` | Copy top-level `COPYING`, `LICENSE`, `NOTICE`, and `PATENTS` files into `licenses/<name>/`. |

Use `muxtools_binaries.io.run` for checked subprocesses and
`muxtools_binaries.io.extract` for supported imported archives. A custom recipe
must copy license files outside the locations handled by `notices()`.

For standard Autotools builds, re-export `build_autotools` as `build` and
`AutotoolsOptions` as `Options`. The helper builds dependencies in manifest order,
then builds and stages the main project for each CPU level.

## Option models

Subclass `muxtools_binaries.models.Model` to define `Options` or `UpdateOptions`.
This base model rejects unknown fields. Use defaults for optional values and
validators for related constraints.

In the build hook, read options with:

```python
options = recipe_options(ctx.package, ctx.target, Options)
```

In the check hook, use `recipe_options(package, target, Options)` when checks
depend on build choices. The loader validates options for every target.

See the [manifest reference](manifest.md#recipe-build-options) for merge behavior
and the options supported by current recipes.

## Check hook

Create a `CheckSuite` from `muxtools_binaries.checks`. `default_checks(package)`
creates a smoke command for every executable, using its manifest arguments.

### Smoke commands

`CheckSuite.smoke` maps each logical executable to a `CommandCheck`.
It must cover exactly the manifest's executables, with the same argument arrays.

| `CommandCheck` field | Default | Meaning |
| --- | --- | --- |
| `args` | `[]` | Arguments that must match `[executables]`. |
| `exit_codes` | `[0]` | Nonempty list of accepted process exit codes. |
| `stdout_prefix` | `""` | Required output prefix, when set. |
| `stdout_contains` | `[]` | Substrings that must appear in the captured output. |
| `timeout` | `60` | Timeout in seconds; greater than 0 and at most 600. |

For example, fdkaac's help command needs `exit_codes=[1]`.

### Functional checks

`CheckSuite.functional` contains `AudioCheck` or `VideoCheck` objects.
Both require `command`, `args`, and an alphanumeric output `suffix` without a dot.
The command must refer to a declared logical executable.

| Check | Additional fields | Input and result |
| --- | --- | --- |
| `AudioCheck` | `lossless=False` | Encode the audio fixture; decode and compare the result. The flag labels the mode; it does not change the quality threshold. |
| `VideoCheck` | `width=64`, `height=64`, `bit_depth=8` | Encode one generated YUV420 frame; require nonempty output. |

Video dimensions must be positive, even, and at most 4096. Bit depth must be
between 8 and 16. The recipe must choose values its encoder supports.

Arguments accept `{source}` and `{output}` placeholders. Video arguments also
accept `{width}`, `{height}`, and `{bit_depth}`. Other placeholders, format
specifiers, and conversions are rejected. Each check runs in a separate working
directory for every eligible executable variant.

### Required file formats

`CheckSuite.files` maps archive-relative paths to `elf`, `pe`, or `script`.
Use it for wrappers and private executable resources. For example:

```python
suite.files = {name: "script" for name in package.executables}
suite.files["MKVToolNix.AppImage"] = "elf"
```

Each declared file must exist. Undeclared executable files must match the target's
native format. Native library and ELF checks still apply to bundled native files.
Paths must be portable, relative, and free of parent-directory traversal.

See [Checks and tests](../architecture/testing.md) for execution behavior.

## Update hook

`UpdateContext` provides separate copies in `original` and `data`.
Return the proposed manifest dictionary from `discover_update(ctx)`.

| Helper | Behavior |
| --- | --- |
| `source_tags()` | Select newer stable numeric tags for the main source and dependencies; update the package version when pins change. |
| `pins_changed` | Compare source and dependency pins with the original manifest. |
| `get_json(url)` | Fetch JSON over HTTP. |
| `get_text(url)` | Fetch text over HTTP, such as an upstream HTML artifact listing. |
| `remote_hash(url)` | Download a remote file and calculate its SHA-256. |

Re-export `source_update` as `discover_update` when source-tag discovery is enough.
The fdkaac and Opus recipes extend it to form compound version strings from
their dependency versions. Import recipes select release assets and refresh hashes.

The framework prevents a package-name change, increments `version_code` when
the returned definition changes, and validates the result and its recipe checks.
The hook does not need to increment the counter itself. With `--apply`, discovery
writes changed values while preserving the existing TOML structure and comments.

Source discovery accepts tags such as `1.5.0` and `v1.5.0`. Other naming schemes
need custom discovery logic. See [update discovery](../architecture/releases.md#update-discovery)
for the scheduled workflow.
