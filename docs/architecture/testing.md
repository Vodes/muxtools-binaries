# Checks and tests

The pipeline checks staged files before packaging, then tests each archive on its
exact target operating system and architecture. Release publication requires version 2
completion reports with both native execution and comparison evidence.

## Structural checks

Portable checks verify file format and CPU architecture, including bundled
libraries and optimized variants that native tests may skip.
Builder runtime audits check Linux loaders and glibc, Windows runtime imports,
and macOS deployment versions and system dependencies.
They reject unsafe paths and invalid archive layouts.

Declare wrappers and private executables in
[recipe checks](../packages/recipes.md#required-file-formats).

## Native tests

The target registry selects Ubuntu 24.04, Windows Server 2022, Windows 11 ARM,
and macOS 15 runners with the matching architecture.
The runner extracts each archive into a path that contains spaces. It invokes
every baseline executable and runs the recipe's functional checks.

Source-built Linux baselines also run in architecture-matched AlmaLinux 9
containers without the builder tools. No emulation is used.

Tests load checks from the repository's matching manifest and recipe. Use the
checkout recorded in `builder.revision` when testing an older download.
Release collection validates artifact provenance, build options, and completion reports.

## Audio and video checks

Audio checks encode `tests/data/audio/wav_source.wav` with every runnable encoder
variant on its native runner. One aggregate `ubuntu-slim` job decodes all outputs
with PyAV and compares each channel at 48 kHz with Zimtohrli. Python is pinned
to 3.12; comparison uses the locked `comparison` dependency group. It rejects:

- Empty or undecodable output.
- A changed channel count or a duration difference over 100 ms.
- Silent output or a per-channel MOS below 4.5.

Scores appear in the test log. This is a coarse corruption check, not a codec
quality benchmark. WavPack runs in default lossless mode and in lossy mode with
`-b128`, targeting 128 kb/s without a correction file.
Both modes use the same quality check, without a sample-equality assertion.

Video checks generate one YUV420 frame and require nonempty encoded output.
x265 derives checks for each configured bit depth.

## Deferred comparison

On the matching native runner:

```sh
uv run --frozen --no-default-groups muxtools-build test ARCHIVE --results native-results
```

Collect the archives and native result directories together, then compare:

```sh
uv run --frozen --no-default-groups --group comparison muxtools-build compare --artifacts artifacts --reports reports
```

Comparison validates native results against the archives and matching recipe
checkout, then checks audio quality and writes completion reports.

`test ARCHIVE` performs both phases locally using the same helpers. Add `--report`
to save its completion report. Native-only environments need core dependencies;
local combined tests also need the `comparison` group. Build tools use the `build`
group; the default development group includes both.

## CPU eligibility

Optimized variants require the complete CPU-tier feature set and OS-enabled
vector state. The runner uses py-cpuinfo2 for CPU features and an OS check for
enabled vector state. If detection is unavailable, optimized variants are skipped.
Baseline checks remain mandatory. ARM64 supports only generic ARMv8-A and bypasses
x86 feature probing. macOS feature detection may conservatively skip optimized tiers.

## Unit tests and CI selection

Run `uv run pytest -q` for shared tests in `tests/` and package tests in
`packages/<name>/tests/`.

On PRs, build selection compares against the PR base. On pushes to `main`, it
compares against the previous branch commit. Changes inside one package select
that package. Shared source, tests, builder files, binary workflows, or dependency
configuration select all packages. If the previous push commit is unavailable,
CI builds all packages.

Documentation pages, `zensical.toml`, the docs workflow, and editor settings do
not select binary builds. Changes to shared `pyproject.toml` or `uv.lock` still
select all packages. Manual runs build all packages unless the `packages` input
selects specific names.
