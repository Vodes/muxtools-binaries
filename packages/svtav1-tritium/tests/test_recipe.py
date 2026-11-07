import tomllib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from muxtools_binaries.checks import VideoCheck
from muxtools_binaries.cli import _matrix
from muxtools_binaries.models import GitSource, load_packages
from muxtools_binaries.recipes import load_recipe
from muxtools_binaries.svtav1 import build_encoder, checks
from muxtools_binaries.updates import UpdateContext, update_definition

ROOT = Path(__file__).resolve().parents[3]
recipe = load_recipe(ROOT, "svtav1-tritium")


def definition():
    return tomllib.loads((ROOT / "packages/svtav1-tritium/package.toml").read_text())


def release(tag="pgo-build-2026-11-08-abcdef0", **kwargs):
    return dict(tag_name=tag, draft=False, published_at="2026-11-08T10:00:00Z", **kwargs)


def responses(monkeypatch, pages, commit=None):
    calls = []

    def get_json(url):
        calls.append(url)
        if "/releases?" in url:
            return pages.get(int(url.rsplit("=", 1)[1]), [])
        assert url == f"{recipe.API}/commits/abcdef0"
        return {"sha": "abcdef0" + "1" * 33} if commit is None else commit

    monkeypatch.setattr("muxtools_binaries.updates.get_json", get_json)
    return calls


def test_discovery_filters_and_paginates(monkeypatch):
    data = definition()
    calls = responses(
        monkeypatch,
        {
            1: [release("v9.0.0"), dict(release("pgo-build-2026-11-09-fffffff"), draft=True)],
            2: [release(), release("pgo-build-2026-11-07-fffffff")],
        },
    )
    updated = update_definition(data, root=ROOT)
    assert updated["version"] == "2026-11-08-abcdef0"
    assert updated["source"]["tag"] == "pgo-build-2026-11-08-abcdef0"
    assert updated["source"]["commit"] == "abcdef0" + "1" * 33
    assert updated["dependencies"] == data["dependencies"]
    assert updated["version_code"] == data["version_code"] + 1
    assert len(calls) == 4


def test_same_day_builds_use_publication_time_not_hash(monkeypatch):
    responses(
        monkeypatch,
        {1: [dict(release("pgo-build-2026-11-08-fffffff"), published_at="2026-11-08T09:00:00Z"), release()]},
    )
    assert recipe.discover_update(UpdateContext(definition()))["version"] == "2026-11-08-abcdef0"


def test_unchanged_release_preserves_rebuild_version(monkeypatch):
    data = definition()
    data["version"] += ".post1"
    calls = responses(monkeypatch, {1: [release(data["source"]["tag"])]})
    assert update_definition(data, root=ROOT) == data
    assert all("/commits/" not in url for url in calls)


def test_older_releases_do_not_downgrade(monkeypatch):
    data = definition()
    responses(monkeypatch, {1: [release("pgo-build-2026-11-06-abcdef0")]})
    assert recipe.discover_update(UpdateContext(data)) == data


@pytest.mark.parametrize("commit", [{}, {"sha": "abcdef0"}, {"sha": "0" * 40}])
def test_rejects_missing_or_inconsistent_source_commit(monkeypatch, commit):
    responses(monkeypatch, {1: [release()]}, commit)
    with pytest.raises(ValueError, match="Invalid source commit"):
        recipe.discover_update(UpdateContext(definition()))


@pytest.mark.parametrize(
    ("releases", "message"),
    [
        ([], "No published"),
        ([dict(release(), published_at=None)], "Missing publication"),
        ([dict(release(), published_at="2026-11-08T10:00:00")], "Missing publication timezone"),
        ([release(), release("pgo-build-2026-11-08-fffffff")], "Ambiguous"),
    ],
)
def test_rejects_invalid_release_metadata(monkeypatch, releases, message):
    responses(monkeypatch, {1: releases})
    with pytest.raises(ValueError, match=message):
        recipe.discover_update(UpdateContext(definition()))


