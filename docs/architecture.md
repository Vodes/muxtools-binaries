# Binary pipeline

Package definitions are desired build state. The public catalog describes only published releases.
Builds never use the catalog to decide whether to run.

## Builder

### Image and qualification

`builder/Dockerfile` extends a digest-pinned manylinux_2_34 x86-64 image.
Compilers and utilities are installed from its AlmaLinux 9 repositories, including CRB and EPEL.
There are no individual tool-version pins or compiler source builds.

The final derived image digest freezes the installed tools.
Rebuilding the image can produce a new toolchain and requires qualification again.
The image includes an RPM inventory at `/opt/builder/packages.txt`.

`builder/lock.toml` holds the base and adopted derived image identities.
The derived image starts empty until a published image is adopted.

- Local test builds accept `--image muxtools-builder:local`.
- Release builds require a registry digest.

Run the **Qualify builder** workflow and inspect its Linux and native Windows results.
Explicitly enable its publish checkbox to push to GHCR, then adopt the resulting digest
in `builder/lock.toml` through normal review.

### Compiler runtimes

The native GCC installation is the manylinux default.
Clang uses that GCC installation's C++ headers and runtime rather than automatically
selecting a different installed GCC.

Windows GCC and Clang share the repository MinGW sysroot and its default CRT.
No UCRT variant is assumed.

## Recipes and source state

Each directory under `packages/` has `package.toml` and `recipe.py`.
The recipe exposes `build(ctx)` and populates `ctx.stage`. It may perform arbitrary producer-side work.

`BuildContext` supplies checked processes, verified downloads, source checkout,
isolated dependency prefixes, and target flags.
Add a target by extending the explicit target registry and toolchain implementation,
not by treating every non-Windows system as Linux.

### Manifest fields

The manifest contains:

- `schema_version = 1`, package `name`, upstream `version`, integer `version_code >= 1`, and provenance `type`.
- Source/dependency repository, tag, and resolved commit for source builds.
- Target compiler, `lto = false | "full" | "thin"`, ordered CPU levels,
  and deliberate `extra_cflags`, `extra_cxxflags`, `extra_ldflags`.
- An HTTPS URL, SHA-256, and archive format for each imported target.
- An `executables` table mapping logical executable names to smoke-test arguments.
- Optional runtime requirements and explicit reasons for imported glibc exceptions.

### Version identity

Versions of bundled dependencies are recorded separately in artifact metadata.
fdkaac and Opus tools retain compound version names.

`version_code` is a monotonically increasing counter per package, so consumers can select
the newest published entry without parsing tool-specific version strings.

- Update discovery increments it for each change and never resets it on a new upstream version.
- Manual updates must also advance it.
- It is recorded in metadata and the catalog, not filenames, release tags, or catalog keys.

Published `(name, version)` identities remain immutable.
Increasing `version_code` alone does not create a separate release or authorize replacing an existing one;
publishing an existing version skips it without comparing the rebuilt archive's checksum.
Use a new package version such as `4.2.post1` and advance `version_code` to publish a changed build of the same source.
Update discovery preserves that package version until its source or dependency pins change.
This also protects historical releases whose tags already use the same naming scheme.

### Source builds and dependencies

Source fetches verify the commit before creating a local version tag.
This lets upstream build systems identify releases even in shallow checkouts.

Autotools receives CPU requirements through compiler arguments, preserving configure's optimization defaults.
CMake receives target flags alongside upstream Release settings.

Dependencies are built privately for each target/tier:

- The audio recipes build FDK, Ogg, Opus, Opusfile, libopusenc, FLAC and WavPack as required.
- Opus tools includes FLAC input support.
- x265 combines its 8-, 10-, and 12-bit builds into each tier executable.

Linux source builds must satisfy glibc 2.34. Compiler runtimes are normally static.

x265 keeps libstdc++ static but uses system libgcc_s:
current repository static unwind libraries introduce `_dl_find_object@GLIBC_2.35`.
This exception avoids imposing a new libstdc++ on consumers.
The emitted ELF symbol check remains mandatory.

## Artifact contract

### Archive layout

Names follow `{name}-{version}-{target}.tar.zst`.
Archives extract directly into their installation directory and contain `.metadata.toml`,
executables, licenses, and any private runtime resources.

- Members are sorted.
- Ownership is normalized; file and directory modification times are retained.
- Executable modes are retained.
- Zstandard compression uses level 19.

Imported archives retain their upstream modification times, including ZIP and 7z imports.
Build and packaging times can differ between runs, so identical binaries can produce different archive checksums.

### Metadata and executable variants

Metadata schema version 1 records identity, explicit target, source/dependency pins, provenance,
test/release channel, builder image/repository revision, and source compiler/linker versions.

Build settings record deliberate extras rather than an exhaustive effective-flag dump.
CI logs retain commands and diagnostics.

Executable mappings are per logical tool:

