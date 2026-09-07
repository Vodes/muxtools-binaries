from types import SimpleNamespace

import pytest

from muxtools_binaries.updates import discover, latest_tag, update_definition


def test_discovery_preserves_toml_formatting(tmp_path, monkeypatch):
    path = tmp_path / "packages" / "example" / "package.toml"
    path.parent.mkdir(parents=True)
    original = (
        '# Keep this comment\nversion = "old" # and this one\n'
        'version_code = 1\n\n[source]\ntag = "old"\n\n'
        '[executables]\nexample = ["--version"]\n'
        'other = [\n    "-V", # keep multiline arrays too\n]\n'
    )
    path.write_text(original)
    monkeypatch.setattr(
        "muxtools_binaries.updates.load_packages", lambda *args: {"example": SimpleNamespace(name="example")}
    )
    monkeypatch.setattr(
        "muxtools_binaries.updates.update_definition",
        lambda data: dict(data, version="new", version_code=2, source={"tag": "new"}),
    )
    discover(tmp_path, None, apply=False)
    assert path.read_text() == original
    discover(tmp_path, None, apply=True)
    assert path.read_text() == original.replace('"old"', '"new"').replace("version_code = 1", "version_code = 2")


def test_tag_order_and_annotated_commit(package, monkeypatch):
    source = package.source.model_dump()
    refs = "old\trefs/tags/v1.9\nannotated\trefs/tags/v1.10\npeeled\trefs/tags/v1.10^{}\n"
    monkeypatch.setattr("muxtools_binaries.updates.subprocess.run", lambda *a, **kw: SimpleNamespace(stdout=refs))
    assert latest_tag(source) == dict(source, tag="v1.10", commit="peeled")


def test_noop_and_dependency_update(package, monkeypatch):
    data = package.model_dump()
    data["dependencies"] = {"dependency": dict(data["source"], repository="https://example.test/dependency")}
    monkeypatch.setattr("muxtools_binaries.updates.latest_tag", lambda source: source)
    assert update_definition(data) == data

    def update(source):
        return dict(source, tag="v2.0") if source == data["dependencies"]["dependency"] else source

    monkeypatch.setattr("muxtools_binaries.updates.latest_tag", update)
    updated = update_definition(data)
    assert updated["version"] == data["version"]
    assert updated["version_code"] == data["version_code"] + 1
    monkeypatch.setattr("muxtools_binaries.updates.latest_tag", lambda source: dict(source, tag="v3.0"))
    newer = update_definition(updated)
    assert newer["version"] != updated["version"]
    assert newer["version_code"] == updated["version_code"] + 1


def test_post_release_version_is_preserved_until_upstream_changes(package, monkeypatch):
    data = package.model_dump()
    data.update(version="1.0.post1", version_code=2)
    monkeypatch.setattr("muxtools_binaries.updates.latest_tag", lambda source: source)
    assert update_definition(data) == data
    monkeypatch.setattr("muxtools_binaries.updates.latest_tag", lambda source: dict(source, tag="v2.0"))
    updated = update_definition(data)
    assert updated["version"] == "2.0"
    assert updated["version_code"] == 3


@pytest.mark.parametrize("version", ["2.0", "2.0-1-gabcdef"])
def test_ffmpeg_asset_selection(package, monkeypatch, version):
    data = package.model_dump()
    data.update(
        name="ffmpeg",
        type="external-build",
        source=None,
        update={"kind": "github-release", "repository": "example/builds"},
    )
    assets = [
        dict(
            name=f"ffmpeg-n{version}-{suffix}",
            browser_download_url="https://example.test/" + suffix,
            digest="sha256:" + "0" * 64,
        )
        for suffix in ("linux64-nonfree-2.0.tar.xz", "win64-nonfree-2.0.zip", "win64-nonfree-shared-2.0.zip")
    ]
    for target, config in data["targets"].items():
        config["asset"] = dict(
            url="https://example.test/old", sha256="0" * 64, format="zip" if target.startswith("windows") else "tar.xz"
        )
    release = dict(tag_name="autobuild-2000-01-01-00-00", draft=False, prerelease=False, assets=assets)
    monkeypatch.setattr("muxtools_binaries.updates.get_json", lambda _: [dict(release, tag_name="latest"), release])
    result = update_definition(data)
    assert result["version"] == version + "-2000-01-01"
    assert result["version_code"] == data["version_code"] + 1
    assert "shared" not in result["targets"]["windows-x86_64"]["asset"]["url"]
    assets.append(assets[0])
    with pytest.raises(ValueError, match="Ambiguous"):
        update_definition(data)
