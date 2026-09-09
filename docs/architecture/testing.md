# Checks and tests

The pipeline checks staged files before packaging, then tests each archive on its
target operating system. Release publication requires matching native test reports.

## Structural checks

Build jobs check executable format, x86-64 architecture, metadata references,
required files, ELF symbol versions, and missing runtime libraries.
They reject unsafe paths and invalid archive layouts.

Declare wrappers and private executables in
[recipe checks](../packages/recipes.md#required-file-formats).

## Native tests

Linux jobs use Ubuntu 24.04; Windows jobs use Windows Server 2022.
The runner extracts each archive into a path that contains spaces. It invokes
every baseline executable and runs the stored functional checks.

Source-built Linux baselines also run in AlmaLinux 9 without the builder tools.

Tests execute the [checks stored in the archive](artifacts.md#metadata).
Release collection compares the stored checks and build options with the current
recipe and manifest.

## Audio and video checks

Audio checks encode `tests/data/audio/wav_source.wav` with every runnable encoder
variant. PyAV decodes the output, and Zimtohrli compares each channel at 48 kHz.
The runner rejects:

- Empty or undecodable output.
- A changed channel count or a duration difference over 100 ms.
- Silent output or a per-channel MOS below 4.5.

Scores appear in the test log. This is a coarse corruption check, not a codec
quality benchmark. WavPack runs in default lossless mode and in lossy mode with
`-b128`, targeting 128 kb/s without a correction file.
Both modes use the same quality check, without a sample-equality assertion.

Video checks generate one YUV420 frame and require nonempty encoded output.
x265 derives checks for each configured bit depth.

## CPU eligibility

Optimized variants require the complete CPU-tier feature set and OS-enabled
vector state. The runner uses py-cpuinfo2 for CPU features and an OS check for
enabled vector state. If detection is unavailable, optimized variants are skipped.
Baseline checks remain mandatory.

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
