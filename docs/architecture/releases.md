# Releases and updates

PR builds create test artifacts. After a merge, **Build and test** skips repeat
checks and test builds when a successful PR or update-discovery run covered the
same Git tree and package targets. Direct pushes and merges without matching
validation run the regular checks and test builds.

## Version identity

A published package is identified by `(name, version)`. That identity is immutable.
The archive name and release tag include the version. `version_code` is stored
in metadata and the catalog, but is not part of archive names, release tags, or
catalog keys.

Use `version_code` as a monotonically increasing package counter. Update discovery
increments it when the definition changes. Manual updates must also increase it.
Do not reset the counter for a new upstream version.

To publish a changed build of the same upstream source, use a new package version
such as `4.2.post1` and increase `version_code`. Increasing only the counter does
not permit replacing an existing version. Publishing an existing version skips
it without comparing the rebuilt archive's checksum.

Source update discovery preserves a package version such as `4.2.post1` until
source or dependency pins change. fdkaac and Opus tools use compound package
versions that also identify their principal dependency. All dependency pins are
recorded separately in archive metadata.

## Publish packages

Before the first container-backed release, [qualify and adopt a builder](builder.md#qualify-and-adopt-an-image).
Its image must be available by registry digest. Native macOS releases instead
require the recorded repository revision and native builder identity.

To publish automatically, add the `release` label before merging a PR into
`main`. Keep the label in place until **Build and test** reads it on the push.
When a push can be attributed to that PR alone, the workflow builds and publishes
new packages and packages with both a changed `version` and a higher
`version_code`. Release builds and smoke tests use the merged commit. Direct
pushes and pushes spanning multiple PRs use the manual release path.

### Manual publishing

1. Merge the package changes and adopted builder digest into `main`.
2. Run **Build and test** manually on `main`.
3. Set `packages` to a comma-separated list, or leave it empty to build all packages.
4. Enable **publish**.
5. Inspect the build, native test, and publication results.

The publish checkbox defaults to off. The Python publisher requires an explicitly
enabled push or workflow dispatch on `main`. Build and test jobs use read-only
repository permissions. The publish job writes releases using release artifacts
from the same run.

Every declared target needs an archive and a matching native smoke report before
the package can upload. The publisher assembles and verifies each new package
release as a draft, then publishes it.

## Retries and existing releases

- Versions already in the catalog are skipped. Their URLs and checksums are retained.
- Published releases missing from the catalog are reused. Their archives supply the recovered metadata.
- Historical releases without supported archive metadata are left out of the new catalog.
- Draft assets are replaced on retry so all targets and checksums come from the current verified build.
- Checksums verify new uploads and downloads; they do not decide whether a version is already published.

The publisher does not delete or move releases or tags. Publication is serialized
to prevent concurrent catalog updates. It makes no source-branch commits.

## Catalog recovery

The publisher uploads a versioned catalog snapshot before replacing the current
`versions.json` asset on the `catalog-v1` release. GitHub asset replacement is
not atomic, so consumers can briefly receive a 404.

If replacement fails, rerun the release job. Completed package releases
are reused, and the latest snapshot restores catalog state.

After `versions.json` is verified and the catalog release is published, the
publisher removes older snapshots. It retains the newest snapshot for recovery.
Failed pointer updates leave all snapshots intact. A retry also repeats cleanup,
even if the current `versions.json` already has the expected contents.

## Update discovery

**Discover updates** runs on a schedule and can also run manually. It discovers
package directories, opens or updates one PR branch per package, and dispatches
a test-only build. The repository must allow GitHub Actions to create pull requests.

Generic source discovery selects stable numeric tags. Import recipes apply their
own upstream naming and asset rules. Ambiguous asset matches fail for maintainer
review. Discovery validates the updated definition and formats and lints the
manifest before opening the PR.

To inspect updates locally without writing changes:

```sh
uv run muxtools-build updates --package flac
```

Add `--apply` to update the manifest, then run the development checks.
See [update hooks](../packages/recipes.md#update-hook) for recipe behavior.

To fill missing archive-source and imported-asset SHA-256 values, run
`uv run muxtools-build hashes` with the package names
you want to update.<br> 
It writes the hashes immediately, including absent or empty
values.<br>
Add `--refresh` to download and recalculate existing hashes too. The
command leaves `version_code` unchanged; increase it when preparing a changed
release definition.
