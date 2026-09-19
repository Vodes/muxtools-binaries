from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

type OperatingSystem = Literal["linux", "windows", "macos"]
type Architecture = Literal["x86_64", "arm64"]
type BinaryFormat = Literal["elf", "pe", "macho"]


@dataclass(frozen=True)
class BuilderSpec:
    name: str
    platform: str
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
    host: str | None = None
    windres: str | None = None
    compatibility_cc: str | None = None
    compatibility_cxx: str | None = None
    runtime_flags: tuple[str, ...] = ()
    link_flags: tuple[str, ...] = ()

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

BUILDERS = {
    "manylinux-x86_64": BuilderSpec("manylinux-x86_64", "linux/amd64", "linux-x86_64"),
    "manylinux-arm64": BuilderSpec("manylinux-arm64", "linux/arm64", "linux-arm64"),
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
            runtime_flags=("-static-libstdc++", "-static-libgcc"),
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
            compatibility_cxx="g++",
            runtime_flags=("-static-libstdc++", "-static-libgcc"),
        ),
    }


MINGW_TRIPLE = "x86_64-w64-mingw32"
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
            host=MINGW_TRIPLE,
            windres=f"{MINGW_TRIPLE}-windres",
            runtime_flags=("-static-libstdc++", "-static-libgcc"),
            link_flags=("-static",),
        ),
        "clang": ToolchainSpec(
            "clang",
            "windows-x86_64",
            "clang",
            "clang++",
            "llvm-ar",
            "llvm-ranlib",
            "llvm-nm",
            "ld.lld",
            "clang",
            host=MINGW_TRIPLE,
            windres=f"{MINGW_TRIPLE}-windres",
            compatibility_cc=f"{MINGW_TRIPLE}-gcc",
            compatibility_cxx=f"{MINGW_TRIPLE}-g++",
            runtime_flags=("-static-libstdc++", "-static-libgcc"),
            link_flags=("-static", "-pthread"),
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