```toml
[binaries.x265]
baseline = "x265.exe"
avx2 = "x265.avx2.exe"
avx512 = "x265.avx512.exe"
zn4 = "x265.zn4.exe"
```

`baseline` targets x86-64-v2; the other levels use v3, v4, and `znver4 -mno-sse4a -mno-avx512bf16`.
Every requested variant travels in one archive.
Consumers choose variants; archives contain no CPU dispatcher or lifecycle hooks.

### Imported runtime requirements

MKVToolNix's AppImage dispatches on `argv[0]`, not its first argument.
Its permanent POSIX wrappers invoke Bash to set that value to the requested command.
They work independently of the caller's working directory and do not modify the installation.

The upstream AppImage requires Bash, FUSE 2, and zlib.
Smoke tests check the actual tool's version output so accidentally launching the GUI cannot pass.

Imported FFmpeg requires system libgcc_s.
Importing an executable does not change its ABI requirements.
A higher glibc requirement must be declared explicitly in the target's runtime table,
with an explanation, and appears in metadata and the catalog.

## Testing

Linux jobs run on Ubuntu 24.04; compilation stays inside the pinned builder,
so the host runner does not determine the binary ABI baseline.

### Build and native checks

Build jobs validate executable format, x86-64 architecture, metadata references, required files,
ELF versions, and missing runtime libraries.

Native tests download and extract archives into a path containing spaces, invoke every baseline,
and run small functional encodes.
Source-built Linux baselines additionally launch in AlmaLinux 9 without the builder toolbox.

### CPU eligibility

[py-cpuinfo2](https://github.com/akx/py-cpuinfo2) supplies CPU feature detection on the native runner;
no compiled probe is built or shipped.
Optimized tests still require the complete tier predicate and OS-enabled vector state.

Because the library merges hardware and OS information, a small OS check uses Linux's enabled AVX flags
or Windows's `GetEnabledXStateFeatures`.
Unavailable detection skips optimized variants; baselines remain mandatory.
This does not provide performance guarantees.

### Unit coverage

The unit suite uses synthetic packages rather than current binary versions or checksums.
It focuses on archive safety/round-tripping, CPU eligibility, runtime-linking overrides,
release gates/integrity, and update selection.

Real builds and native smoke tests cover package-specific behavior.
HTTP operations use httpx2; upload tests use its mock transport instead of a simulated GitHub service.

## Publishing

Normal CI has read-only repository permissions.
Publishing requires **workflow_dispatch + main + publish=true**, with the checkbox off by default.
The publisher also enforces that gate in Python.

A package needs every declared target and matching native smoke reports before any upload.
Each package release is assembled and verified as a draft, then published.

- Versions already in the catalog are skipped, retaining their original download metadata and checksums.
- Published releases missing from the catalog are reused; catalog entries are recovered from their published archives.
  Historical releases without the current archive metadata are skipped without adding a catalog entry.
- Draft assets are replaced on retry so all targets and checksums come from the current verified build.
- Checksums still verify new uploads and downloads; they do not decide whether a version is already published.
- No release or tag is deleted or moved.

### Version catalog

The dedicated `catalog-v1` release hosts `versions.json`. Its schema is:

```json
{
  "schema_version": 1,
  "packages": {
    "x265": {
      "provides": ["x265"],
      "versions": {
        "4.2": {
          "version": "4.2",
          "version_code": 1,
          "tag": "x265-4.2",
          "targets": {
            "linux-x86_64": {
              "url": "...",
              "sha256": "...",
              "size": 123,
              "binaries": {
                "x265": {
                  "baseline": "x265"
                }
              },
              "runtime": {}
            }
          }
        }
      }
    }
  }
}
```

Each package has a `provides` list of logical executable names from its newest published version
(highest `version_code`), for example `["flac", "metaflac"]`.
It excludes platform extensions and CPU suffixes.

Version entries live under `versions`; their per-target `binaries` mappings remain authoritative
for exact files and historical versions.

### Catalog recovery

Versioned catalog snapshots are uploaded before replacing the current pointer.
GitHub asset replacement is not atomic: consumers should retry a transient 404.

If pointer replacement fails, rerun the same manual release.
Completed package releases are reused and the latest snapshot recovers catalog state.

Publishing is serialized to prevent concurrent catalog updates.
Catalog publication makes no source-branch commits.

After `versions.json` is verified and the catalog release is published, older snapshots are deleted.
Only the newest snapshot is retained for recovery. Failed pointer updates leave all snapshots intact;
rerunning publication also retries cleanup, even if the pointer already has the expected contents.

## Update discovery

Scheduled discovery opens or updates one PR branch per package and dispatches a test-only build.
It never publishes binaries. The repository must allow Actions to create pull requests.

Source-tag ordering is stable numeric releases only;
ambiguous imported asset matches fail for maintainer review.

## Compatibility and future work

Historical ZIP releases remain untouched.
This schema and `.tar.zst` format intentionally require consumer changes;
the old repository catalog is not the new API.

qaac, ARM64, macOS, PGO, and benchmarks remain future work.
