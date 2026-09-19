import re
import subprocess
from pathlib import Path
from typing import Any

from packaging.version import Version

from muxtools_binaries.build import BuildContext
from muxtools_binaries.checks import AudioCheck, CheckSuite, default_checks
from muxtools_binaries.io import run
from muxtools_binaries.models import Package
from muxtools_binaries.targets import target_spec
from muxtools_binaries.updates import UpdateContext

_QT_OPTIONS = [
    "-release",
    "-static",
    "-opensource",
    "-confirm-license",
    "-nomake",
    "examples",
    "-nomake",
    "tests",
    "-no-gui",
    "-no-widgets",
    "-no-feature-network",
    "-no-feature-concurrent",
    "-no-feature-sql",
    "-no-feature-testlib",
    "-no-feature-xml",
    "-no-dbus",
    "-no-opengl",
    "-no-openssl",
    "-no-glib",
    "-no-icu",
    "-qt-zlib",
    "-qt-pcre",
    "-qt-doubleconversion",
]


def _static_zlib(ctx: BuildContext, source: Path) -> None:
    env = ctx.environment()
    run([source / "configure", f"--prefix={ctx.prefix}", "--static"], cwd=source, env=env)
    run(["make", f"-j{ctx.jobs}"], cwd=source, env=env)
    run(["make", "install"], cwd=source, env=env)


def _static_gmp(ctx: BuildContext, source: Path) -> None:
    ctx.autotools("gmp", source, ["--enable-cxx"], flags_in_compiler=False)


def _static_iconv(ctx: BuildContext, source: Path) -> None:
    ctx.autotools("iconv", source, ["--disable-nls"])


def _static_zstd(ctx: BuildContext, source: Path) -> None:
    ctx.cmake(
        "zstd",
        source / "build" / "cmake",
        {
            "ZSTD_BUILD_PROGRAMS": False,
            "ZSTD_BUILD_SHARED": False,
            "ZSTD_BUILD_STATIC": True,
            "ZSTD_BUILD_TESTS": False,
        },
        install=True,
    )


def _build_boost(ctx: BuildContext, source: Path) -> None:
    target_env = ctx.environment()
    build_env = ctx.host_environment()
    if ctx.windows:
        (source / "user-config.jam").write_text(f"using gcc : mingw : {ctx.toolchain.cxx} ;\n")
    run([source / "bootstrap.sh", "--with-libraries=filesystem,system"], cwd=source, env=build_env)
    b2 = source / "b2"
    options: list[str | Path] = [
        b2,
        f"-j{ctx.jobs}",
        "--with-filesystem",
        "--with-system",
        "variant=release",
        "link=static",
        "runtime-link=static",
        "threading=multi",
        f"architecture={'x86' if ctx.target_info.arch == 'x86_64' else 'arm'}",
        "address-model=64",
        f"cxxflags={target_env['CXXFLAGS']}",
        f"linkflags={target_env['LDFLAGS']}",
        f"--prefix={ctx.prefix}",
        "install",
    ]
    if ctx.windows:
        options.extend(
            [
                "toolset=gcc-mingw",
                "target-os=windows",
                f"--user-config={source / 'user-config.jam'}",
            ]
        )
    run(options, cwd=source, env=build_env)


def _build_qt(ctx: BuildContext, source: Path) -> Path:
    target_env = ctx.environment()
    host_prefix = ctx.work / ctx.tier / "qt-host-prefix"
    if ctx.windows:
        host_build = ctx.work / ctx.tier / "qt-host"
        host_build.mkdir(parents=True, exist_ok=True)
        host_env = ctx.host_environment()
        run(
            [source / "configure", *_QT_OPTIONS, "-prefix", host_prefix],
            cwd=host_build,
            env=host_env,
        )
        run(["cmake", "--build", host_build, "--parallel", ctx.jobs], env=host_env)
        run(["cmake", "--install", host_build], env=host_env)

    target_build = ctx.work / ctx.tier / "qtbase"
    target_build.mkdir(parents=True, exist_ok=True)
    options: list[str | Path] = [*(_QT_OPTIONS), "-prefix", ctx.prefix]
    if ctx.windows:
        toolchain = ctx.work / ctx.tier / "qt-mingw-toolchain.cmake"
        toolchain.write_text(
            "set(CMAKE_SYSTEM_NAME Windows)\n"
            f'set(CMAKE_SYSTEM_PROCESSOR "{ctx.target_info.arch}")\n'
            f'set(CMAKE_C_COMPILER "{ctx.toolchain.cc}")\n'
            f'set(CMAKE_CXX_COMPILER "{ctx.toolchain.cxx}")\n'
            f'set(CMAKE_RC_COMPILER "{ctx.toolchain.windres}")\n'
            f'set(CMAKE_AR "{ctx.toolchain.ar}")\n'
            f'set(CMAKE_RANLIB "{ctx.toolchain.ranlib}")\n'
            "set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)\n"
            "set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)\n"
            "set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)\n"
            "set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)\n"
        )
        options += [
            "-xplatform",
            "win32-g++",
            "-device-option",
            f"CROSS_COMPILE={ctx.toolchain.host}-",
            "-qt-host-path",
            host_prefix,
            "--",
            f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
        ]
    run([source / "configure", *options], cwd=target_build, env=target_env)
    run(["cmake", "--build", target_build, "--parallel", ctx.jobs], env=target_env)
    run(["cmake", "--install", target_build], env=target_env)
    return ctx.prefix / "bin" / "qmake6"


