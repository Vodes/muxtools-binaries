from pathlib import Path

import pytest

from muxtools_binaries.recipes import load_recipe
from muxtools_binaries.updates import UpdateContext

recipe = load_recipe(Path(__file__).resolve().parents[3], "ffmpeg")
TARGETS = ("linux-x86_64", "linux-arm64", "windows-x86_64", "windows-arm64", "macos-arm64")


def _definition() -> dict:
    return dict(
        name="ffmpeg",
        version="9.0.1-r1",
        version_code=1,
        type="external-build",
        targets={
            target: {"asset": {"url": f"https://example.test/old-{target}", "sha256": "0" * 64, "format": "tar.zst"}}
            for target in TARGETS
        },
        executables={"ffmpeg": ["-version"]},
        update={"repository": "example/ffmpeg-mt"},
    )


def _release(tag: str) -> dict:
    version = tag.removeprefix("v").split("-r", 1)[0]
    return dict(
        tag_name=tag,
        draft=False,
        prerelease=False,
        assets=[
            dict(
                name=f"ffmpeg-{version}-{target}-nonfree.tar.zst",
                browser_download_url=f"https://example.test/{version}/{target}.tar.zst",
                digest="sha256:" + "1" * 64,
            )
            for target in TARGETS
        ],
    )


def test_ffmpeg_selects_latest_nonfree_release_and_target_assets(monkeypatch):
    data = _definition()
    release = _release("v9.0.2-r2-nonfree")
    release["assets"][0]["digest"] = None
    release["assets"].append(dict(name="ffmpeg-9.0.2-linux-x86_64-free.tar.zst"))
    old = _release("v9.0.1-r2-nonfree")
    draft = dict(_release("v9.0.3-r2-nonfree"), draft=True)
    monkeypatch.setattr("muxtools_binaries.updates.get_json", lambda _: [old, draft, release])
    calls = []

    def remote_hash(url):
        calls.append(url)
        return "2" * 64

    monkeypatch.setattr("muxtools_binaries.updates.remote_hash", remote_hash)
    result = recipe.discover_update(UpdateContext(data))
    assert result["version"] == "9.0.2-r2"
    assert result["targets"]["linux-x86_64"]["asset"]["sha256"] == "2" * 64
    assert result["targets"]["macos-arm64"]["asset"]["sha256"] == "1" * 64
    assert all(result["targets"][target]["asset"]["url"].endswith(f"/{target}.tar.zst") for target in TARGETS)
    assert calls == [release["assets"][0]["browser_download_url"]]

    release["assets"].append(release["assets"][0])
    with pytest.raises(ValueError, match="Ambiguous"):
        recipe.discover_update(UpdateContext(data))
