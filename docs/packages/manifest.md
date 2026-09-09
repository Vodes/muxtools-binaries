# Manifest reference

Each package has one `packages/<name>/package.toml`. The shared models in
`src/muxtools_binaries/models.py` define its structure. Recipe models define the
contents of `[build]` and `[update]`. Unknown fields are rejected, except for keys
inside dictionaries that a model explicitly allows, such as x265's CMake definitions.

Run `uv run muxtools-build validate` after an edit. It validates all manifests,
merged recipe options, and check definitions. Build-system options still need a
real build to confirm that upstream accepts them.

## Top-level fields

Put scalar fields before the first TOML table header. Package manifests use
schema 1; [archive metadata](../architecture/artifacts.md#metadata) uses schema 2.

| Field | Type | Required or default | Meaning and limits |
| --- | --- | --- | --- |
| `schema_version` | Integer | `1` | Only manifest schema 1 is accepted. Write it explicitly. |
| `name` | String | Required | Lowercase letter, followed by lowercase letters, digits, or hyphens. Must match the directory name. |
| `description` | String | `""` | Free-form text for package-manager search results and package details. |
| `version` | String | Required | Release identity. Starts with a letter or digit; remaining characters can also include `.`, `+`, `_`, and `-`. |
| `version_code` | Integer | Required | At least 1. Increase for each new release definition; never reset for a new upstream version. Booleans and strings are rejected. |
| `type` | String | Required | `source-build`, `external-build`, or `upstream-binary`. |
| `provider` | String | `""` | Provenance label for an imported build, such as `Vodes/FFmpeg-Builds`. It does not select an update source. |
| `source` | Table | Required for source builds | Main source pin. See below. |
| `dependencies` | Tables | Empty | Named source pins. Names use letters, digits, `_`, and `-`. |
| `targets` | Tables | Required, nonempty | Build targets and settings. |
| `executables` | Table | Required, nonempty | Logical executable names and smoke arguments. |
| `build` | Table | Empty | Common recipe options, validated by `Options`. |
| `update` | Table | Empty | Update settings, validated by `UpdateOptions`. |

Use upstream's short description when it fits the package. The text is carried
into archive metadata and the catalog for package managers to display.

`type` records provenance and applies validation rules. It does not choose a
recipe or build system. Both import types require a target asset and a
baseline-only layout. Source builds require a source pin and reject target assets.

The `version` can differ from the source tag. For example, a new build of source
tag `4.2` can use package version `4.2.post1`. Changing only `version_code` does
not create a new publishable identity. See [version identity](../architecture/releases.md#version-identity).

## Source and dependency pins

`[source]` and each `[dependencies.<name>]` use the same fields. All three are required.

| Field | Type | Constraint |
| --- | --- | --- |
| `repository` | String | Starts with `https://`. |
| `tag` | String | Starts with a letter or digit. Remaining characters can also include `.`, `_`, `/`, and `-`. |
| `commit` | String | Exactly 40 lowercase hexadecimal characters. |

```toml
[source]
repository = "https://github.com/xiph/flac.git"
tag = "1.5.0"
commit = "1507800de4b70e21be71f38caa0d9079d0bc6e45"

[dependencies.ogg]
repository = "https://github.com/xiph/ogg.git"
tag = "v1.3.6"
commit = "be05b13e98b048f0b5a0f5fa8ce514d56db5f822"
```

The build fetches and verifies the commit, then creates the local tag for upstream
version detection. The commit selects the source; the tag is not a floating build input.
Autotools dependencies build in manifest order into a private prefix for each
target and CPU level. A custom recipe controls its own dependency steps.

## Target settings

The registered target names are `linux-x86_64` and `windows-x86_64`. There is no
top-level target-default table. Declare each target separately.

| Field in `[targets.<target>]` | Type | Default | Meaning and limits |
| --- | --- | --- | --- |
| `compiler` | String | `"gcc"` | `gcc` or `clang`. Used by source-build helpers. |
| `lto` | Boolean false or string | `false` | `false`, `"full"`, or `"thin"`. Thin LTO requires Clang. `true` is not accepted. |
| `cpu_levels` | String array | `["baseline"]` | Unique entries, with `baseline` first. Other values: `avx2`, `avx512`, `zn4`. Imports must use only `baseline`. |
| `extra_cflags` | String array | `[]` | Extra C compiler arguments. |
| `extra_cxxflags` | String array | `[]` | Extra C++ compiler arguments. |
| `extra_ldflags` | String array | `[]` | Extra linker arguments. |
| `runtime` | Table | Defaults below | Linux runtime requirements and exceptions. |
| `asset` | Table | Absent | Required for imports; forbidden for source builds. |
| `build` | Table | Empty | Overrides for the common recipe options. |

Use one argument per array element, for example `extra_cflags = ["-DFLAC__NO_DLL"]`.
The helpers add toolchain, CPU, LTO, and runtime-linking flags. Compiler settings
do not rebuild an imported binary or change its ABI.

```toml
[targets.linux-x86_64]
compiler = "clang"
lto = "thin"
cpu_levels = ["baseline", "avx2", "avx512", "zn4"]
extra_ldflags = ["-shared-libgcc", "-Wl,--strip-debug"]
```

`-shared-libgcc` also suppresses the helper's default `-static-libgcc` flag.
See [compiler runtimes](../architecture/builder.md#compiler-runtimes) before changing this choice.

### Runtime table

| Field in `[targets.<target>.runtime]` | Type | Default | Meaning |
| --- | --- | --- | --- |
| `glibc` | String | `"2.34"` | Version in `major.minor` form. Linux source builds must use `2.34`. |
| `requirements` | String array | `[]` | System shared libraries that the package needs, such as `libgcc_s.so.1`. |
| `exception` | String | `""` | Explanation required whenever `glibc` differs from `2.34`. |

For example, an imported Linux binary with a higher minimum can declare:

```toml
[targets.linux-x86_64.runtime]
glibc = "2.35"
requirements = ["libgcc_s.so.1"]
exception = "The imported executable requires GLIBC_2.35 symbols."
```

Declare the measured requirement. This table does not install libraries or change
the binary. Linux runtime exceptions and requirements are recorded in metadata
and the catalog. Windows metadata does not emit this runtime table.

### Imported asset table

| Field in `[targets.<target>.asset]` | Type | Constraint |
| --- | --- | --- |
| `url` | String | Required HTTPS download URL. |
| `sha256` | String | Required; exactly 64 lowercase hexadecimal characters. |
| `format` | String | Required; `zip`, `tar.xz`, `7z`, or `appimage`. |

Each target has one asset. Its recipe selects files, preserves licenses, and
stages runtime resources. AppImages need recipe-specific handling; declaring
`format = "appimage"` does not create wrappers automatically.
See `packages/ffmpeg/package.toml` and `packages/mkvtoolnix/recipe.py` for examples.

## Executables

```toml
[executables]
flac = ["--version"]
metaflac = ["--version"]
```

Keys use letters, digits, `_`, and `-`. Do not include paths, `.exe`, or CPU suffixes.
Each value is an array of command arguments; an empty array is allowed.
The recipe's smoke arguments must match this table exactly for every target.
Exit codes, output checks, timeouts, and functional tests belong in Python.

The framework derives names such as `x265`, `x265.avx2`, `x265.exe`, and
`x265.avx2.exe`. Every executable gets every requested CPU level in the archive.
TOML cannot specify arbitrary output filenames or per-executable CPU levels.

## Recipe build options

The recipe must expose an `Options` model to accept nonempty build tables.
Without that model, only empty `[build]` and `[targets.<target>.build]` tables are valid.
Build procedures, including shell commands, patches, and environment changes,
belong in `recipe.py`.

### Target overrides

`recipe_options(package, target, Options)` merges the target build table into
the common build table, then validates the result:

- Nested tables merge recursively by key.
- Arrays and scalar values replace the common value.

For the x265 recipe:

```toml
[build]
bit_depths = [8, 10, 12]

[build.cmake]
CMAKE_BUILD_TYPE = "Release"
CMAKE_POLICY_VERSION_MINIMUM = "3.5"
ENABLE_SHARED = false
ENABLE_ASSEMBLY = true
ENABLE_LIBNUMA = false

[targets.windows-x86_64.build]
bit_depths = [8, 10]

[targets.windows-x86_64.build.cmake]
ENABLE_ASSEMBLY = false
```

Windows replaces `ENABLE_ASSEMBLY` and uses only the 8- and 10-bit depths.
It keeps the other CMake settings. Other targets use the common values.
There is no TOML null value to delete an inherited key. An empty nested table
does not clear its inherited entries; an empty array replaces an inherited array.

### Autotools: fdkaac, FLAC, Opus tools, and WavPack

These recipes expose `AutotoolsOptions` as `Options`.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `build.configure` | String array | `[]` | Extra configure arguments for the main source. |
| `build.dependencies.<name>` | String array | `[]` per dependency | Extra configure arguments for a named dependency. |

```toml
[build]
configure = ["--disable-doxygen-docs", "--disable-cpplibs"]

[build.dependencies]
ogg = []
```

Dependency option names must refer to `[dependencies]` entries.
TOML validation checks the argument types, not whether upstream accepts the flags.

### x265

| Field | Type | Requirement |
| --- | --- | --- |
| `build.bit_depths` | Integer array | Required. Unique values from `8`, `10`, and `12`; must include `8`. |
| `build.cmake` | Table | Required. CMake definitions with string, boolean, or integer values. |

See the [target override example](#target-overrides) above.

The recipe reserves `HIGH_BIT_DEPTH`, `MAIN12`, `EXPORT_C_API`, `ENABLE_CLI`,
`LINKED_10BIT`, `LINKED_12BIT`, and `EXTRA_LIB`. Set `bit_depths` instead of these
keys. The recipe derives the multilib links and video checks from the selected depths.

The CMake helper translates booleans to `ON` and `OFF`. It supplies the install
prefix, compilers, archiver, ranlib, and executable linker flags. For Windows,
it also supplies the system name, processor, resource compiler, and link-dependency
setting. Those helper-owned values override definitions from `build.cmake`.
Other definition names pass through to CMake; schema validation does not catch
misspelled upstream CMake variables.

### FFmpeg and MKVToolNix

These import recipes have no `Options` model. Their build tables must be empty.
Change their target assets to select inputs. Change `recipe.py` to alter extraction,
file selection, or wrappers.

## Recipe update options

There are no target-specific update tables. The recipe's `UpdateOptions` model
validates the common `[update]` table.

| Recipe | Accepted settings |
| --- | --- |
| FFmpeg | Required `repository` string, in GitHub `owner/repository` form. |
| All other current recipes | Empty table only. Their source pins or recipe logic determine discovery. |

```toml
[update]
repository = "Vodes/FFmpeg-Builds"
```

For another option, define an `UpdateOptions` model and read it in
`discover_update(ctx)`. See [update hooks](recipes.md#update-hook).
