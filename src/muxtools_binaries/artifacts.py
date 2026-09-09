import json
import os
import tarfile
import tomllib
from pathlib import Path
from typing import Any

import tomlkit
import zstandard

from .checks import CheckSuite, archive_checks
from .io import run, sha256
from .models import Package, safe_path


def metadata(
    package: Package, target: str, revision: str, image: str, channel: str, checks: CheckSuite
) -> dict[str, Any]:
    config = package.targets[target]
    data: dict[str, Any] = dict(
        schema_version=2,
        name=package.name,
        version=package.version,
        version_code=package.version_code,
        target=target,
        binaries=package.binaries(target),
        smoke=package.executables,
        checks=checks.model_dump(),
        provenance=dict(type=package.type, channel=channel),
        builder=dict(revision=revision, image=image),
    )
    if package.provider:
        data["provenance"]["provider"] = package.provider
    if package.source:
        data["source"] = package.source.model_dump()
    if package.dependencies:
        data["dependencies"] = {name: pin.model_dump() for name, pin in package.dependencies.items()}
    if target.startswith("linux") and (config.runtime.exception or config.runtime.requirements):
        data["runtime"] = config.runtime.model_dump(exclude_defaults=True)
    if config.asset:
        data["provenance"]["asset"] = config.asset.model_dump()
    if package.type == "source-build":
        from .build import TRIPLE

        compiler = config.compiler
        executable = (TRIPLE + "-" if target.startswith("windows") and compiler == "gcc" else "") + compiler
        linker = "ld.lld" if compiler == "clang" else (TRIPLE + "-" if target.startswith("windows") else "") + "ld"
        data["build"] = {
            key: getattr(config, key)
            for key in ("compiler", "lto", "cpu_levels", "extra_cflags", "extra_cxxflags", "extra_ldflags")
        }
        data["build"].update(
            compiler_version=run([executable, "--version"], capture=True).stdout.splitlines()[0],
            linker=linker,
            linker_version=run([linker, "--version"], capture=True).stdout.splitlines()[0],
        )
    data.setdefault("build", {}).update(options=package.build, target_options=config.build)
    return data


def validate_layout(stage: Path, data: dict[str, Any]) -> None:
    import re

    from .models import TARGETS, TIERS

    if not re.fullmatch(r"[a-z][a-z0-9-]*", data.get("name", "")) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9.+_-]*", data.get("version", "")
    ):
        raise ValueError("Invalid artifact identity")
    if type(data.get("version_code")) is not int or data["version_code"] < 1 or data.get("target") not in TARGETS:
        raise ValueError("Invalid artifact version code or target")
    if data.get("schema_version") not in (1, 2) or data.get("provenance", {}).get("channel") not in ("test", "release"):
        raise ValueError("Unsupported metadata schema or channel")
    seen = set()
    for path in stage.rglob("*"):
        name = safe_path(path.relative_to(stage).as_posix())
        if path.is_symlink() or not (path.is_file() or path.is_dir()) or name.casefold() in seen:
            raise ValueError(f"Invalid package member: {name}")
        seen.add(name.casefold())
    for executable, variants in data["binaries"].items():
        if not variants or not set(variants) <= set(TIERS):
            raise ValueError("Invalid binary tiers")
        if "baseline" not in variants or executable not in data["smoke"]:
            raise ValueError(f"Missing baseline or smoke command for {executable}")
        for name in variants.values():
            path = stage / safe_path(name)
            if not path.is_file():
                raise ValueError(f"Missing executable: {name}")
            if data["target"].startswith("linux") and not path.stat().st_mode & 0o111:
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


def write_report(archive: Path, checks: list[str], output: Path) -> None:
    output.write_text(
        json.dumps(
            {"schema_version": 1, "archive": archive.name, "sha256": sha256(archive), "checks": checks, "os": os.name},
            indent=2,
        )
        + "\n"
    )
