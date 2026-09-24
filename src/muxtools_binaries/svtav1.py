"""Shared build steps for the SVT-AV1 forks."""

import shutil
from pathlib import Path

from .build import BuildContext
from .checks import CheckSuite, VideoCheck, default_checks
from .io import run
from .models import Package


def build_hdr_dependencies(ctx: BuildContext) -> None:
    ctx.tier = "baseline"
    ctx.shared_prefix = ctx.prefix
    dovi = ctx.source("dovi", ctx.package.dependencies["dovi"])
    ctx.cargo_cinstall(dovi / "dolby_vision", features=("capi",))
    hdr10plus = ctx.source("hdr10plus", ctx.package.dependencies["hdr10plus"])
    ctx.cargo_cinstall(hdr10plus / "hdr10plus", features=("capi",))


def build_ffms2_dependencies(ctx: BuildContext) -> None:
    if ctx.target_info.os == "linux":
        gcc = ctx.toolchain.compatibility_cc or ctx.toolchain.cc
        libatomic = Path(run([gcc, "-print-file-name=libatomic.a"], capture=True).stdout.strip())
        if not libatomic.is_absolute() or not libatomic.is_file():
            raise FileNotFoundError(f"GCC static libatomic archive is missing: {libatomic}")
        (ctx.prefix / "lib").mkdir(parents=True, exist_ok=True)
        shutil.copy2(libatomic, ctx.prefix / "lib/libatomic.a")

    zlib = ctx.source("zlib", ctx.package.dependencies["zlib"])
    env = ctx.dependency_environment()
    run([zlib / "configure", f"--prefix={ctx.prefix}", "--static"], cwd=zlib, env=env)
    run(["make", f"-j{ctx.jobs}"], cwd=zlib, env=env)
    run(["make", "install"], cwd=zlib, env=env)

    ffmpeg = ctx.source("ffmpeg", ctx.package.dependencies["ffmpeg"])
    ffmpeg_build = ctx.work / "baseline" / "ffmpeg-build"
    ffmpeg_build.mkdir(parents=True, exist_ok=True)
    configure: list[str | Path] = [
        ffmpeg / "configure",
        f"--prefix={ctx.prefix}",
        "--libdir=" + str(ctx.prefix / "lib"),
        "--disable-autodetect",
        "--disable-programs",
        "--disable-doc",
        "--disable-debug",
        "--disable-shared",
        "--enable-static",
        "--enable-pic",
        "--disable-everything",
        "--enable-avcodec",
        "--enable-avformat",
        "--enable-avutil",
        "--enable-swscale",
        "--enable-swresample",
        "--enable-zlib",
        "--pkg-config-flags=--static",
        f"--cc={ctx.toolchain.cc}",
        f"--cxx={ctx.toolchain.cxx}",
        f"--ar={ctx.toolchain.ar}",
        f"--ranlib={ctx.toolchain.ranlib}",
        f"--nm={ctx.toolchain.nm}",
        "--enable-protocol=file",
        "--enable-demuxer=mov,matroska,mpegts,avi,image2,rawvideo",
        "--enable-parser=h264,hevc,av1,mpegvideo,aac",
        "--enable-decoder=h264,hevc,av1,mpeg2video,mpeg4,rawvideo,mjpeg",
    ]
    if ctx.windows:
        host = ctx.toolchain.host
        if host is None:
            raise ValueError("Windows target needs a cross-compiler host")
        configure.extend(
            [
                "--enable-cross-compile",
                "--target-os=mingw32",
                f"--arch={ctx.target_info.arch}",
                f"--cross-prefix={host}-",
                f"--windres={ctx.toolchain.windres}",
            ]
        )
    run(configure, cwd=ffmpeg_build, env=env)
    run(["make", f"-j{ctx.jobs}"], cwd=ffmpeg_build, env=env)
    run(["make", "install"], cwd=ffmpeg_build, env=env)

    ffms2 = ctx.source("ffms2", ctx.package.dependencies["ffms2"])
    makefile = ffms2 / "Makefile.am"
    makefile_text = makefile.read_text()
    indexer = "bin_PROGRAMS = src/index/ffmsindex"
    if makefile_text.count(indexer) != 1:
        raise ValueError("FFMS2 indexer build declaration changed")
    makefile.write_text(makefile_text.replace(indexer, "bin_PROGRAMS ="))
    (ffms2 / "src/config").mkdir(parents=True, exist_ok=True)
    run(["autoreconf", "-isf"], cwd=ffms2, env=env)
    ctx.autotools("ffms2", ffms2)
    if not (ctx.prefix / "lib/pkgconfig/ffms2.pc").is_file():
        raise ValueError("FFMS2 static installation is missing")


