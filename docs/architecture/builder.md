# Builder and toolchains

Source builds run in architecture-specific Linux images. Windows builds cross-compile
with MinGW or the MSVC ABI. Linux CI hosts do not set the binary ABI baseline;
compilation stays in the builder.

| Target | `gcc` | `clang` | `clang-msvc` |
| --- | --- | --- | --- |
| `windows-x86_64` | Fedora MinGW-w64 | LLVM 22 llvm-mingw/UCRT | LLVM 22 with xwin/MSVC |
| `windows-arm64` | Unsupported | LLVM 22 llvm-mingw/UCRT | LLVM 22 with xwin/MSVC |

Architecture remains a target property. The toolchain name does not change between
x86-64 and ARM64.

## Image identity

`builder/Dockerfile` extends a digest-pinned manylinux_2_34 x86-64 or ARM64 image.
It installs compilers and utilities from AlmaLinux 9 repositories, including CRB
and EPEL.

The final image digest fixes the installed tool versions. Rebuilding the image
can install a newer toolchain and requires qualification again. The image records
its RPM inventory in `/opt/builder/packages.txt`. The lock also pins host-native
llvm-mingw and xwin archives, a frozen Visual Studio manifest, and exact SDK/CRT
versions. xwin installs only the Windows architecture matching its Linux builder.

`builder/lock.toml` contains:

| Field | Purpose |
| --- | --- |
| `schema_version` | Lock format marker, currently `3`. |
| `builders.<name>.base` | Digest-pinned manylinux base identity. |
| `builders.<name>.image` | Adopted derived image, in `registry/path@sha256:<digest>` form. |
| `toolchains.llvm-mingw` | LLVM 22 host archives and checksums. |
| `toolchains.xwin` | xwin archives, frozen manifest, SDK, CRT, and toolset pins. |

The target registry selects a builder entry. The build command reads its `image`
unless `--image` overrides it. Test CI builds from `base` while an image is empty;
release builds require an adopted registry digest.

## Qualify and adopt an image

1. Run **Qualify builder** and inspect both native Linux architectures and Windows results.
2. Enable its publish checkbox on `main` to push the tested images to GHCR.
3. Copy each resulting digest into its builder entry in `builder/lock.toml`.
4. Review and merge the lock change before using those images for releases.

## Compiler runtimes

Native GCC is the manylinux default. Native Linux Clang uses that GCC installation's
C++ headers and runtime. Windows `clang` is the self-contained llvm-mingw/UCRT
toolchain. `clang-msvc` uses normal `clang`/`clang++` argument syntax with the
pinned xwin headers and libraries.

The helpers normally link compiler runtimes statically. Windows GCC keeps its
existing fallback that bundles imported GCC, libstdc++, and winpthread DLLs.
llvm-mingw and MSVC runtime DLL imports are rejected. MinGW and MSVC builds use
different sysroots and private-prefix package discovery. All source-built Linux
executables must satisfy glibc 2.34.

Recipes using `clang-msvc` keep GCC-style Clang by default, including Autotools
recipes. A CMake recipe that specifically needs the cl-compatible frontend can
pass `clang_cl=True` to `BuildContext.cmake`. That local mode uses `clang-cl`,
`lld-link`, `llvm-lib`, `llvm-rc`, `llvm-mt`, and the static `/MT` runtime without
creating a separate toolchain identity.

Autotools normally receives the real MSVC host triple. If project-owned
`configure.ac` or `configure.in` checks `host_os` for MinGW without mentioning
MSVC, the helper supplies the equivalent MinGW host alias so the project selects
its Windows code path. Clang still receives the real MSVC target triple.

x265 and MKVToolNix keep libstdc++ static but use system `libgcc_s`. The
repository's static unwind library introduces `_dl_find_object@GLIBC_2.35`. The
dynamic libgcc choice avoids that requirement without imposing a new libstdc++
on consumers.

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

Windows adds `.exe` after the CPU suffix.
Linux and Windows ARM64 support only `baseline`, compiled with `-march=armv8-a`.
See [CPU eligibility](testing.md#cpu-eligibility) for the test runner's rules.

To add an operating system or architecture, extend the explicit target registry
and toolchain implementation. A new target table alone is not sufficient.

## Imported runtime requirements

Imported binaries retain their upstream ABI requirements. Declare any exceptions
in the [runtime table](../packages/manifest.md#runtime-table).

FFmpeg requires system `libgcc_s.so.1`.
