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


def latest_tag(source: dict[str, Any]) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "ls-remote", "--tags", source["repository"]], check=True, text=True, capture_output=True
    )
    refs = dict(line.split()[::-1] for line in result.stdout.splitlines())
    candidates = []
    for ref, commit in refs.items():
        tag = ref.removeprefix("refs/tags/")
        if re.fullmatch(r"v?\d+(?:\.\d+)+", tag):
            candidates.append((Version(tag.removeprefix("v")), tag, refs.get(ref + "^{}", commit)))
    if not candidates:
        raise ValueError(f"No stable version tags in {source['repository']}")
    _, tag, commit = max(candidates)
    try:
        if Version(tag.removeprefix("v")) <= Version(source["tag"].removeprefix("v")):
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
        self.data["source"] = latest_tag(self.data["source"])
        if "dependencies" in self.data:
            self.data["dependencies"] = {name: latest_tag(pin) for name, pin in self.data["dependencies"].items()}
        if self.pins_changed:
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