def _verify_configuration(summary: str, build_config: str = "", *, windows: bool = False) -> None:
    if build_config:
        required = {
            "GUI": r"^BUILD_GUI\s*=\s*no\b",
            "FMT": r"^FMT_INTERNAL\s*=\s*(?:yes|1)\b",
            "EBML/Matroska": r"^EBML_MATROSKA_INTERNAL\s*=\s*(?:yes|1)\b",
            "pugixml": r"^PUGIXML_INTERNAL\s*=\s*(?:yes|1)\b",
            "nlohmann-json": r"^NLOHMANN_JSON_INTERNAL\s*=\s*(?:yes|1)\b",
            "utf8-cpp": r"^UTF8CPP_INTERNAL\s*=\s*(?:yes|1)\b",
            "FLAC": r"^FLAC_LIBS\s*=\s*.+",
            "Qt": r"^QT_LIBS_NON_GUI\s*=\s*.+",
            "DVD read": r"^USE_DVDREAD\s*=\s*(?!yes\b).*$",
        }
        if windows:
            required["iconv"] = r"^ICONV_LIBS\s*=\s*.+"
        missing = [name for name, pattern in required.items() if not re.search(pattern, build_config, re.MULTILINE)]
        if missing:
            raise ValueError(f"MKVToolNix build-config is missing: {', '.join(missing)}")
    if not summary:
        raise ValueError("MKVToolNix configure produced no feature summary")
    marker = "Optional features that are NOT built:"
    disabled = summary.partition(marker)
    if not disabled[1]:
        raise ValueError("MKVToolNix configure summary has no disabled-feature section")
    if "FLAC audio" not in disabled[0]:
        raise ValueError("MKVToolNix configure summary is missing: FLAC")
    required = {
        "GUI": "MKVToolNix GUI",
        "DBus": "DBus support",
        "DVD read": "DVD chapter support via libdvdread",
    }
    missing = [name for name, feature in required.items() if feature not in disabled[2]]
    if missing:
        raise ValueError(f"MKVToolNix configure summary is missing: {', '.join(missing)}")


def _use_static_libstdcpp(source: Path) -> None:
    rakefile = source / "Rakefile"
    contents = rakefile.read_text()
    marker = '  "-lstdc++",\n'
    if contents.count(marker) != 1:
        raise ValueError("Cannot locate MKVToolNix's explicit libstdc++ link argument")
    rakefile.write_text(
        contents.replace(
            marker,
            '  "-Wl,-Bstatic",\n  "-lstdc++",\n  "-Wl,-Bdynamic",\n',
        )
    )


