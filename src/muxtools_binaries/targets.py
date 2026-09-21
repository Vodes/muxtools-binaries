from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

type OperatingSystem = Literal["linux", "windows", "macos"]
type Architecture = Literal["x86_64", "arm64"]
type BinaryFormat = Literal["elf", "pe", "macho"]
type ToolchainABI = Literal["native", "mingw-gcc", "mingw-ucrt", "msvc"]


@dataclass(frozen=True)
class BuilderSpec:
    name: str
    platform: str | None
    host_target: str


@dataclass(frozen=True)
class TargetSpec:
    name: str
    os: OperatingSystem
    arch: Architecture
    binary_format: BinaryFormat
    build_runner: str
    smoke_runner: str
    builder: str
    cpu_flags: Mapping[str, tuple[str, ...]]
    executable_suffix: str = ""
    elf_machine: int | None = None
    interpreter: str | None = None


@dataclass(frozen=True)
class ToolchainSpec:
    name: str
    target: str
    cc: str
    cxx: str
    ar: str
    ranlib: str
    nm: str
    linker: str
    family: Literal["gcc", "clang"]
    abi: ToolchainABI = "native"
    host: str | None = None
    sysroot: str | None = None
    windres: str | None = None
    mt: str | None = None
    compatibility_cc: str | None = None
    default_cflags: tuple[str, ...] = ()
    default_cxxflags: tuple[str, ...] = ()
    default_ldflags: tuple[str, ...] = ()
    rcflags: tuple[str, ...] = ()

    @property
    def clang(self) -> bool:
        return self.family == "clang"


X86_CPU_FLAGS = {
    "baseline": ("-march=x86-64-v2",),
    "avx2": ("-march=x86-64-v3",),
    "avx512": ("-march=x86-64-v4",),
    "zn4": ("-march=znver4", "-mno-sse4a", "-mno-avx512bf16"),
}
ARM64_CPU_FLAGS = {"baseline": ("-march=armv8-a",)}
APPLE_ARM64_CPU_FLAGS = {"baseline": ()}

BUILDERS = {
    "manylinux-x86_64": BuilderSpec("manylinux-x86_64", "linux/amd64", "linux-x86_64"),
    "manylinux-arm64": BuilderSpec("manylinux-arm64", "linux/arm64", "linux-arm64"),
    "native-macos-arm64": BuilderSpec("native-macos-arm64", None, "macos-arm64"),
}

TARGETS = {
    "linux-x86_64": TargetSpec(
        "linux-x86_64",
        "linux",
        "x86_64",
        "elf",
        "ubuntu-24.04",
        "ubuntu-24.04",
        "manylinux-x86_64",
        X86_CPU_FLAGS,
        elf_machine=62,
        interpreter="ld-linux-x86-64.so.2",
    ),
    "windows-x86_64": TargetSpec(
        "windows-x86_64",
        "windows",
        "x86_64",
        "pe",
        "ubuntu-24.04",
        "windows-2022",
        "manylinux-x86_64",
        X86_CPU_FLAGS,
        executable_suffix=".exe",
    ),
    "linux-arm64": TargetSpec(
        "linux-arm64",
        "linux",
        "arm64",
        "elf",
        "ubuntu-24.04-arm",
        "ubuntu-24.04-arm",
        "manylinux-arm64",
        ARM64_CPU_FLAGS,
        elf_machine=183,
        interpreter="ld-linux-aarch64.so.1",
    ),
    "windows-arm64": TargetSpec(
        "windows-arm64",
        "windows",
        "arm64",
        "pe",
        "ubuntu-24.04-arm",
        "windows-11-arm",
        "manylinux-arm64",
        ARM64_CPU_FLAGS,
        executable_suffix=".exe",
    ),
    "macos-arm64": TargetSpec(
        "macos-arm64",
        "macos",
        "arm64",
        "macho",
        "macos-15",
        "macos-15",
        "native-macos-arm64",
        APPLE_ARM64_CPU_FLAGS,
    ),
}


def _native_toolchains(target: str) -> dict[str, ToolchainSpec]:
    return {
        "gcc": ToolchainSpec(
            "gcc",
            target,
            "gcc",
            "g++",
            "gcc-ar",
            "gcc-ranlib",
            "gcc-nm",
            "ld",
            "gcc",
            default_ldflags=("-static-libstdc++", "-static-libgcc"),
        ),
        "clang": ToolchainSpec(
            "clang",
            target,
            "clang",
            "clang++",
            "llvm-ar",
            "llvm-ranlib",
            "llvm-nm",
            "ld.lld",
            "clang",
            compatibility_cc="gcc",
            default_ldflags=("-static-libstdc++", "-static-libgcc", "-fuse-ld=lld"),
        ),
    }


LLVM_MINGW_ROOT = "/opt/llvm-mingw"
XWIN_ROOT = "/opt/xwin"
XWIN_CRT = "14.44.17.14"
XWIN_SDK = "10.0.26100"


def _llvm_tool(name: str) -> str:
    return f"{LLVM_MINGW_ROOT}/bin/{name}"


