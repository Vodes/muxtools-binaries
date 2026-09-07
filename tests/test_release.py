import json
from unittest.mock import Mock

import httpx2
import pytest

from muxtools_binaries.artifacts import pack
from muxtools_binaries.io import sha256
from muxtools_binaries.release import GitHub, _publish_catalog, collect, publish, release_allowed


def test_manual_release_gate():
    assert release_allowed("workflow_dispatch", "refs/heads/main", True)
    assert not release_allowed("push", "refs/heads/main", True)
    assert not release_allowed("pull_request", "refs/heads/main", True)
    assert not release_allowed("workflow_dispatch", "refs/heads/topic", True)
    assert not release_allowed("workflow_dispatch", "refs/heads/main", False)


@pytest.fixture
def releases(package, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "fixture-revision")
    monkeypatch.setattr("muxtools_binaries.release.load_packages", lambda _: {package.name: package})
    monkeypatch.setattr("muxtools_binaries.release.builder_image", lambda *a, **kw: "fixture-image")
    artifacts = tmp_path / "artifacts"
    for target, config in package.targets.items():
        stage = tmp_path / target
        stage.mkdir()
        for variants in package.binaries(target).values():
            for name in variants.values():
                (stage / name).write_bytes(b"fixture")
                (stage / name).chmod(0o755)
        data = dict(
            schema_version=1,
            name=package.name,
            version=package.version,
            version_code=package.version_code,
            target=target,
            binaries=package.binaries(target),
            smoke=package.executables,
            source=package.source.model_dump(),
            provenance={"type": package.type, "channel": "release"},
            builder={"revision": "fixture-revision", "image": "fixture-image"},
            build=config.model_dump(
                include={"compiler", "lto", "cpu_levels", "extra_cflags", "extra_cxxflags", "extra_ldflags"}
            ),
        )
        archive = pack(stage, data, artifacts)
        archive.with_name(archive.name + ".report.json").write_text(
            json.dumps(
                dict(
                    sha256=sha256(archive),
                    os="nt" if target.startswith("windows") else "posix",
                    checks=["structure", "smoke", f"run:{package.name}:baseline"],
                )
            )
        )
    return artifacts


@pytest.mark.parametrize("broken", ["target", "report"])
def test_release_requires_complete_verified_artifacts(releases, package, broken):
    assert len(collect(releases, releases)[package.name]) == len(package.targets)
    if broken == "target":
        next(releases.glob("*.tar.zst")).unlink()
    else:
        next(releases.glob("*.report.json")).write_text("{}")
    with pytest.raises(ValueError, match="Incomplete|Missing native"):
        collect(releases, releases)


def test_failed_upload_does_not_publish_catalog(releases, package, monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    api = Mock()
    api.release.return_value = None
    api.upload.side_effect = ValueError("upload failed")
    monkeypatch.setattr("muxtools_binaries.release.GitHub", lambda _: api)
    with pytest.raises(ValueError, match="upload failed"):
        publish(releases, releases, "example/repo", True)
    api.request.assert_not_called()
    api.ensure_release.assert_called_once_with(f"{package.name}-{package.version}", "fixture-revision")
    assert all(call.args[0] != "catalog-v1" for call in api.ensure_release.call_args_list)


def test_catalog_provides_and_nested_versions(releases, package, monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_RUN_ID", "fixture-run")
    previous = {"version_code": package.version_code - 1, "targets": {}}
    old_catalog = {
        "schema_version": 1,
        "packages": {package.name: {"provides": ["old-tool"], "versions": {"previous": previous}}},
    }
    api = Mock()
    api.release.return_value = {"id": 1, "draft": False}
    api.ensure_release.return_value = {"id": 2, "draft": True}
    api.request.return_value = [{"id": 3, "name": "versions.json"}]
    api.asset_bytes.return_value = json.dumps(old_catalog).encode()
    published = {}

    def upload(release, path):
        if path.name == "versions.json":
            published.update(json.loads(path.read_text()))

    api.upload.side_effect = upload
    monkeypatch.setattr("muxtools_binaries.release.GitHub", lambda _: api)
    publish(releases, releases, "example/repo", True)
    entry = published["packages"][package.name]
    assert entry["provides"] == sorted(package.executables)
    assert entry["versions"]["previous"] == previous
    assert entry["versions"][package.version]["version_code"] == package.version_code


@pytest.mark.parametrize("state", ["changed", "unchanged", "failed"])
def test_catalog_cleanup_waits_for_verified_pointer(monkeypatch, state):
    monkeypatch.setenv("GITHUB_RUN_ID", "100")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    catalog = {"schema_version": 1, "packages": {}}
    payload = json.dumps(catalog, indent=2, sort_keys=True) + "\n"
    api = Mock()
    api.request.return_value = [
        {"id": 1, "name": "versions-99-1.json"},
        {"id": 2, "name": "versions.json"},
        {"id": 3, "name": "versions-notes.json"},
        {"id": 4, "name": "versions-100-1.json"},
    ]
    api.asset_bytes.return_value = (payload if state == "unchanged" else "{}").encode()

    def upload(release, path):
        # No snapshots may be deleted before upload verification returns successfully.
        assert not any(call.args == ("DELETE", "/releases/assets/1") for call in api.request.call_args_list)
        if path.name == "versions.json" and state == "failed":
            raise ValueError("Upload verification failed")

    api.upload.side_effect = upload
    if state == "failed":
        with pytest.raises(ValueError, match="Upload verification failed"):
            _publish_catalog(api, {"id": 10, "draft": False}, catalog)
    else:
        _publish_catalog(api, {"id": 10, "draft": False}, catalog)
    deleted = [call.args[1] for call in api.request.call_args_list if call.args[0] == "DELETE"]
    expected = [] if state == "unchanged" else ["/releases/assets/2"]
    if state != "failed":
        expected.append("/releases/assets/1")
    assert deleted == expected


def test_http_upload_integrity_and_redirects(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    archive = tmp_path / "example.tar.zst"
    archive.write_bytes(b"binary\x00payload")
    uploaded = []

    def respond(request):
        if request.method == "POST":
            uploaded.append(request.read())
            assert request.headers["content-length"] == str(archive.stat().st_size)
            return httpx2.Response(201, json={"id": 1})
        if request.url.host == "download.example":
            assert "authorization" not in request.headers
            return httpx2.Response(200, content=uploaded[-1])
        if request.url.path.endswith("/assets/1"):
            return httpx2.Response(302, headers={"Location": "https://download.example/asset"})
        return httpx2.Response(200, json=[{"id": 1, "name": archive.name}] if uploaded else [])

    api = GitHub("example/repo")
    with httpx2.Client(
        transport=httpx2.MockTransport(respond), headers=api.session.headers, follow_redirects=True
    ) as client:
        api.session.close()
        api.session = client
        release = {"id": 1, "draft": True, "upload_url": "https://uploads.github.com/example"}
        api.upload(release, archive)
        api.upload(release, archive)
        assert uploaded == [archive.read_bytes()]
        archive.write_bytes(b"changed")
        with pytest.raises(ValueError, match="Conflicting"):
            api.upload(release, archive)