def _build_mkvtoolnix(ctx: BuildContext, source: Path, qmake: Path) -> None:
    if not ctx.windows:
        _use_static_libstdcpp(source)
    env = ctx.environment()
    env.update(
        ac_cv_fmt="no",
        ac_cv_header_pugixml_hpp="no",
        ac_cv_nlohmann_jsoncpp="no",
        ac_cv_header_utf8_h="no",
        PKG_CONFIG="pkg-config --static",
    )
    run([source / "autogen.sh"], cwd=source, env=dict(env, NOCONFIGURE="1"))
    options: list[str | Path] = [
        source / "configure",
        f"--prefix={ctx.prefix}",
        "--disable-gui",
        "--disable-dbus",
        "--without-dvdread",
        "--without-gettext",
        f"--with-qmake6={qmake}",
        f"--with-boost={ctx.prefix}",
        f"--with-boost-libdir={ctx.prefix / 'lib'}",
        f"--with-extra-includes={ctx.prefix / 'include'}",
        f"--with-extra-libs={ctx.prefix / 'lib'}",
    ]
    if ctx.windows:
        options.append(f"--host={ctx.toolchain.host}")
    result = run(options, cwd=source, env=env, capture=True)
    summary = getattr(result, "stdout", "")
    if summary:
        print(summary, end="")
    build_config = (source / "build-config").read_text() if (source / "build-config").is_file() else ""
    _verify_configuration(summary, build_config, windows=ctx.windows)
    run(["rake", f"-j{ctx.jobs}", "apps:cli"], cwd=source, env=env)
    run(["rake", "install:programs"], cwd=source, env=env)


def build(ctx: BuildContext) -> None:
    ctx.tier = "baseline"
    dependencies = ctx.package.dependencies
    _static_zlib(ctx, ctx.source("zlib", dependencies["zlib"]))
    _static_zstd(ctx, ctx.source("zstd", dependencies["zstd"]))
    if ctx.windows:
        _static_iconv(ctx, ctx.source("iconv", dependencies["iconv"]))
    ctx.autotools("ogg", ctx.source("ogg", dependencies["ogg"]))
    ctx.autotools("vorbis", ctx.source("vorbis", dependencies["vorbis"]))
    ctx.autotools("flac", ctx.source("flac", dependencies["flac"]), ["--disable-doxygen-docs", "--disable-cpplibs"])
    _static_gmp(ctx, ctx.source("gmp", dependencies["gmp"]))
    _build_boost(ctx, ctx.source("boost", dependencies["boost"]))
    qmake = _build_qt(ctx, ctx.source("qtbase", dependencies["qtbase"]))
    if ctx.package.source is None:
        raise ValueError("MKVToolNix requires a source pin")
    source = ctx.source(ctx.package.name, ctx.package.source)
    _build_mkvtoolnix(ctx, source, qmake)
    ctx.stage_binaries()


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    for name, check in suite.smoke.items():
        check.stdout_prefix = name + " v"
    suite.functional = [
        AudioCheck(
            command="mkvmerge",
            source="flac",
            suffix="mka",
            args=["-o", "{output}", "{source}"],
            lossless=True,
        )
    ]
    suite.forbidden_libraries = (
        [
            "libQt6Core.so*",
            "libFLAC.so*",
            "libogg.so*",
            "libvorbis.so*",
            "libboost_filesystem.so*",
            "libgmp.so*",
            "libz.so*",
            "libzstd.so*",
            "libstdc++.so*",
        ]
        if target_spec(target).os == "linux"
        else [
            "Qt6Core.dll",
            "libFLAC*.dll",
            "libogg*.dll",
            "libvorbis*.dll",
            "libboost*.dll",
            "libgmp*.dll",
            "gmp*.dll",
            "libiconv*.dll",
            "zlib*.dll",
            "libz*.dll",
            "libzstd*.dll",
            "libstdc++-6.dll",
            "libgcc*.dll",
            "libwinpthread*.dll",
        ]
    )
    return suite


def _latest_release(source: dict[str, Any]) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "ls-remote", "--tags", source["repository"]], check=True, text=True, capture_output=True
    )
    refs = dict(line.split()[::-1] for line in result.stdout.splitlines())
    candidates = []
    for ref, commit in refs.items():
        match = re.fullmatch(r"refs/tags/(release-(\d+(?:\.\d+)+))", ref)
        if match:
            candidates.append((Version(match[2]), match[1], refs.get(ref + "^{}", commit)))
    if not candidates:
        raise ValueError(f"No MKVToolNix release tags in {source['repository']}")
    version, tag, commit = max(candidates)
    current = Version(source["tag"].removeprefix("release-"))
    if version <= current:
        return source
    return dict(source, tag=tag, commit=commit)


def discover_update(ctx: UpdateContext) -> dict[str, Any]:
    ctx.data["source"] = _latest_release(ctx.data["source"])
    if ctx.data["source"] != ctx.original["source"]:
        ctx.data["version"] = ctx.data["source"]["tag"].removeprefix("release-")
    return ctx.data


__all__ = ["build", "checks", "discover_update"]
