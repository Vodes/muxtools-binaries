# Builder and toolchains

All source builds run in one x86-64 Linux image, including Windows and macOS.
Linux CI hosts do not set the binary ABI baseline; compilation stays in the builder.

## Image identity

`builder/Dockerfile` extends a digest-pinned manylinux_2_34 x86-64 image.
It installs compilers and utilities from AlmaLinux 9 repositories, including CRB
and EPEL.

The final image digest fixes the installed tool versions. Rebuilding the image
can install a newer toolchain and requires qualification again. The image records
its RPM inventory in `/opt/builder/packages.txt`.

`builder/lock.toml` contains:

| Field | Purpose |
| --- | --- |
| `schema_version` | Lock format marker, currently `1`. |
| `base` | Digest-pinned manylinux base identity. Keep it aligned with the Dockerfile. |
| `image` | Adopted derived image, in `registry/path@sha256:<digest>` form. |

The build command reads `image` unless `--image` overrides it. It does not use
`base` to rewrite the Dockerfile. An empty `image` requires a local override;
test CI can build a temporary image. Release builds require a registry digest.

## Qualify and adopt an image

1. Run **Qualify builder** and inspect results from all six native runners.
2. Enable its publish checkbox on `main` to push the tested image to GHCR.
3. Copy the resulting digest from the workflow summary into `builder/lock.toml`.
4. Review and merge the lock change before using that image for releases.

Publication requires passing qualification on all six native runners, including
AlmaLinux 9 baseline checks for both Linux architectures.

## Targets and pinned toolchains

| Target | Compilers | Native runner |
| --- | --- | --- |
| `linux-x86_64` | `gcc`, `clang` | `ubuntu-24.04` |
| `linux-arm64` | `gcc`, `clang` | `ubuntu-24.04-arm` |
| `windows-x86_64` | `gcc`, `clang`, `clang-msvc` | `windows-2022` |
| `windows-arm64` | `clang`, `clang-msvc` | `windows-11-arm` |
| `macos-x86_64` | `clang` | `macos-15-intel` |
| `macos-arm64` | `clang` | `macos-15` |

The lock also records source revisions, download hashes, SDK/CRT versions, and
configuration paths. Linux ARM64 uses crosstool-NG 1.28.0, GCC 14.3.0, and glibc
2.34; Clang shares that sysroot and runtime. Windows MSVC-ABI builds use xwin
0.10.0 with a frozen Microsoft manifest, `clang-cl`, `lld-link`, and static release
CRT linkage. This compiler supports CMake; Autotools rejects it explicitly.

macOS uses pinned OSXCross with its LLVM flavor and supplied compiler/CMake
wrappers. Builder creation downloads and verifies the macOS 15.5 SDK. Deployment
minimum is 13.0, with system libc++. Binary inspection checks that minimum and
rejects non-system dependencies. Hosted macOS 15 tests do not verify execution
on macOS 13. There are no universal binaries or signing workflows.

## Compiler runtimes

Native GCC is the manylinux default. Clang uses that GCC installation's C++
headers and runtime. On Windows x86-64, MinGW GCC and Clang share the repository
MinGW sysroot and its default CRT. Windows ARM64 `clang` uses the pinned
llvm-mingw toolchain with UCRT, compiler-rt, and libc++; it supports both
Autotools and CMake. `clang-msvc` remains available through CMake with xwin.

The helpers normally link compiler runtimes statically. On Windows, the build
also copies required GCC, libstdc++, winpthread, libc++, and libunwind DLLs,
including dependencies of those DLLs, when they remain imported.
All source-built Linux executables must satisfy glibc 2.34.

x265 keeps libstdc++ static but uses system `libgcc_s`. The repository's static
unwind library introduces `_dl_find_object@GLIBC_2.35`. The dynamic libgcc choice
avoids that requirement without imposing a new libstdc++ on consumers.

Autotools receives CPU flags through compiler arguments so configure can retain
its optimization defaults. CMake receives target flags alongside upstream Release
settings.

## CPU levels

| Level | Compiler CPU arguments | Output suffix |
| --- | --- | --- |
| `baseline` | `-march=x86-64-v2` | None. |
| `avx2` | `-march=x86-64-v3` | `.avx2` |
| `avx512` | `-march=x86-64-v4` | `.avx512` |
| `zn4` | `-march=znver4 -mno-sse4a -mno-avx512bf16` | `.zn4` |

These are almost entirely copied from Vapoursynth's new plugin cpu level guidelines.<br>
See the [Vapoursynth R75 Release Blogpost](https://www.vapoursynth.com/2026/04/30/r75-sanding-of-the-r74-edges-and-plugin-manifests/) for details and reasoning.

ARM64 accepts only `baseline`, using generic ARMv8-A. Windows adds `.exe` after
the CPU suffix.
See [CPU eligibility](testing.md#cpu-eligibility) for the test runner's rules.

To add an operating system or architecture, extend the explicit target registry
and toolchain implementation. A new target table alone is not sufficient.

## Imported runtime requirements

Imported binaries retain their upstream ABI requirements. Declare any exceptions
in the [runtime table](../packages/manifest.md#runtime-table).

FFmpeg requires system `libgcc_s.so.1`. MKVToolNix's Linux AppImage requires
Bash, FUSE 2, and zlib. Its wrappers use Bash to select the requested command-line
tool inside the AppImage. They work independently of the caller's working directory.

On Ubuntu 24.04, install `libfuse2t64` before testing MKVToolNix.
