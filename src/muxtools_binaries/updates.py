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


def update_definition(data: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(data)
    kind = data.get("update", {}).get("kind", "git-tags")
    if kind == "git-tags":
        updated["source"] = latest_tag(data["source"])
        if "dependencies" in data:
            updated["dependencies"] = {name: latest_tag(source) for name, source in data["dependencies"].items()}
        version = updated["source"]["tag"].removeprefix("v")
        if data["name"] == "fdkaac":
            version += "-libfdk-" + updated["dependencies"]["fdk"]["tag"].removeprefix("v")
        if data["name"] == "opus-tools":
            version += "-libopus-" + updated["dependencies"]["opus"]["tag"].removeprefix("v")
        updated["version"] = version
    elif kind == "github-release":
        repository = data["update"]["repository"]
        releases = get_json(f"https://api.github.com/repos/{repository}/releases?per_page=100")
        release = next(
            (r for r in releases if not r["draft"] and not r["prerelease"] and r["tag_name"].startswith("autobuild-")),
            None,
        )
        if not release:
            raise ValueError("No dated FFmpeg release available")
        source_versions = set()
        for target, config in updated["targets"].items():
            suffix = r"linux64-nonfree-[\d.]+\.tar\.xz" if target.startswith("linux") else r"win64-nonfree-[\d.]+\.zip"
            pattern = r"ffmpeg-n(\d+(?:\.\d+)+(?:-\d+-g[0-9a-f]+)?)-" + suffix
            matches = [(a, re.fullmatch(pattern, a["name"])) for a in release["assets"]]
            matches = [(asset, match) for asset, match in matches if match]
            if len(matches) != 1:
                raise ValueError(f"Ambiguous or missing FFmpeg artifact for {target}")
            asset, match = matches[0]
            source_versions.add(match[1])
            config["asset"]["url"] = asset["browser_download_url"]
            config["asset"]["sha256"] = (asset.get("digest") or "").removeprefix("sha256:") or remote_hash(
                asset["browser_download_url"]
            )
        if len(source_versions) != 1:
            raise ValueError("FFmpeg targets have different source versions")
        updated["version"] = source_versions.pop() + "-" + release["tag_name"][10:20]
    else:
        entries = get_json("https://mkvtoolnix.download/windows/releases/")
        versions = [
            Version(entry["name"].rstrip("/"))
            for entry in entries
            if re.fullmatch(r"\d+\.\d+(?:\.\d+)?/", entry["name"])
        ]
        version = str(max(versions))
        if Version(version) <= Version(data["version"]):
            return data
        for target, config in updated["targets"].items():
            url = (
                f"https://mkvtoolnix.download/appimage/MKVToolNix_GUI-{version}-x86_64.AppImage"
                if target.startswith("linux")
                else f"https://mkvtoolnix.download/windows/releases/{version}/mkvtoolnix-64-bit-{version}.zip"
            )
            config["asset"].update(url=url, sha256=remote_hash(url))
        updated["version"] = version
    if updated != data:
        updated["version_code"] = data["version_code"] + 1
    Package.model_validate(updated)
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
        updated = update_definition(original)
        if updated != original:
            changes.append({"package": package.name, "before": original["version"], "after": updated["version"]})
            if apply:
                document = tomlkit.parse(text)
                _apply_updates(document, updated)
                path.write_text(tomlkit.dumps(document))
    print(json.dumps(changes, indent=2))
