import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx2

from .artifacts import read_metadata
from .build import builder_image
from .io import extract, sha256
from .models import Package, load_packages


def release_allowed(event: str, ref: str, publish: bool) -> bool:
    return event == "workflow_dispatch" and ref == "refs/heads/main" and publish is True


type TargetArtifacts = dict[str, tuple[Path, dict[str, Any], str]]
type ReleaseArtifacts = dict[str, TargetArtifacts]


def release_assets(github: "GitHub", release: dict[str, Any]) -> list[dict[str, Any]]:
    assets = []
    page = 1
    while True:
        batch = github.request("GET", f"/releases/{release['id']}/assets?per_page=100&page={page}")
        assets.extend(batch)
        if len(batch) < 100:
            return assets
        page += 1


class GitHub:
    def __init__(self, repository: str) -> None:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
            raise ValueError("Expected owner/repository")
        self.repository = repository
        self.url = f"https://api.github.com/repos/{repository}"
        self.session = httpx2.Client(
            follow_redirects=True,
            timeout=httpx2.Timeout(120, connect=30),
            headers={
                "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.session.request(method, self.url + path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def release(self, tag: str) -> dict[str, Any] | None:
        response = self.session.get(self.url + "/releases/tags/" + quote(tag, safe=""), timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def ensure_release(self, tag: str, revision: str) -> dict[str, Any]:
        existing = self.release(tag)
        return existing or self.request(
            "POST",
            "/releases",
            json={"tag_name": tag, "target_commitish": revision, "name": tag, "draft": True, "make_latest": "false"},
        )

    def asset_bytes(self, asset: dict[str, Any]) -> bytes:
        response = self.session.get(
            self.url + f"/releases/assets/{asset['id']}",
            headers={"Accept": "application/octet-stream"},
        )
        response.raise_for_status()
        return response.content

    def upload(self, release: dict[str, Any], path: Path, *, replace: bool = False) -> None:
        assets = release_assets(self, release)
        existing = next((a for a in assets if a["name"] == path.name), None)
        if existing and replace:
            if not release["draft"]:
                raise ValueError("Cannot replace an asset on a published release")
            self.request("DELETE", f"/releases/assets/{existing['id']}")
        elif existing:
            import hashlib

            digest = hashlib.sha256(self.asset_bytes(existing)).hexdigest()
            if digest != sha256(path):
                raise ValueError(f"Conflicting published asset: {path.name}")
            return
        if not release["draft"]:
            raise ValueError(f"Published release is missing an expected asset: {path.name}")
        with path.open("rb") as stream:
            response = self.session.post(
                release["upload_url"].split("{")[0],
                params={"name": path.name},
                headers={"Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size)},
                content=stream,
                timeout=httpx2.Timeout(600, connect=30),
            )
        response.raise_for_status()
        import hashlib

        if hashlib.sha256(self.asset_bytes(response.json())).hexdigest() != sha256(path):
            raise ValueError(f"Upload verification failed: {path.name}")


def _validate_package(data: dict[str, Any], package: Package) -> None:
    if data["provenance"]["type"] != package.type:
        raise ValueError("Artifact provenance differs from package definition")
    if package.source and data.get("source") != package.source.model_dump():
        raise ValueError("Artifact source differs from package definition")
    if data.get("dependencies", {}) != {name: source.model_dump() for name, source in package.dependencies.items()}:
        raise ValueError("Artifact dependencies differ from package definition")
    if (data["version"], data["version_code"]) != (package.version, package.version_code):
        raise ValueError("Artifact does not match desired version")
    if data["binaries"] != package.binaries(data["target"]):
        raise ValueError("Artifact executable mapping differs from package definition")


def _validate_target(data: dict[str, Any], package: Package, root: Path) -> None:
    from .checks import archive_checks
    from .recipes import load_recipe, recipe_checks

    actual_checks = archive_checks(data)
    if actual_checks != recipe_checks(load_recipe(root, package.name), package, data["target"]):
        raise ValueError("Artifact checks differ from package recipe")
    config = package.targets[data["target"]]
    if data["smoke"] != package.executables:
        raise ValueError("Artifact smoke commands differ from package definition")
    if config.asset and data["provenance"].get("asset") != config.asset.model_dump():
        raise ValueError("Artifact import differs from package definition")
    expected_runtime = config.runtime.model_dump(exclude_defaults=True) if data["target"].startswith("linux") else {}
    if data.get("runtime", {}) != expected_runtime:
        raise ValueError("Artifact runtime differs from package definition")
    if (
        data.get("build", {}).get("options") != package.build
        or data.get("build", {}).get("target_options") != config.build
    ):
        raise ValueError("Artifact build options differ from package definition")
    if package.type == "source-build":
        for key in ("compiler", "lto", "cpu_levels", "extra_cflags", "extra_cxxflags", "extra_ldflags"):
            if data.get("build", {}).get(key) != getattr(config, key):
                raise ValueError(f"Artifact build setting differs from package definition: {key}")


def _require_native_report(artifacts: Path, archive: Path, digest: str, package: Package, target: str) -> None:
    reports = [json.loads(path.read_text()) for path in artifacts.rglob(archive.name + ".report.json")]
    required = {"structure", "smoke", *[f"run:{name}:baseline" for name in package.executables]}
    native_os = "nt" if target.startswith("windows") else "posix"
    if not any(
        report.get("sha256") == digest and required <= set(report.get("checks", [])) and report.get("os") == native_os
        for report in reports
    ):
        raise ValueError(f"Missing native smoke report: {archive.name}")


def collect(root: Path, artifacts: Path) -> ReleaseArtifacts:
    packages = load_packages(root)
    groups: ReleaseArtifacts = {}
    for archive in sorted(artifacts.rglob("*.tar.zst")):
        digest = sha256(archive)
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            extract(archive, stage, "tar.zst")
            data = read_metadata(stage)
        if data["provenance"]["channel"] != "release":
            raise ValueError(f"Not a release artifact: {archive.name}")
        package = packages[data["name"]]
        _validate_package(data, package)
        if data["builder"]["revision"] != os.environ["GITHUB_SHA"] or data["builder"]["image"] != builder_image(
            root, release=True
        ):
            raise ValueError("Artifact revision or builder is not eligible for publishing")
        _validate_target(data, package, root)
        _require_native_report(artifacts, archive, digest, package, data["target"])
        group = groups.setdefault(package.name, {})
        if data["target"] in group:
            raise ValueError("Duplicate target artifact")
        group[data["target"]] = (archive, data, digest)
    if not groups:
        raise ValueError("No release archives found")
    for name, targets in groups.items():
        if set(targets) != set(packages[name].targets):
            raise ValueError(f"Incomplete target set for {name}")
    return groups


def _load_catalog(github: GitHub, catalog_release: dict[str, Any] | None) -> dict[str, Any]:
    catalog: dict[str, Any] = {"schema_version": 1, "packages": {}}
    if catalog_release:
        assets = release_assets(github, catalog_release)
        current = next((a for a in assets if a["name"] == "versions.json"), None)
        if current:
            catalog = json.loads(github.asset_bytes(current))
        elif assets:
            snapshots = sorted((a for a in assets if a["name"].startswith("versions-")), key=lambda a: a["id"])
            if snapshots:
                catalog = json.loads(github.asset_bytes(snapshots[-1]))
    if catalog.get("schema_version") != 1:
        raise ValueError("Unsupported catalog schema")
    return catalog


def _publish_draft(github: GitHub, release: dict[str, Any]) -> None:
    if release["draft"]:
        github.request("PATCH", f"/releases/{release['id']}", json={"draft": False, "make_latest": "false"})


def _published_entry(github: GitHub, release: dict[str, Any], name: str, version: str) -> dict[str, Any] | None:
    entry: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for asset in release_assets(github, release):
            if not asset["name"].endswith(".tar.zst"):
                continue
            archive = directory / "archive.tar.zst"
            archive.write_bytes(github.asset_bytes(asset))
            stage = directory / str(asset["id"])
            extract(archive, stage, "tar.zst")
            data = read_metadata(stage)
            if (data["name"], data["version"]) != (name, version):
                raise ValueError("Published archive identity differs from its release")
            if entry is None:
                entry = {
                    "version": version,
                    "version_code": data["version_code"],
                    "tag": release["tag_name"],
                    "targets": {},
                }
            if data["version_code"] != entry["version_code"] or data["target"] in entry["targets"]:
                raise ValueError("Inconsistent published package archives")
            entry["targets"][data["target"]] = {
                "url": asset["browser_download_url"],
                "sha256": sha256(archive),
                "size": archive.stat().st_size,
                "binaries": data["binaries"],
                "runtime": data.get("runtime", {}),
            }
    return entry


def _publish_package(
    github: GitHub,
    repository: str,
    name: str,
    targets: TargetArtifacts,
    release: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    sample = next(iter(targets.values()))[1]
    tag = f"{name}-{sample['version']}"
    release = release or github.ensure_release(tag, sample["builder"]["revision"])
    if not release["draft"]:
        print(f"Skipping published version {tag}; recovering its catalog entry from published archives", flush=True)
        return _published_entry(github, release, name, sample["version"])
    entry = {"version": sample["version"], "version_code": sample["version_code"], "tag": tag, "targets": {}}
    for target, (archive, data, digest) in targets.items():
        checksum = archive.with_name(archive.name + ".sha256")
        checksum.write_text(f"{digest}  {archive.name}\n")
        github.upload(release, archive, replace=True)
        github.upload(release, checksum, replace=True)
        entry["targets"][target] = {
            "url": f"https://github.com/{repository}/releases/download/{tag}/{archive.name}",
            "sha256": digest,
            "size": archive.stat().st_size,
            "binaries": data["binaries"],
            "runtime": data.get("runtime", {}),
        }
    _publish_draft(github, release)
    return entry


def _update_catalog(github: GitHub, repository: str, catalog: dict[str, Any], groups: ReleaseArtifacts) -> None:
    for name, targets in groups.items():
        sample = next(iter(targets.values()))[1]
        version = sample["version"]
        package_entry = catalog["packages"].setdefault(name, {"provides": [], "versions": {}})
        versions = package_entry["versions"]
        if version in versions:
            print(f"Skipping published version {name}-{version}", flush=True)
            continue
        release = github.release(f"{name}-{version}")
        if (release is None or release["draft"]) and any(
            sample["version_code"] <= entry["version_code"] for entry in versions.values()
        ):
            raise ValueError(f"Version code for {name} must exceed its published version codes")
        entry = _publish_package(github, repository, name, targets, release)
        if entry is None:
            print(f"No catalog metadata available for historical release {name}-{version}", flush=True)
            if not versions:
                del catalog["packages"][name]
            continue
        versions[version] = entry
        latest = max(versions.values(), key=lambda item: item["version_code"])
        package_entry["provides"] = sorted(
            {binary for target in latest["targets"].values() for binary in target["binaries"]}
        )


def _prune_catalog_snapshots(github: GitHub, assets: list[dict[str, Any]]) -> None:
    snapshots = sorted(
        (asset for asset in assets if re.fullmatch(r"versions-[0-9]+-[0-9]+\.json", asset["name"])),
        key=lambda asset: asset["id"],
    )
    for asset in snapshots[:-1]:
        github.request("DELETE", f"/releases/assets/{asset['id']}")


def _publish_catalog(github: GitHub, catalog_release: dict[str, Any], catalog: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        payload = json.dumps(catalog, indent=2, sort_keys=True) + "\n"
        snapshot = (
            directory / f"versions-{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}.json"
        )
        snapshot.write_text(payload)
        # Only the dedicated catalog is mutable. Immutable snapshots make interrupted pointer updates recoverable.
        upload_release = dict(catalog_release, draft=True)
        github.upload(upload_release, snapshot)
        assets = release_assets(github, catalog_release)
        current = next((a for a in assets if a["name"] == "versions.json"), None)
        if not current or github.asset_bytes(current).decode() != payload:
            if current:
                github.request("DELETE", f"/releases/assets/{current['id']}")
            pointer = directory / "versions.json"
            pointer.write_text(payload)
            github.upload(upload_release, pointer)
        _publish_draft(github, catalog_release)
        # Upload verifies the pointer bytes; an unchanged pointer was verified above.
        # Keep recovery snapshots intact until the catalog is safely published.
        _prune_catalog_snapshots(github, assets)


def publish(root: Path, artifacts: Path, repository: str, enabled: bool) -> None:
    if not release_allowed(os.getenv("GITHUB_EVENT_NAME", ""), os.getenv("GITHUB_REF", ""), enabled):
        raise ValueError("Publishing requires manual dispatch on main with publish enabled")
    groups = collect(root, artifacts)
    github = GitHub(repository)
    catalog_release = github.release("catalog-v1")
    catalog = _load_catalog(github, catalog_release)
    _update_catalog(github, repository, catalog, groups)
    catalog_release = catalog_release or github.ensure_release("catalog-v1", os.environ["GITHUB_SHA"])
    _publish_catalog(github, catalog_release, catalog)
