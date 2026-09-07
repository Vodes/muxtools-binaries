import shutil
from pathlib import Path

from .build import BuildContext
from .io import extract, run


def audio(ctx: BuildContext) -> None:
    if ctx.package.source is None:
        raise ValueError("Audio builds require a source pin")
    for tier in ctx.config.cpu_levels:
        ctx.tier = tier
        for name, pin in ctx.package.dependencies.items():
            options = ["--disable-http"] if name == "opusfile" else []
            if name == "flac":
                options = ["--disable-doxygen-docs", "--disable-cpplibs"]
            ctx.autotools(name, ctx.source(name, pin), options)
        source = ctx.source(ctx.package.name, ctx.package.source)
        options = ["--disable-doxygen-docs", "--disable-cpplibs"] if ctx.package.name == "flac" else []
        ctx.autotools(ctx.package.name, source, options)
        ctx.stage_binaries()


def x265(ctx: BuildContext) -> None:
    if ctx.package.source is None:
        raise ValueError("x265 builds require a source pin")
    for tier in ctx.config.cpu_levels:
        ctx.tier = tier
        source = ctx.source("x265", ctx.package.source)
        env = ctx.environment()
        common = [
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
            "-DENABLE_SHARED=OFF",
            "-DENABLE_ASSEMBLY=ON",
            "-DENABLE_LIBNUMA=OFF",
            f"-DCMAKE_INSTALL_PREFIX={ctx.prefix}",
            f"-DCMAKE_C_COMPILER={env['CC']}",
            f"-DCMAKE_CXX_COMPILER={env['CXX']}",
            f"-DCMAKE_AR={shutil.which(env['AR'])}",
            f"-DCMAKE_RANLIB={shutil.which(env['RANLIB'])}",
            f"-DCMAKE_EXE_LINKER_FLAGS={env['LDFLAGS']}",
        ]
        if ctx.windows:
            common += [
                "-DCMAKE_SYSTEM_NAME=Windows",
                "-DCMAKE_SYSTEM_PROCESSOR=x86_64",
                "-DCMAKE_LINK_DEPENDS_USE_LINKER=OFF",
                f"-DCMAKE_RC_COMPILER={env['WINDRES']}",
            ]
        libraries: list[Path] = []
        for depth in (12, 10, 8):
            build = ctx.work / tier / f"x265-{depth}"
            args: list[str | Path] = [
                "cmake",
                "-S",
                source / "source",
                "-B",
                build,
                *common,
                f"-DHIGH_BIT_DEPTH={'ON' if depth > 8 else 'OFF'}",
                f"-DMAIN12={'ON' if depth == 12 else 'OFF'}",
                f"-DEXPORT_C_API={'ON' if depth == 8 else 'OFF'}",
                f"-DENABLE_CLI={'ON' if depth == 8 else 'OFF'}",
            ]
            if depth == 8:
                args += ["-DLINKED_10BIT=ON", "-DLINKED_12BIT=ON", "-DEXTRA_LIB=" + ";".join(map(str, libraries))]
            run(args, env=env)
            run(["cmake", "--build", build, "--parallel", ctx.jobs], env=env)
            if depth > 8:
                libraries.append(build / "libx265.a")
            else:
                binary = build / ("x265.exe" if ctx.windows else "x265")
                destination = ctx.stage / ctx.package.binaries(ctx.target)["x265"][tier]
                shutil.copy2(binary, destination)
                destination.chmod(0o755)


def imported(ctx: BuildContext) -> None:
    if ctx.config.asset is None:
        raise ValueError("Imported packages require an asset")
    asset = ctx.asset()
    if ctx.config.asset.format == "appimage":
        destination = ctx.stage / "MKVToolNix.AppImage"
        shutil.copy2(asset, destination)
        destination.chmod(0o755)
        for name in ctx.package.executables:
            wrapper = ctx.stage / name
            wrapper.write_text(
                '#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                + 'exec bash -c \'exec -a "$0" "$@"\' '
                + name
                + ' "$base/MKVToolNix.AppImage" "$@"\n'
            )
            wrapper.chmod(0o755)
        return
    unpacked = ctx.work / "import"
    extract(asset, unpacked, ctx.config.asset.format)
    extension = ".exe" if ctx.windows else ""
    if ctx.package.name == "mkvtoolnix":
        matches = list(unpacked.rglob("mkvmerge.exe"))
        if len(matches) != 1:
            raise ValueError("Expected exactly one MKVToolNix installation")
        shutil.copytree(matches[0].parent, ctx.stage, dirs_exist_ok=True)
    else:
        for name in ctx.package.executables:
            matches = list(unpacked.rglob(name + extension))
            if len(matches) != 1:
                raise ValueError(f"Expected exactly one imported {name}")
            shutil.copy2(matches[0], ctx.stage / (name + extension))
        for path in unpacked.rglob("*"):
            if path.is_file() and (
                path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")) or "license" in path.parts
            ):
                output = ctx.stage / "licenses" / path.relative_to(unpacked)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, output)
    for name in ctx.package.executables:
        (ctx.stage / (name + extension)).chmod(0o755)
