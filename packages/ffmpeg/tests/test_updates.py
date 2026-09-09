from pathlib import Path

import pytest

from muxtools_binaries.recipes import load_recipe
from muxtools_binaries.updates import UpdateContext

recipe = load_recipe(Path(__file__).resolve().parents[3], "ffmpeg")


@pytest.mark.parametrize("version", ["2.0", "2.0-1-gabcdef"])
def test_ffmpeg_asset_selection(monkeypatch, version):
    data = dict(
        version="old",
        version_code=1,
        targets={target: {} for target in ("linux-x86_64", "windows-x86_64")},
        executables={"ffmpeg": ["-version"]},
    )
    data.update(
        name="ffmpeg",
        type="external-build",
        source=None,
        update={"repository": "example/builds"},
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
    result = recipe.discover_update(UpdateContext(data))
    assert result["version"] == version + "-2000-01-01"
    assert "shared" not in result["targets"]["windows-x86_64"]["asset"]["url"]
    assets.append(assets[0])
    with pytest.raises(ValueError, match="Ambiguous"):
        recipe.discover_update(UpdateContext(data))
