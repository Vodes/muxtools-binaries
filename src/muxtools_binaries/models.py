import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

TIERS = ("baseline", "avx2", "avx512", "zn4")
TARGETS = {"linux-x86_64": ("linux", "x86_64"), "windows-x86_64": ("windows", "x86_64")}


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or any(character in value for character in '\\:*?"<>|')
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"Unsafe archive path: {value!r}")
    if any(part.endswith((".", " ")) for part in path.parts):
        raise ValueError(f"Non-portable archive path: {value!r}")
    if not path.parts or any(
        part.split(".")[0].upper()
        in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
        for part in path.parts
    ):
        raise ValueError(f"Non-portable archive path: {value!r}")
    return path.as_posix()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Model):
    repository: str = Field(pattern=r"^https://")
    tag: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class Asset(Model):
    url: str = Field(pattern=r"^https://")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    format: Literal["zip", "tar.xz", "7z", "appimage", "binary"]


class Runtime(Model):
    glibc: str = Field(default="2.34", pattern=r"^\d+\.\d+$")
    requirements: list[str] = Field(default_factory=list)
    exception: str = ""


class Target(Model):
    compiler: Literal["gcc", "clang"] = "gcc"
    lto: Literal[False, "full", "thin"] = False
    cpu_levels: list[Literal["baseline", "avx2", "avx512", "zn4"]] = Field(default=["baseline"])
    extra_cflags: list[str] = Field(default_factory=list)
    extra_cxxflags: list[str] = Field(default_factory=list)
    extra_ldflags: list[str] = Field(default_factory=list)
    runtime: Runtime = Field(default_factory=Runtime)
    asset: Asset | None = None
    build: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def choices(self) -> Self:
        if not self.cpu_levels or self.cpu_levels[0] != "baseline" or len(set(self.cpu_levels)) != len(self.cpu_levels):
            raise ValueError("CPU levels must be unique and start with baseline")
        if self.compiler == "gcc" and self.lto == "thin":
            raise ValueError("GCC does not support thin LTO")
        return self


class Package(Model):
    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    description: str = ""
    version: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.+_-]*$")
    version_code: int = Field(ge=1, strict=True)
    type: Literal["source-build", "external-build", "upstream-binary"]
    provider: str = ""
    source: Source | None = None
    dependencies: dict[str, Source] = Field(default_factory=dict)
    targets: dict[str, Target]
    executables: dict[str, list[str]]
    build: dict[str, Any] = Field(default_factory=dict)
    update: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def contract(self) -> Self:
        if not self.targets or not self.executables:
            raise ValueError("A package needs targets and executable smoke commands")
        if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", name) for name in self.dependencies):
            raise ValueError("Invalid dependency name")
        for name in self.executables:
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
                raise ValueError(f"Invalid executable name: {name}")
        for target, config in self.targets.items():
            if target not in TARGETS:
                raise ValueError(f"Target {target} has no registered toolchain")
            if self.type == "source-build":
                if not self.source or config.asset:
                    raise ValueError("Source builds require source pins and cannot specify an imported asset")
                if target.startswith("linux") and config.runtime.glibc != "2.34":
                    raise ValueError("Source builds must meet glibc 2.34")
            elif not config.asset or config.cpu_levels != ["baseline"]:
                raise ValueError("Imported packages require an asset and baseline-only layout")
            if config.runtime.glibc != "2.34" and not config.runtime.exception:
                raise ValueError("A higher runtime minimum needs an explicit exception")
        return self

    def binaries(self, target: str) -> dict[str, dict[str, str]]:
        extension = ".exe" if TARGETS[target][0] == "windows" else ""
        return {
            name: {
                tier: name + ("" if tier == "baseline" else f".{tier}") + extension
                for tier in self.targets[target].cpu_levels
            }
            for name in self.executables
        }


def load_packages(root: Path, names: list[str] | None = None) -> dict[str, Package]:
    packages = {}
    for path in sorted((root / "packages").glob("*/package.toml")):
        package = Package.model_validate(tomllib.loads(path.read_text()))
        if package.name != path.parent.name:
            raise ValueError(f"Package name must match directory: {path}")
        packages[package.name] = package
    if not packages:
        raise ValueError(f"No package definitions in {root / 'packages'}")
    if names:
        unknown = set(names) - packages.keys()
        if unknown:
            raise ValueError(f"Unknown packages: {sorted(unknown)}")
        return {name: packages[name] for name in names}
    return packages
