import subprocess
from pathlib import Path
from types import SimpleNamespace

from muxtools_binaries.build import BuildContext
from muxtools_binaries.models import load_packages
from muxtools_binaries.recipes import load_recipe
from muxtools_binaries.updates import UpdateContext

ROOT = Path(__file__).resolve().parents[3]

recipe = load_recipe(ROOT, "mkvtoolnix")


def _context(tmp_path, target):
    package = load_packages(ROOT, ["mkvtoolnix"])["mkvtoolnix"]
    stage = tmp_path / "stage"
    stage.mkdir()
    return BuildContext(ROOT, package, target, tmp_path / "work", stage, 3)


def _mock_build(ctx, monkeypatch, tmp_path):
    names = ["zlib", "zstd", "ogg", "vorbis", "flac", "boost", "qtbase", "mkvtoolnix"]
    sources = {name: tmp_path / name for name in names}
    for source in sources.values():
        source.mkdir()
    (sources["mkvtoolnix"] / "Rakefile").write_text('  "-lstdc++",\n')
    calls = []

    monkeypatch.setattr(ctx, "source", lambda name, pin: sources[name])
    monkeypatch.setattr(
        ctx, "autotools", lambda name, source, options=(): calls.append(("autotools", name, list(options)))
    )
    monkeypatch.setattr(
        ctx,
        "cmake",
        lambda name, source, definitions, *, install=False: calls.append(
            ("cmake", name, source, definitions, install)
        ),
    )
    monkeypatch.setattr(ctx, "notices", lambda source, name: None)
    monkeypatch.setattr(ctx, "stage_binaries", lambda: calls.append(("stage",)))
    gmp_archive = tmp_path / "gmp.tar.xz"
    gmp_archive.write_bytes(b"fixture")
    iconv_archive = tmp_path / "libiconv.tar.gz"
    iconv_archive.write_bytes(b"fixture")
    monkeypatch.setattr(
        recipe,
        "download",
        lambda url, *_args: iconv_archive if "libiconv" in url else gmp_archive,
    )

    def fake_extract(input_archive, destination, _kind):
        source = destination / ("libiconv-1.18" if input_archive == iconv_archive else "gmp-6.3.0")
        source.mkdir(parents=True)

    monkeypatch.setattr(recipe, "extract", fake_extract)

    def fake_run(args, **kwargs):
        command = [str(arg) for arg in args]
        calls.append((command, kwargs))
        if command and Path(command[0]).name == "configure" and kwargs.get("cwd") == sources["mkvtoolnix"]:
            (sources["mkvtoolnix"] / "build-config").write_text(
                "\n".join(
                    [
                        "BUILD_GUI = no",
                        "FMT_INTERNAL = yes",
                        "EBML_MATROSKA_INTERNAL = yes",
                        "PUGIXML_INTERNAL = yes",
                        "NLOHMANN_JSON_INTERNAL = yes",
                        "UTF8CPP_INTERNAL = yes",
                        "FLAC_LIBS = -lFLAC",
                        "ICONV_LIBS = -liconv",
                        "QT_LIBS_NON_GUI = -lQt6Core",
                        "USE_DVDREAD = ",
                    ]
                )
            )
            return SimpleNamespace(
                stdout="\n".join(
                    [
                        "Optional features that are built:",
                        "   * FLAC audio",
                        "Optional features that are NOT built:",
                        "   * MKVToolNix GUI",
                        "   * DBus support",
                        "   * DVD chapter support via libdvdread",
                    ]
                )
            )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(recipe, "run", fake_run)
    return calls, sources


def test_manifest_is_source_build_with_pinned_minimal_dependencies():
    package = load_packages(ROOT, ["mkvtoolnix"])["mkvtoolnix"]
    assert package.type == "source-build"
    assert package.source is not None and package.source.recursive
    assert package.source.tag == "release-102.0"
    assert package.version_code == 3
    assert package.targets["linux-x86_64"].asset is None
    assert package.targets["windows-x86_64"].asset is None
    assert "-shared-libgcc" in package.targets["linux-x86_64"].extra_ldflags
    assert package.targets["linux-x86_64"].runtime.requirements == ["libgcc_s.so.1"]
    assert "-DFLAC__NO_DLL" in package.targets["windows-x86_64"].extra_cflags
    assert "-DFLAC__NO_DLL" in package.targets["windows-x86_64"].extra_cxxflags
    assert {"zlib", "zstd", "ogg", "vorbis", "flac", "boost", "qtbase"} == set(package.dependencies)


