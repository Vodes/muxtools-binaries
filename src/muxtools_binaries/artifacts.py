import json
import platform
import tarfile
import tomllib
from pathlib import Path
from typing import Any

import tomlkit
import zstandard

from .checks import archive_checks
from .io import run, sha256
from .models import Package, safe_path
from .targets import BUILDERS, TARGETS, normalize_arch, normalize_os, target_spec, toolchain_spec


def metadata(package: Package, target: str, revision: str, image: str | None, channel: str) -> dict[str, Any]:
    config = package.targets[target]
    target_info = target_spec(target)
    native = BUILDERS[target_info.builder].platform is None
    if native and image is not None:
        raise ValueError("Native builders cannot record an image")
    if not native and image is None:
        raise ValueError("Container builders must record an image")
    data: dict[str, Any] = dict(
        schema_version=5,
        name=package.name,
        version=package.version,
        version_code=package.version_code,
        target=target,
        platform=dict(os=target_info.os, arch=target_info.arch),
        binaries=package.binaries(target),
        provenance=dict(type=package.type, channel=channel),
        builder=dict(
            kind="native" if native else "container",
            revision=revision,
            backend=target_info.builder,
        ),
    )
    if image is not None:
        data["builder"]["image"] = image
    if package.description:
        data["description"] = package.description
    if package.provider:
        data["provenance"]["provider"] = package.provider
    if package.source:
        data["source"] = package.source.model_dump()
    if package.dependencies:
        data["dependencies"] = {name: pin.model_dump() for name, pin in package.dependencies.items()}
    if target_info.os == "linux" and (config.runtime.exception or config.runtime.requirements):
        data["runtime"] = config.runtime.model_dump(exclude_defaults=True)
    if config.asset:
        data["provenance"]["asset"] = config.asset.model_dump()
    if package.type == "source-build":
        toolchain = toolchain_spec(target, config.toolchain)
        data["build"] = {
            key: getattr(config, key)
            for key in ("toolchain", "lto", "cpu_levels", "extra_cflags", "extra_cxxflags", "extra_ldflags")
        }
        linker_command = [toolchain.linker, "-v"] if target_info.os == "macos" else [toolchain.linker, "--version"]
        linker_result = run(linker_command, capture=True)
        if target_info.os == "macos":
            linker_version = "\n".join(
                output.strip() for output in (linker_result.stdout, linker_result.stderr) if output.strip()
            )
            linker_version = linker_version.splitlines()[0]
        else:
            linker_version = linker_result.stdout.splitlines()[0]
        data["build"].update(
            compiler_version=run([toolchain.cc, "--version"], capture=True).stdout.splitlines()[0],
            linker=toolchain.linker,
            linker_version=linker_version,
        )
    if package.build:
        data.setdefault("build", {})["options"] = package.build
    if config.build:
        data.setdefault("build", {})["target_options"] = config.build
    return data


def validate_layout(stage: Path, data: dict[str, Any]) -> None:
    import re

    from .models import TIERS

    if not re.fullmatch(r"[a-z][a-z0-9-]*", data.get("name", "")) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9.+_-]*", data.get("version", "")
    ):
        raise ValueError("Invalid artifact identity")
    if type(data.get("version_code")) is not int or data["version_code"] < 1 or data.get("target") not in TARGETS:
        raise ValueError("Invalid artifact version code or target")
    if data.get("schema_version") not in (1, 2, 3, 4, 5) or data.get("provenance", {}).get("channel") not in (
        "test",
        "release",
    ):
        raise ValueError("Unsupported metadata schema or channel")
    if data["schema_version"] >= 4:
        target = target_spec(data["target"])
        if data.get("platform") != {"os": target.os, "arch": target.arch}:
            raise ValueError("Artifact platform differs from its target")
        if data.get("builder", {}).get("backend") != target.builder:
            raise ValueError("Artifact builder differs from its target")
    if data["schema_version"] >= 5:
        builder = data.get("builder", {})
        expected_kind = "container" if BUILDERS[target_spec(data["target"]).builder].platform is not None else "native"
        if builder.get("kind") != expected_kind:
            raise ValueError("Artifact builder kind differs from its target")
        if expected_kind == "container" and not isinstance(builder.get("image"), str):
            raise ValueError("Container artifact is missing its builder image")
        if expected_kind == "native" and "image" in builder:
            raise ValueError("Native artifact cannot record a builder image")
    if not isinstance(data.get("description", ""), str):
        raise ValueError("Invalid artifact description")
    seen = set()
    for path in stage.rglob("*"):
        name = safe_path(path.relative_to(stage).as_posix())
        if path.is_symlink() or not (path.is_file() or path.is_dir()) or name.casefold() in seen:
            raise ValueError(f"Invalid package member: {name}")
        seen.add(name.casefold())
    for executable, variants in data["binaries"].items():
        if not variants or not set(variants) <= set(TIERS):
            raise ValueError("Invalid binary tiers")
        if "baseline" not in variants:
            raise ValueError(f"Missing baseline for {executable}")
        for name in variants.values():
            path = stage / safe_path(name)
            if not path.is_file():
                raise ValueError(f"Missing executable: {name}")
            if target_spec(data["target"]).os == "linux" and not path.stat().st_mode & 0o111:
                raise ValueError(f"Missing executable mode: {name}")
    if data["schema_version"] == 2:
        for name in archive_checks(data).files:
            path = stage / safe_path(name)
            if not path.is_file():
                raise ValueError(f"Missing required file: {name}")


def pack(stage: Path, data: dict[str, Any], output: Path) -> Path:
    validate_layout(stage, data)
    metadata_path = stage / ".metadata.toml"
    # Staged paths are excluded from repository-wide TOML checks.
    formatted = run(["tombi", "format", "--offline", "-"], input=tomlkit.dumps(data), capture=True).stdout
    run(["tombi", "lint", "--offline", "--error-on-warnings", "-"], input=formatted)
    metadata_path.write_text(formatted)
    output.mkdir(parents=True, exist_ok=True)
    name = f"{data['name']}-{data['version']}-{data['target']}.tar.zst"
    archive = output / name
    temporary = archive.with_suffix(".part")
    with temporary.open("wb") as raw, zstandard.ZstdCompressor(level=19, threads=0).stream_writer(raw) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for path in sorted(stage.rglob("*")):
                info = tar.gettarinfo(str(path), path.relative_to(stage).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.pax_headers = {}
                info.mode = 0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644
                if path.is_file():
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)
    temporary.replace(archive)
    archive.with_name(name + ".sha256").write_text(f"{sha256(archive)}  {name}\n")
    return archive


def read_metadata(stage: Path) -> dict[str, Any]:
    data = tomllib.loads((stage / ".metadata.toml").read_text())
    validate_layout(stage, data)
    return data


def write_report(archive: Path, checks: list[str], output: Path, target: str) -> None:
    target_info = target_spec(target)
    output.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "archive": archive.name,
                "sha256": sha256(archive),
                "checks": checks,
                "os": normalize_os(platform.system()),
                "arch": normalize_arch(platform.machine()),
                "target": target_info.name,
            },
            indent=2,
        )
        + "\n"
    )
