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
    names = ["zlib", "zstd", "ogg", "vorbis", "flac", "boost", "qtbase", "gmp", "iconv", "mkvtoolnix"]
    sources = {name: tmp_path / name for name in names}
    for source in sources.values():
        source.mkdir()
    (sources["mkvtoolnix"] / "Rakefile").write_text('  "-lstdc++",\n')
    (sources["vorbis"] / "configure.ac").write_text("CFLAGS=' -force_cpusubtype_ALL'\n" * 3)
    qt_header = sources["qtbase"] / "src/corelib/thread/qyieldcpu.h"
    qt_header.parent.mkdir(parents=True)
    qt_header.write_text("#include <QtCore/qtconfigmacros.h>\n")
    calls = []

    monkeypatch.setattr(ctx, "source", lambda name, pin: sources[name])
    monkeypatch.setattr(
        ctx,
        "autotools",
        lambda name, source, options=(), **kwargs: calls.append(
            ("autotools", name, list(options), kwargs.get("flags_in_compiler", True))
        ),
    )
    monkeypatch.setattr(
        ctx,
        "cmake",
        lambda name, source, definitions, *, install=False: calls.append(("cmake", name, source, definitions, install)),
    )
    monkeypatch.setattr(ctx, "notices", lambda source, name: None)
    monkeypatch.setattr(ctx, "stage_binaries", lambda: calls.append(("stage",)))

    def fake_run(args, **kwargs):
        command = [str(arg) for arg in args]
        calls.append((command, kwargs))
        if command == ["brew", "--prefix", "docbook-xsl"]:
            return SimpleNamespace(stdout="/opt/homebrew/opt/docbook-xsl\n")
        if command == ["brew", "--prefix", "libxslt"]:
            return SimpleNamespace(stdout="/opt/homebrew/opt/libxslt\n")
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
    assert package.targets["linux-arm64"].toolchain == "gcc"
    assert package.targets["windows-x86_64"].asset is None
    assert package.targets["windows-arm64"].toolchain == "clang"
    assert package.targets["macos-arm64"].toolchain == "clang"
    assert "-shared-libgcc" in package.targets["linux-x86_64"].extra_ldflags
    assert package.targets["linux-x86_64"].runtime.requirements == ["libgcc_s.so.1"]
    assert "-shared-libgcc" in package.targets["linux-arm64"].extra_ldflags
    assert package.targets["linux-arm64"].runtime.requirements == ["libgcc_s.so.1"]
    assert "-DFLAC__NO_DLL" in package.targets["windows-x86_64"].extra_cflags
    assert "-DFLAC__NO_DLL" in package.targets["windows-x86_64"].extra_cxxflags
    assert "-DFLAC__NO_DLL" in package.targets["windows-arm64"].extra_cflags
    assert {"zlib", "zstd", "ogg", "vorbis", "flac", "boost", "qtbase", "gmp", "iconv"} == set(package.dependencies)


def test_linux_build_orchestrates_static_dependencies_and_cli_only_mkv(tmp_path, monkeypatch):
    ctx = _context(tmp_path, "linux-x86_64")
    calls, sources = _mock_build(ctx, monkeypatch, tmp_path)

    recipe.build(ctx)

    assert [entry[1] for entry in calls if entry[0] == "autotools"] == ["ogg", "vorbis", "flac", "gmp"]
    assert next(entry for entry in calls if entry[:2] == ("autotools", "gmp"))[2:] == (["--enable-cxx"], False)
    zstd_cmake = next(entry for entry in calls if entry[:2] == ("cmake", "zstd"))
    assert zstd_cmake[2] == tmp_path / "zstd" / "build" / "cmake"
    assert zstd_cmake[3]["ZSTD_BUILD_SHARED"] is False
    assert zstd_cmake[3]["ZSTD_BUILD_STATIC"] is True
    assert zstd_cmake[4] is True
    rakefile = (sources["mkvtoolnix"] / "Rakefile").read_text()
    assert '  "-Wl,-Bstatic",\n  "-lstdc++",\n  "-Wl,-Bdynamic",\n' in rakefile
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

    assert [entry[1] for entry in calls if entry[0] == "autotools"] == [
        "iconv",
        "ogg",
        "vorbis",
        "flac",
        "gmp",
    ]

    qt_configures = [
        entry for entry in calls if isinstance(entry[0], list) and entry[0] and Path(entry[0][0]).name == "configure"
    ]
    assert any(entry[1]["env"]["CC"] == "gcc" for entry in qt_configures)
    target_qt = next(entry for entry in qt_configures if "-qt-host-path" in entry[0])
    assert "-xplatform" in target_qt[0]
    assert any(argument.startswith("-DCMAKE_TOOLCHAIN_FILE=") for argument in target_qt[0])
    toolchain_argument = next(argument for argument in target_qt[0] if argument.startswith("-DCMAKE_TOOLCHAIN_FILE="))
    assert "CMAKE_SYSTEM_NAME Windows" in Path(toolchain_argument.split("=", 1)[1]).read_text()
    mkv_configure = next(entry for entry in calls if isinstance(entry[0], list) and "--disable-gui" in entry[0])
    assert "--enable-static" not in mkv_configure[0]
    assert mkv_configure[1]["env"]["CC"] == "x86_64-w64-mingw32-gcc"
    assert (sources["mkvtoolnix"] / "Rakefile").read_text() == '  "-lstdc++",\n'


def test_windows_arm64_build_uses_qt_clang_spec(tmp_path, monkeypatch):
    ctx = _context(tmp_path, "windows-arm64")
    calls, _ = _mock_build(ctx, monkeypatch, tmp_path)

    recipe.build(ctx)

    qt_configures = [
        entry for entry in calls if isinstance(entry[0], list) and "-qt-host-path" in entry[0]
    ]
    assert len(qt_configures) == 1
    xplatform = qt_configures[0][0].index("-xplatform")
    assert qt_configures[0][0][xplatform + 1] == "win32-clang-g++"
    assert "CROSS_COMPILE=/opt/llvm-mingw/bin/aarch64-w64-mingw32-" in qt_configures[0][0]


def test_macos_build_keeps_native_cxx_runtime_linking(tmp_path, monkeypatch):
    ctx = _context(tmp_path, "macos-arm64")
    calls, sources = _mock_build(ctx, monkeypatch, tmp_path)

    recipe.build(ctx)

    assert "force_cpusubtype_ALL" not in (sources["vorbis"] / "configure.ac").read_text()
    assert "#include <arm_acle.h>" in (sources["qtbase"] / "src/corelib/thread/qyieldcpu.h").read_text()
    assert (sources["mkvtoolnix"] / "Rakefile").read_text() == '  "-lstdc++",\n'
    mkv_configure = next(entry for entry in calls if isinstance(entry[0], list) and "--disable-gui" in entry[0])
    assert mkv_configure[1]["env"]["CC"] == "clang"
    assert "--with-docbook-xsl-root=/opt/homebrew/opt/docbook-xsl/docbook-xsl" in mkv_configure[0]
    assert "--with-xsltproc=/opt/homebrew/opt/libxslt/bin/xsltproc" in mkv_configure[0]
    assert not any(entry[:2] == ("autotools", "iconv") for entry in calls)


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
