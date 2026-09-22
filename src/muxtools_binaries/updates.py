import copy
import hashlib
import json
import re
import subprocess
import tomllib
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import httpx2
import tomlkit
from packaging.version import InvalidVersion, Version

from .models import Package, load_packages
from .recipes import load_recipe, recipe_checks


def latest_tag(source: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result = subprocess.run(
        ["git", "ls-remote", "--tags", source["repository"]], check=True, text=True, capture_output=True
    )
    refs = dict(line.split()[::-1] for line in result.stdout.splitlines())
    candidates = []
    for ref, commit in refs.items():
        tag = ref.removeprefix("refs/tags/")
        if prefix and not tag.startswith(prefix):
            continue
        version = tag[len(prefix) :] if prefix else tag
        if re.fullmatch(r"v?\d+(?:\.\d+)+", version):
            candidates.append((Version(version.removeprefix("v")), tag, refs.get(ref + "^{}", commit)))
    if not candidates:
        raise ValueError(f"No stable version tags in {source['repository']}")
    _, tag, commit = max(candidates)
    try:
        current = source["tag"]
        if prefix:
            if not current.startswith(prefix):
                raise ValueError
            current = current[len(prefix) :]
        if Version(current.removeprefix("v")) >= Version(tag[len(prefix) :].removeprefix("v")):
            return source
    except InvalidVersion:
        raise ValueError(f"Cannot order source tag {source['tag']}") from None
    return dict(source, tag=tag, commit=commit)


def get_json(url: str) -> Any:
    response = httpx2.get(url, headers={"Accept": "application/json"}, follow_redirects=True, timeout=60)
    response.raise_for_status()
    return response.json()


def remote_hash(url: str) -> str:
    digest = hashlib.sha256()
    with httpx2.stream("GET", url, follow_redirects=True, timeout=httpx2.Timeout(120, connect=30)) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class UpdateContext:
    def __init__(self, data: dict[str, Any]) -> None:
        self.original = copy.deepcopy(data)
        self.data = copy.deepcopy(data)

    def source_tags(self) -> dict[str, Any]:
        if "repository" in self.data["source"]:
            self.data["source"] = latest_tag(self.data["source"])
        if "dependencies" in self.data:
            self.data["dependencies"] = {
                name: latest_tag(pin) if "repository" in pin else pin for name, pin in self.data["dependencies"].items()
            }
        if self.pins_changed and "tag" in self.data["source"]:
            self.data["version"] = self.data["source"]["tag"].removeprefix("v")
        return self.data

    @property
    def pins_changed(self) -> bool:
        return any(self.data.get(key) != self.original.get(key) for key in ("source", "dependencies"))

    def get_json(self, url: str) -> Any:
        return get_json(url)

    def get_text(self, url: str) -> str:
        response = httpx2.get(url, follow_redirects=True, timeout=60)
        response.raise_for_status()
        return response.text

    def remote_hash(self, url: str) -> str:
        return remote_hash(url)


def source_update(ctx: UpdateContext) -> dict[str, Any]:
    return ctx.source_tags()


def update_definition(data: dict[str, Any], *, root: Path) -> dict[str, Any]:
    recipe = load_recipe(root, data["name"])
    updated = recipe.discover_update(UpdateContext(data))
    if updated["name"] != data["name"]:
        raise ValueError("Update discovery cannot change package identity")
    updated["version_code"] = data["version_code"]
    if updated != data:
        updated["version_code"] += 1
    package = Package.model_validate(updated)
    for target in package.targets:
        recipe_checks(recipe, package, target)
    return updated


def _apply_updates(document: MutableMapping[str, Any], updated: dict[str, Any]) -> None:
    """Replace changed values without rebuilding existing TOML tables or arrays."""
    for key, value in updated.items():
        current = document.get(key)
        if isinstance(current, MutableMapping) and isinstance(value, dict):
            _apply_updates(current, value)
        elif current != value:
            document[key] = value


def fill_hashes(root: Path, name: str, refresh: bool = False) -> list[str]:
    """Fill archive and asset hashes in one package manifest."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise ValueError(f"Invalid package name: {name}")
    path = root / "packages" / name / "package.toml"
    if not path.is_file():
        raise ValueError(f"Unknown package: {name}")

    original = path.read_text()
    data = tomllib.loads(original)
    if data.get("name") != name:
        raise ValueError(f"Package name must match directory: {path}")

    pins: list[tuple[str, dict[str, Any]]] = []
    source = data.get("source")
    if isinstance(source, dict) and "url" in source:
        pins.append(("source.sha256", source))
    for dependency, pin in data.get("dependencies", {}).items():
        if "url" in pin:
            pins.append((f"dependencies.{dependency}.sha256", pin))
    for target, config in data.get("targets", {}).items():
        if "asset" in config:
            pins.append((f"targets.{target}.asset.sha256", config["asset"]))

    changes: list[str] = []
    digests: dict[str, str] = {}
    for field, pin in pins:
        if not refresh and pin.get("sha256"):
            continue
        url = pin.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError(f"Invalid HTTPS URL for {field}: {url!r}")
        if url not in digests:
            digests[url] = remote_hash(url)
        digest = digests[url]
        if pin.get("sha256") != digest:
            pin["sha256"] = digest
            changes.append(field)

    Package.model_validate(data)
    if changes:
        document = tomlkit.parse(original)
        _apply_updates(document, data)
        path.write_text(tomlkit.dumps(document))
    return changes


def discover(root: Path, name: str | None, apply: bool) -> None:
    changes = []
    for package in load_packages(root, [name] if name else None).values():
        path = root / "packages" / package.name / "package.toml"
        text = path.read_text()
        original = tomllib.loads(text)
        updated = update_definition(original, root=root)
        if updated != original:
            changes.append({"package": package.name, "before": original["version"], "after": updated["version"]})
            if apply:
                document = tomlkit.parse(text)
                _apply_updates(document, updated)
                path.write_text(tomlkit.dumps(document))
    print(json.dumps(changes, indent=2))
