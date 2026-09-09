# Archives and catalog

Each target produces `{name}-{version}-{target}.tar.zst` and a matching
`.sha256` file. Linux and Windows use the same archive format.

## Archive layout

Archives extract directly into the installation directory. They contain
`.metadata.toml`, executables, licenses, and any private runtime resources.

```text
x265.exe
x265.avx2.exe
x265.avx512.exe
x265.zn4.exe
licenses/
.metadata.toml
```

Archive members are sorted. Ownership is normalized. Files use mode `0755` when
executable and `0644` otherwise; directories use `0755`. File and directory
modification times are retained. Compression uses Zstandard level 19.

Imported files retain upstream modification times, including ZIP and 7z inputs.
Build and packaging times can differ between runs, so identical binaries can
produce different archive checksums.

## Metadata

New archives use metadata schema 2. Package manifests and the public catalog
remain on schema 1.

| Metadata field | Contents |
| --- | --- |
| `schema_version` | `2` for newly built archives. |
| `name`, `version`, `version_code`, `target` | Package identity and explicit target. |
| `binaries` | Logical executable names mapped to CPU variants and archive paths. |
| `smoke` | Smoke arguments from the manifest. |
| `checks` | Declarative smoke, functional, and required-file checks. |
| `provenance` | Build type, test or release channel, optional provider, and imported asset details. |
| `builder` | Builder image and repository revision. |
| `source`, `dependencies` | Source pins, when present. |
| `build` | Common and target recipe options; source builds also record compiler, linker, versions, CPU levels, LTO, and extra flags. |
| `runtime` | Linux requirements and exceptions, when declared. |

`build.options` and `build.target_options` retain the separate manifest tables.
They are not a dump of the merged options or every effective compiler flag.
CI logs retain the executed commands and diagnostics.

Packaging generates `.metadata.toml`, formats and lints it offline, then writes
the archive. Native archive testing reads its stored check definitions without
loading the current recipe. Schema 1 archives remain readable for catalog
recovery; rebuild them for native testing and new publication.

### Executable variants

For example, a Windows x265 archive records:

```toml
[binaries.x265]
baseline = "x265.exe"
avx2 = "x265.avx2.exe"
avx512 = "x265.avx512.exe"
zn4 = "x265.zn4.exe"
```

All requested CPU variants travel together. Consumers choose the variant.
See [CPU levels](builder.md#cpu-levels) for their compiler settings.

## Version catalog

The dedicated `catalog-v1` GitHub release hosts `versions.json`. This example
shows its structure; the URL, checksum, and size are illustrative:

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
              "url": "https://example.com/x265-4.2-linux-x86_64.tar.zst",
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

Each package's `provides` list contains the logical executable names from its
newest published version, selected by the highest `version_code`. It excludes
platform extensions and CPU suffixes. Per-target `binaries` mappings remain
authoritative for exact filenames and historical versions.

Consumers should select versions by `version_code`, then use the target's URL,
checksum, runtime requirements, and executable mapping. Package versions can
include dependency versions or build suffixes and need not share one ordering scheme.

Catalog replacement is not atomic. Retry a transient 404 while `versions.json`
is replaced. See [catalog recovery](releases.md#catalog-recovery) for maintainer steps.

The catalog records only published releases. Building a version does not add it
to the catalog. Historical ZIP releases and the old repository catalog remain
separate from this contract.