def test_rejects_inconsistent_current_pin():
    data = definition()
    data["source"]["commit"] = "0" * 40
    with pytest.raises(ValueError, match="source pin"):
        recipe.discover_update(UpdateContext(data))


@pytest.mark.parametrize("fork", ["5fish", "essential", "tritium"])
@pytest.mark.parametrize("windows", [False, True])
def test_shared_encoder_configuration(tmp_path, fork, windows):
    source = tmp_path / "source"
    app = source / "Source/App/CMakeLists.txt"
    app.parent.mkdir(parents=True)
    app.write_text('"-l${BASE}${SUFFIX}" "${BASE}${SUFFIX}"')
    root_cmake = source / "CMakeLists.txt"
    root_cmake.write_text('"-l${BASE}${SUFFIX}"' if fork == "essential" else "# upstream FFMS2 wiring")
    if fork == "essential":
        bundled = source / "third_party/webm/libwebm"
        (bundled / "mkvmuxer").mkdir(parents=True)
        (bundled / "mkvmuxer/mkvmuxer.cc").touch()
        (bundled / "LICENSE.TXT").write_text("WebM license")
    ctx = MagicMock()
    ctx.package.name = f"svtav1-{fork}"
    ctx.package.source = GitSource(repository="https://example.test/svt", tag="upstream-tag", commit="0" * 40)
    ctx.config.cpu_levels = ["baseline"]
    ctx.source.return_value = source
    ctx.stage = tmp_path / "stage"
    ctx.prefix = tmp_path / "prefix"
    ctx.windows = windows
    ctx.target_info.arch = "x86_64"
    ctx.target_info.executable_suffix = ".exe" if windows else ""
    ctx.cmake.return_value = tmp_path / "result"
    version_tag = "2026-11-07-25c3e38" if fork == "tritium" else None
    build_encoder(ctx, essential=fork == "essential", ffms2=fork == "tritium", version_tag=version_tag)
    build_pin = ctx.source.call_args.args[1]
    assert build_pin.tag == (version_tag or "upstream-tag")
    assert build_pin.commit == ctx.package.source.commit
    assert ctx.package.source.tag == "upstream-tag"
    definitions = ctx.cmake.call_args.args[2]
    assert definitions.get("USE_FFMS2", False) == (fork != "5fish")
    assert definitions.get("USE_WEBM_IO", False) == (fork == "essential")
    assert definitions["LIBDOVI_FOUND"] and definitions["LIBHDR10PLUS_RS_FOUND"]
    assert definitions["EXT_LIB_STATIC"] and not definitions["SVT_AV1_LTO"]
    assert ("CMAKE_ASM_NASM_OBJECT_FORMAT" in definitions) == windows
    assert "${SUFFIX}" not in app.read_text()
    if fork == "essential":
        assert root_cmake.read_text() == '"${BASE}"'
        assert (ctx.stage / "licenses/libwebm/LICENSE.TXT").read_text() == "WebM license"
    else:
        assert root_cmake.read_text() == "# upstream FFMS2 wiring"
    ctx.stage_binary.assert_called_once()


def test_targets_and_functional_check():
    package = load_packages(ROOT, ["svtav1-tritium"])["svtav1-tritium"]
    targets = {entry["target"] for entry in _matrix(ROOT, package.name, None)["include"]}
    assert targets == {"linux-x86_64", "windows-x86_64", "linux-arm64", "windows-arm64", "macos-arm64"}
    for target in targets:
        suite = checks(package, target)
        suite.validate_binaries(package.binaries(target))
        video = suite.functional[0]
        assert isinstance(video, VideoCheck)
        assert video.bit_depth == 10 and video.suffix == "ivf"
        assert "--fps-num" in video.args and "--fps-denom" in video.args
        assert "--frames" in video.args and "--preset" in video.args
        assert "--speed" not in video.args and "--webm" not in video.args