def _llvm_mingw(target: str, triple: str) -> ToolchainSpec:
    sysroot = f"{LLVM_MINGW_ROOT}/{triple}"
    return ToolchainSpec(
        "clang",
        target,
        _llvm_tool(f"{triple}-clang"),
        _llvm_tool(f"{triple}-clang++"),
        _llvm_tool(f"{triple}-ar"),
        _llvm_tool(f"{triple}-ranlib"),
        _llvm_tool(f"{triple}-nm"),
        _llvm_tool(f"{triple}-ld"),
        "clang",
        abi="mingw-ucrt",
        host=triple,
        sysroot=sysroot,
        windres=_llvm_tool(f"{triple}-windres"),
        default_cflags=(f"--target={triple}", f"--sysroot={sysroot}"),
        default_ldflags=(
            "-static-libstdc++",
            "-static-libgcc",
            "-static",
            "-Wl,-Bstatic",
            "-pthread",
            "-fuse-ld=lld",
        ),
    )


def _msvc_paths(arch: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ms_arch = "arm64" if arch == "arm64" else "x64"
    include_root = f"{XWIN_ROOT}/WindowsKits/10/Include/{XWIN_SDK}"
    includes = (
        f"{XWIN_ROOT}/VC/Tools/MSVC/{XWIN_CRT}/include",
        f"{include_root}/ucrt",
        f"{include_root}/shared",
        f"{include_root}/um",
    )
    libraries = (
        f"{XWIN_ROOT}/VC/Tools/MSVC/{XWIN_CRT}/lib/{ms_arch}",
        f"{XWIN_ROOT}/WindowsKits/10/Lib/{XWIN_SDK}/ucrt/{ms_arch}",
        f"{XWIN_ROOT}/WindowsKits/10/Lib/{XWIN_SDK}/um/{ms_arch}",
    )
    return includes, libraries


def _clang_msvc(target: str, arch: str, triple: str) -> ToolchainSpec:
    includes, libraries = _msvc_paths(arch)
    include_flags = tuple(flag for path in includes for flag in ("-isystem", path))
    return ToolchainSpec(
        "clang-msvc",
        target,
        _llvm_tool("clang"),
        _llvm_tool("clang++"),
        _llvm_tool("llvm-ar"),
        _llvm_tool("llvm-ranlib"),
        _llvm_tool("llvm-nm"),
        _llvm_tool("lld-link"),
        "clang",
        abi="msvc",
        host=triple,
        sysroot=XWIN_ROOT,
        windres=_llvm_tool("llvm-windres"),
        mt="/usr/bin/llvm-mt",
        default_cflags=(f"--target={triple}", "-fms-runtime-lib=static", *include_flags),
        default_ldflags=("-fms-runtime-lib=static", "-fuse-ld=lld", *(f"-L{path}" for path in libraries)),
        rcflags=tuple(f"-I{path}" for path in includes),
    )


MINGW_TRIPLE = "x86_64-w64-mingw32"
ARM64_MINGW_TRIPLE = "aarch64-w64-mingw32"
TOOLCHAINS = {
    "linux-x86_64": _native_toolchains("linux-x86_64"),
    "linux-arm64": _native_toolchains("linux-arm64"),
    "windows-x86_64": {
        "gcc": ToolchainSpec(
            "gcc",
            "windows-x86_64",
            f"{MINGW_TRIPLE}-gcc",
            f"{MINGW_TRIPLE}-g++",
            f"{MINGW_TRIPLE}-gcc-ar",
            f"{MINGW_TRIPLE}-gcc-ranlib",
            f"{MINGW_TRIPLE}-gcc-nm",
            f"{MINGW_TRIPLE}-ld",
            "gcc",
            abi="mingw-gcc",
            host=MINGW_TRIPLE,
            windres=f"{MINGW_TRIPLE}-windres",
            default_ldflags=("-static-libstdc++", "-static-libgcc", "-static"),
        ),
        "clang": _llvm_mingw("windows-x86_64", MINGW_TRIPLE),
        "clang-msvc": _clang_msvc("windows-x86_64", "x86_64", "x86_64-pc-windows-msvc"),
    },
    "windows-arm64": {
        "clang": _llvm_mingw("windows-arm64", ARM64_MINGW_TRIPLE),
        "clang-msvc": _clang_msvc("windows-arm64", "arm64", "aarch64-pc-windows-msvc"),
    },
    "macos-arm64": {
        "clang": ToolchainSpec(
            "clang",
            "macos-arm64",
            "clang",
            "clang++",
            "ar",
            "ranlib",
            "nm",
            "ld",
            "clang",
        ),
    },
}


def target_spec(name: str) -> TargetSpec:
    try:
        return TARGETS[name]
    except KeyError as error:
        raise ValueError(f"Unknown target: {name}") from error


def toolchain_spec(target: str, name: str) -> ToolchainSpec:
    try:
        return TOOLCHAINS[target][name]
    except KeyError as error:
        raise ValueError(f"Unsupported toolchain {name} for {target}") from error


def normalize_arch(machine: str) -> str:
    aliases = {"amd64": "x86_64", "aarch64": "arm64"}
    return aliases.get(machine.casefold(), machine.casefold())


def normalize_os(system: str) -> str:
    aliases = {"darwin": "macos"}
    return aliases.get(system.casefold(), system.casefold())
