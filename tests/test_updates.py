from types import SimpleNamespace

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
        lambda data, **kwargs: dict(data, version="new", version_code=2, source={"tag": "new"}),
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


def test_noop_and_dependency_update(package, monkeypatch, recipe_root):
    data = package.model_dump()
    data["dependencies"] = {"dependency": dict(data["source"], repository="https://example.test/dependency")}
    monkeypatch.setattr("muxtools_binaries.updates.latest_tag", lambda source: source)
    assert update_definition(data, root=recipe_root) == data

    def update(source):
        return dict(source, tag="v2.0") if source == data["dependencies"]["dependency"] else source

    monkeypatch.setattr("muxtools_binaries.updates.latest_tag", update)
    updated = update_definition(data, root=recipe_root)
    assert updated["version"] == data["version"]
    assert updated["version_code"] == data["version_code"] + 1
    monkeypatch.setattr("muxtools_binaries.updates.latest_tag", lambda source: dict(source, tag="v3.0"))
    newer = update_definition(updated, root=recipe_root)
    assert newer["version"] != updated["version"]
    assert newer["version_code"] == updated["version_code"] + 1


def test_post_release_version_is_preserved_until_upstream_changes(package, monkeypatch, recipe_root):
    data = package.model_dump()
    data.update(version="1.0.post1", version_code=2)
    monkeypatch.setattr("muxtools_binaries.updates.latest_tag", lambda source: source)
    assert update_definition(data, root=recipe_root) == data
    monkeypatch.setattr("muxtools_binaries.updates.latest_tag", lambda source: dict(source, tag="v2.0"))
    updated = update_definition(data, root=recipe_root)
    assert updated["version"] == "2.0"
    assert updated["version_code"] == 3