def test_linux_build_orchestrates_static_dependencies_and_cli_only_mkv(tmp_path, monkeypatch):
    ctx = _context(tmp_path, "linux-x86_64")
    calls, sources = _mock_build(ctx, monkeypatch, tmp_path)

    recipe.build(ctx)

    assert [entry[1] for entry in calls if entry[0] == "autotools"] == ["ogg", "vorbis", "flac"]
    zstd_cmake = next(entry for entry in calls if entry[:2] == ("cmake", "zstd"))
    assert zstd_cmake[2] == tmp_path / "zstd" / "build" / "cmake"
    assert zstd_cmake[3]["ZSTD_BUILD_SHARED"] is False
    assert zstd_cmake[3]["ZSTD_BUILD_STATIC"] is True
    assert zstd_cmake[4] is True
    rakefile = (sources["mkvtoolnix"] / "Rakefile").read_text()
    assert '  "-Wl,-Bstatic",\n  "-lstdc++",\n  "-Wl,-Bdynamic",\n' in rakefile
    gmp_configure = next(
        entry
        for entry in calls
        if isinstance(entry[0], list) and entry[0] and "gmp-6.3.0/configure" in entry[0][0]
    )
    assert "-march=x86-64-v2" in gmp_configure[1]["env"]["CFLAGS"].split()
    assert not any(argument.startswith("--host=") for argument in gmp_configure[0])
    mkv_configure = next(entry for entry in calls if isinstance(entry[0], list) and "--disable-gui" in entry[0])
    assert "--without-dvdread" in mkv_configure[0]
    assert "--without-gettext" in mkv_configure[0]
    assert "--enable-static" not in mkv_configure[0]
    assert mkv_configure[1]["env"]["PKG_CONFIG"] == "pkg-config --static"
    commands = [entry for entry in calls if len(entry) == 2 and isinstance(entry[0], list)]
    assert any(command == ["rake", "-j3", "apps:cli"] for command, _ in commands)
    assert any(command == ["rake", "install:programs"] for command, _ in commands)
    assert any(entry[0] == "stage" for entry in calls)
    assert "ac_cv_fmt" in mkv_configure[1]["env"]


def test_windows_build_bootstraps_native_qt_tools_and_cross_compiles_target(tmp_path, monkeypatch):
    ctx = _context(tmp_path, "windows-x86_64")
    calls, sources = _mock_build(ctx, monkeypatch, tmp_path)

    recipe.build(ctx)

    assert [entry[1] for entry in calls if entry[0] == "autotools"] == ["iconv", "ogg", "vorbis", "flac"]
    gmp_configure = next(
        entry
        for entry in calls
        if isinstance(entry[0], list) and entry[0] and "gmp-6.3.0/configure" in entry[0][0]
    )
    assert "--host=x86_64-w64-mingw32" in gmp_configure[0]
    assert "-march=x86-64-v2" in gmp_configure[1]["env"]["CFLAGS"].split()

    qt_configures = [
        entry for entry in calls if isinstance(entry[0], list) and entry[0] and Path(entry[0][0]).name == "configure"
    ]
    assert any(entry[1]["env"]["CC"] == "gcc" for entry in qt_configures)
    target_qt = next(entry for entry in qt_configures if "-qt-host-path" in entry[0])
    assert "-xplatform" in target_qt[0]
    assert any(argument.startswith("-DCMAKE_TOOLCHAIN_FILE=") for argument in target_qt[0])
    toolchain_argument = next(argument for argument in target_qt[0] if argument.startswith("-DCMAKE_TOOLCHAIN_FILE="))
    assert "CMAKE_SYSTEM_NAME Windows" in Path(toolchain_argument.split("=", 1)[1]).read_text()
    mkv_configure = next(
        entry for entry in calls if isinstance(entry[0], list) and "--disable-gui" in entry[0]
    )
    assert "--enable-static" not in mkv_configure[0]
    assert mkv_configure[1]["env"]["CC"] == "x86_64-w64-mingw32-gcc"
    assert (sources["mkvtoolnix"] / "Rakefile").read_text() == '  "-lstdc++",\n'


def test_checks_and_release_update():
    package = load_packages(ROOT, ["mkvtoolnix"])["mkvtoolnix"]
    suite = recipe.checks(package, "linux-x86_64")
    assert suite.smoke["mkvmerge"].stdout_prefix == "mkvmerge v"
    assert not suite.files
    assert len(suite.functional) == 1
    assert suite.functional[0].command == "mkvmerge"
    assert suite.functional[0].source == "flac"
    assert "libQt6Core.so*" in suite.forbidden_libraries
    assert "libgcc_s.so*" not in suite.forbidden_libraries
    assert "libzstd.so*" in suite.forbidden_libraries
    windows_suite = recipe.checks(package, "windows-x86_64")
    assert "libgmp*.dll" in windows_suite.forbidden_libraries
    assert "libiconv*.dll" in windows_suite.forbidden_libraries
    assert "zlib*.dll" in windows_suite.forbidden_libraries
    assert "libzstd*.dll" in windows_suite.forbidden_libraries

    original = package.model_dump()
    original["source"]["tag"] = "release-101.0"
    original["source"]["commit"] = "0" * 40
    update = UpdateContext(original)
    old_run = recipe.subprocess.run
    recipe.subprocess.run = lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 0, "e85150f7e615f9f735f2187d48e808dc56adfe01\trefs/tags/release-102.0\n", ""
    )
    try:
        result = recipe.discover_update(update)
    finally:
        recipe.subprocess.run = old_run
    assert result["source"]["tag"] == "release-102.0"
    assert result["version"] == "102.0"
    assert result["dependencies"] == original["dependencies"]
