import re
import subprocess
from pathlib import Path
from typing import Any

from packaging.version import Version
from pydantic import Field

from muxtools_binaries.build import BuildContext
from muxtools_binaries.checks import AudioCheck, CheckSuite, CommandCheck, default_checks
from muxtools_binaries.io import download, extract, run
from muxtools_binaries.models import Model, Package
from muxtools_binaries.updates import UpdateContext, latest_tag


class Options(Model):
    fftw_url: str = Field(pattern=r"^https://")
    fftw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lame_url: str = Field(pattern=r"^https://")
    lame_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _archive(ctx: BuildContext, name: str, url: str, digest: str) -> Path:
    destination = ctx.work / ctx.tier / "sources" / name
    extract(download(url, digest, ctx.cache), destination, "tar.gz")
    sources = list(destination.iterdir())
    if len(sources) != 1 or not sources[0].is_dir():
        raise ValueError(f"Expected one source directory in {name} archive")
    ctx.notices(sources[0], name)
    return sources[0]


def build(ctx: BuildContext) -> None:
    options = Options.model_validate(ctx.package.build)
    ctx.tier = "baseline"

    # zlib has its own configure script; it does not use Autotools options.
    zlib = ctx.source("zlib", ctx.package.dependencies["zlib"])
    env = ctx.environment()
    run([zlib / "configure", f"--prefix={ctx.prefix}", "--static"], cwd=zlib, env=env)
    run(["make", f"-j{ctx.jobs}"], cwd=zlib, env=env)
    run(["make", "install"], cwd=zlib, env=env)

    ctx.autotools("ogg", ctx.source("ogg", ctx.package.dependencies["ogg"]))
    opus = ctx.source("opus", ctx.package.dependencies["opus"])
    # Its autogen.sh downloads a large DNN model used only by optional features.
    run(["autoreconf", "-isf"], cwd=opus, env=env)
    ctx.autotools("opus", opus, ["--disable-extra-programs"])
    ctx.autotools("opusfile", ctx.source("opusfile", ctx.package.dependencies["opusfile"]), ["--disable-http"])
    ctx.autotools(
        "flac", ctx.source("flac", ctx.package.dependencies["flac"]), ["--disable-doxygen-docs", "--disable-cpplibs"]
    )
    ctx.autotools("vorbis", ctx.source("vorbis", ctx.package.dependencies["vorbis"]))
    ctx.autotools("png", ctx.source("png", ctx.package.dependencies["png"]))
    ctx.autotools("lame", _archive(ctx, "lame", options.lame_url, options.lame_sha256), ["--disable-frontend"])
    ctx.autotools(
        "fftw", _archive(ctx, "fftw", options.fftw_url, options.fftw_sha256), ["--disable-threads", "--disable-openmp"]
    )
    ctx.cmake(
        "id3tag", ctx.source("id3tag", ctx.package.dependencies["id3tag"]), {"ZLIB_ROOT": str(ctx.prefix)}, install=True
    )
    ctx.cmake(
        "sndfile",
        ctx.source("sndfile", ctx.package.dependencies["sndfile"]),
        {
            "CMAKE_POLICY_VERSION_MINIMUM": "3.5",
            "BUILD_PROGRAMS": False,
            "BUILD_TESTING": False,
            "ENABLE_CPACK": False,
            "ENABLE_EXTERNAL_LIBS": False,
            "ENABLE_MPEG": False,
        },
        install=True,
    )
    ctx.autotools("wavpack", ctx.source("wavpack", ctx.package.dependencies["wavpack"]))

    if ctx.package.source is None:
        raise ValueError("SoX_ng requires a pinned source")
    sox = ctx.source(ctx.package.name, ctx.package.source)
    ctx.autotools(
        ctx.package.name,
        sox,
        [
            # opusfile.h includes opus_multistream.h without its opus/ directory.
            f"CPPFLAGS=-I{ctx.prefix / 'include'} -I{ctx.prefix / 'include/opus'}",
            "--enable-replace",
            "--disable-openmp",
            "--without-libltdl",
            "--without-ladspa",
            "--without-magic",
            "--without-ffmpeg",
            "--without-mad",
            "--without-speexdsp",
            "--without-twolame",
            "--without-amrnb",
            "--without-amrwb",
            "--without-alsa",
            "--without-pulseaudio",
            "--without-oss",
            "--without-sndio",
            "--without-ao",
            "--without-coreaudio",
            "--without-waveaudio",
            "--without-sunaudio",
            "--with-lame",
            "--with-id3tag",
            "--with-png",
            "--with-fftw",
            "--with-oggvorbis",
            "--with-opus",
            "--with-flac",
            "--with-sndfile",
            "--with-wavpack",
        ],
    )
    ctx.stage_binaries()
    staged = {path.name for path in ctx.stage.iterdir() if path.is_file()}
    expected = {filename for variants in ctx.package.binaries(ctx.target).values() for filename in variants.values()}
    if staged != expected:
        raise ValueError(f"Unexpected staged executables: {sorted(staged ^ expected)}")


def checks(package: Package, target: str) -> CheckSuite:
    suite = default_checks(package)
    suite.smoke["sox"].stdout_prefix = "sox"
    suite.smoke["soxi"] = CommandCheck(exit_codes=[1], stdout_prefix="soxi", stdout_contains=["Usage summary:"])
    suite.functional = [
        AudioCheck(command="sox", args=["{source}", "{output}"], suffix="wav", lossless=True),
        AudioCheck(command="sox", args=["{source}", "{output}"], suffix="flac", lossless=True),
    ]
    return suite


def discover_update(ctx: UpdateContext) -> dict[str, Any]:
    source = ctx.data["source"]
    result = subprocess.run(
        ["git", "ls-remote", "--tags", source["repository"]], check=True, text=True, capture_output=True
    )
    refs = dict(line.split()[::-1] for line in result.stdout.splitlines())
    candidates = []
    for ref, commit in refs.items():
        match = re.fullmatch(r"refs/tags/(sox_ng-(\d+(?:\.\d+)+))", ref)
        if match:
            candidates.append((Version(match[2]), match[1], refs.get(ref + "^{}", commit)))
    if not candidates:
        raise ValueError("No SoX_ng release tags found")
    version, tag, commit = max(candidates)
    if version > Version(source["tag"].removeprefix("sox_ng-")):
        source.update(tag=tag, commit=commit)
    ctx.data["dependencies"] = {name: latest_tag(pin) for name, pin in ctx.data["dependencies"].items()}
    if ctx.pins_changed:
        base = source["tag"].removeprefix("sox_ng-")
        ctx.data["version"] = (
            base if source != ctx.original["source"] else f"{base}.r{ctx.original['version_code'] + 1}"
        )
    return ctx.data


__all__ = ["Options", "build", "checks", "discover_update"]