def build_encoder(ctx: BuildContext, *, essential: bool = False) -> None:
    if ctx.package.source is None:
        raise ValueError("SVT-AV1 requires a pinned source")
    for tier in ctx.config.cpu_levels:
        ctx.tier = tier
        source = ctx.source(ctx.package.name, ctx.package.source)
        app_cmake = source / "Source/App/CMakeLists.txt"
        app_text = app_cmake.read_text()
        if "${BASE}${SUFFIX}" not in app_text:
            raise ValueError("SVT-AV1 external library wiring changed")
        app_cmake.write_text(
            app_text.replace('"-l${BASE}${SUFFIX}"', '"${BASE}"').replace('"${BASE}${SUFFIX}"', '"${BASE}"')
        )
        if essential:
            root_cmake = source / "CMakeLists.txt"
            root_text = root_cmake.read_text()
            if "${BASE}${SUFFIX}" not in root_text:
                raise ValueError("Essential FFMS2 library wiring changed")
            root_cmake.write_text(
                root_text.replace('"-l${BASE}${SUFFIX}"', '"${BASE}"').replace('"${BASE}${SUFFIX}"', '"${BASE}"')
            )
            bundled = source / "third_party/webm/libwebm"
            if not (bundled / "mkvmuxer/mkvmuxer.cc").is_file():
                raise ValueError("Essential's bundled WebM source is missing")
            license_dir = ctx.stage / "licenses" / "libwebm"
            license_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled / "LICENSE.TXT", license_dir / "LICENSE.TXT")
        definitions: dict[str, str | bool | int] = {
            "BUILD_SHARED_LIBS": False,
            "CMAKE_TRY_COMPILE_TARGET_TYPE": "STATIC_LIBRARY",
            "BUILD_DEC": False,
            "BUILD_TESTING": False,
            "BUILD_APPS": True,
            "NATIVE": False,
            "SVT_AV1_LTO": False,
            "EXT_LIB_STATIC": True,
            "LIBDOVI_FOUND": True,
            "LIBHDR10PLUS_RS_FOUND": True,
        }
        if ctx.windows:
            # ThinLTO bitcode hides the pointer size from CMake's compiler ABI probe.
            definitions["CMAKE_SIZEOF_VOID_P"] = 8
            if ctx.target_info.arch == "x86_64":
                definitions["CMAKE_ASM_NASM_OBJECT_FORMAT"] = "win64"
        if essential:
            definitions.update(USE_FFMS2=True, USE_WEBM_IO=True)
        result = ctx.cmake(ctx.package.name, source, definitions, install=True)
        binary = ctx.prefix / "bin" / ("SvtAv1EncApp" + ctx.target_info.executable_suffix)
        if not binary.is_file():
            binary = result / "Bin" / "Release" / ("SvtAv1EncApp" + ctx.target_info.executable_suffix)
        ctx.stage_binary(binary, "SvtAv1EncApp")


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    args = ["-i", "{source}", "-w", "{width}", "-h", "{height}"]
    if package.name == "svtav1-essential":
        args += [
            "--fps-num",
            "24",
            "--fps-denom",
            "1",
            "--input-depth",
            "{bit_depth}",
            "--frames",
            "1",
            "--speed",
            "faster",
            "--webm",
            "0",
        ]
    else:
        args += ["--fps", "24", "--input-depth", "{bit_depth}", "-n", "1", "--preset", "11"]
    args += ["-b", "{output}"]
    suite.functional = [
        VideoCheck(
            command="SvtAv1EncApp",
            suffix="ivf",
            bit_depth=10,
            args=args,
        )
    ]
    return suite
