import os
import platform
import re
import shlex
import shutil
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field

from .checks import CheckSuite, reject_static_windows_runtimes
from .io import download, extract, run
from .models import ArchiveSource, GitSource, Model, Package
from .recipes import load_recipe, recipe_checks, recipe_options
from .targets import BUILDERS, normalize_arch, normalize_os, target_spec, toolchain_spec


class AutotoolsOptions(Model):
    configure: list[str] = Field(default_factory=list)
    dependencies: dict[str, list[str]] = Field(default_factory=dict)


def _needs_mingw_host(source: Path) -> bool:
    for name in ("configure.ac", "configure.in"):
        path = source / name
        if path.is_file():
            text = path.read_text().casefold()
            if "host_os" in text and "mingw" in text and "msvc" not in text:
                return True
    return False


def build_autotools(ctx: "BuildContext") -> None:
    options = recipe_options(ctx.package, ctx.target, AutotoolsOptions)
    if options.dependencies.keys() - ctx.package.dependencies.keys():
        raise ValueError("Configure options reference unknown dependencies")
    if ctx.package.source is None:
        raise ValueError("Autotools builds require a source pin")
    for tier in ctx.config.cpu_levels:
        ctx.tier = tier
        for name, pin in ctx.package.dependencies.items():
            ctx.autotools(name, ctx.source(name, pin), options.dependencies.get(name, []))
        ctx.autotools(ctx.package.name, ctx.source(ctx.package.name, ctx.package.source), options.configure)
        ctx.stage_binaries()


class BuildContext:
    def __init__(self, root: Path, package: Package, target: str, work: Path, stage: Path, jobs: int) -> None:
        self.root, self.package, self.target = root, package, target
        self.work, self.stage, self.jobs = work, stage, jobs
        self.config = package.targets[target]
        self.target_info = target_spec(target)
        self.toolchain = toolchain_spec(target, self.config.toolchain)
        self.windows = self.target_info.os == "windows"
        self.cache = root / "build" / "downloads"
        self.tier = "baseline"

    def source(self, name: str, pin: GitSource | ArchiveSource) -> Path:
        if isinstance(pin, ArchiveSource):
            return self.archive_source(name, pin)
        path = self.work / self.tier / "sources" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "init", path])
        run(["git", "-C", path, "remote", "add", "origin", pin.repository])
        run(["git", "-C", path, "fetch", "--depth=1", "origin", pin.commit])
        run(["git", "-C", path, "checkout", "--detach", "FETCH_HEAD"])
        if run(["git", "-C", path, "rev-parse", "HEAD"], capture=True).stdout.strip() != pin.commit:
            raise ValueError(f"Source revision mismatch: {name}")
        if pin.recursive:
            run(["git", "-C", path, "submodule", "sync", "--recursive"])
            run(["git", "-C", path, "submodule", "update", "--init", "--recursive"])
        # Native builds inherit host Git settings; force a lightweight tag even when tag.gpgSign is enabled.
        run(["git", "-C", path, "tag", "--no-sign", pin.tag, pin.commit])
        self.notices(path, name)
        if pin.recursive:
            submodules = run(
                ["git", "-C", path, "submodule", "foreach", "--recursive", "--quiet", 'printf "%s\\0" "$displaypath"'],
                capture=True,
            ).stdout
            for relative in filter(None, submodules.split("\0")):
                self.notices(path / relative, str(Path(name) / relative))
        return path

    def archive_source(self, name: str, pin: ArchiveSource) -> Path:
        """Download and unpack an archive source into one validated source directory."""
        archive = download(pin.url, pin.sha256, self.cache)
        destination = self.work / self.tier / "sources" / name
        extract(archive, destination, pin.format)
        sources = list(destination.iterdir())
        if len(sources) != 1 or not sources[0].is_dir():
            raise ValueError(f"Expected one source directory in {name} archive")
        source = sources[0]
        self.notices(source, name)
        return source

    def notices(self, source: Path, name: str) -> None:
        output = self.stage / "licenses" / name
        for path in source.iterdir():
            if path.is_file() and path.name.upper().startswith(("COPYING", "LICENSE", "NOTICE", "PATENTS")):
                output.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, output / path.name)

    @property
    def prefix(self) -> Path:
        return self.work / self.tier / "prefix"

    def build_environment(self) -> dict[str, str]:
        removed = {
            "CC",
            "CXX",
            "AR",
            "NM",
            "LD",
            "RANLIB",
            "WINDRES",
            "RC",
            "CFLAGS",
            "CXXFLAGS",
            "CPPFLAGS",
            "LDFLAGS",
            "CPATH",
            "C_INCLUDE_PATH",
            "CPLUS_INCLUDE_PATH",
            "LIBRARY_PATH",
            "INCLUDE",
            "LIB",
            "CL",
            "_CL_",
            "RCFLAGS",
            "PKG_CONFIG_PATH",
            "PKG_CONFIG_LIBDIR",
            "PKG_CONFIG_SYSROOT_DIR",
            "CMAKE_PREFIX_PATH",
            "CMAKE_TOOLCHAIN_FILE",
        }
        env = {key: value for key, value in os.environ.items() if key not in removed}
        tools = {
            "CC": self.toolchain.cc,
            "CXX": self.toolchain.cxx,
            "AR": self.toolchain.ar,
            "RANLIB": self.toolchain.ranlib,
            "NM": self.toolchain.nm,
            "LD": self.toolchain.linker,
        }
        if self.toolchain.windres:
            tools["WINDRES"] = self.toolchain.windres
            tools["RC"] = self.toolchain.windres
        common = [*self.target_info.cpu_flags[self.tier], *self.toolchain.default_cflags]
        cxx = list(self.toolchain.default_cxxflags)
        link = list(self.toolchain.default_ldflags)
        if "-shared-libgcc" in self.config.extra_ldflags:
            link = [flag for flag in link if flag != "-static-libgcc"]
        if not self.windows and self.toolchain.clang and self.toolchain.compatibility_cc:
            gcc_directory = Path(
                run([self.toolchain.compatibility_cc, "-print-libgcc-file-name"], capture=True).stdout.strip()
            ).parent
            common.append(f"--gcc-install-dir={gcc_directory}")
        if self.config.lto:
            common += ["-flto=thin" if self.config.lto == "thin" else "-flto"]
        env.update(tools)
        env.update(
            CFLAGS=shlex.join(common + self.config.extra_cflags),
            CXXFLAGS=shlex.join(common + cxx + self.config.extra_cxxflags),
            LDFLAGS=shlex.join(common + link + [f"-L{self.prefix / 'lib'}"] + self.config.extra_ldflags),
            CPPFLAGS=shlex.join([f"-I{self.prefix / 'include'}"]),
            PKG_CONFIG_LIBDIR=str(self.prefix / "lib/pkgconfig"),
            PKG_CONFIG_PATH="",
            PKG_CONFIG_SYSROOT_DIR="",
            SOURCE_DATE_EPOCH="0",
        )
        if self.target_info.os == "macos":
            env["MACOSX_DEPLOYMENT_TARGET"] = "12.0"
        if self.toolchain.rcflags:
            env["RCFLAGS"] = shlex.join(self.toolchain.rcflags)
        return env

    def environment(self) -> dict[str, str]:
        return self.build_environment()

    def host_environment(self) -> dict[str, str]:
        builder = BUILDERS[self.target_info.builder]
        if self.target_info.name == builder.host_target:
            return self.build_environment()
        host_target = target_spec(builder.host_target)
        toolchain = toolchain_spec(host_target.name, "gcc")
        flags = list(host_target.cpu_flags["baseline"])
        env = self.build_environment()
        for name in ("WINDRES", "RC", "RCFLAGS"):
            env.pop(name, None)
        env.update(
            CC=toolchain.cc,
            CXX=toolchain.cxx,
            AR=toolchain.ar,
            RANLIB=toolchain.ranlib,
            NM=toolchain.nm,
            LD=toolchain.linker,
            CFLAGS=shlex.join(flags),
            CXXFLAGS=shlex.join(flags),
            LDFLAGS=shlex.join([*flags, *toolchain.default_ldflags]),
            CPPFLAGS="",
            PKG_CONFIG_LIBDIR="",
            PKG_CONFIG_PATH="",
        )
        return env

    def autotools(
        self, name: str, source: Path, options: Sequence[str] = (), *, flags_in_compiler: bool = True
    ) -> None:
        env = self.build_environment()
        if flags_in_compiler:
            # Keep configure's own CFLAGS/CXXFLAGS defaults while adding target requirements.
            env["CC"] += " " + env.pop("CFLAGS")
            env["CXX"] += " " + env.pop("CXXFLAGS")
        if not (source / "configure").exists():
            if (source / "autogen.sh").exists():
                run(["sh", "autogen.sh"], cwd=source, env=dict(env, NOCONFIGURE="1"))
            else:
                run(["autoreconf", "-fiv"], cwd=source, env=env)
        build = self.work / self.tier / name
        build.mkdir(parents=True, exist_ok=True)
        args: list[str | Path] = [
            source / "configure",
            f"--prefix={self.prefix}",
            "--disable-shared",
            "--enable-static",
            *options,
        ]
        configure_host = self.toolchain.host
        if configure_host and self.toolchain.abi == "msvc" and _needs_mingw_host(source):
            configure_host = f"{configure_host.partition('-')[0]}-w64-mingw32"
        if configure_host:
            args.append(f"--host={configure_host}")
        run(args, cwd=build, env=env)
        run(["make", f"-j{self.jobs}"], cwd=build, env=env)
        run(["make", "install"], cwd=build, env=env)

    def stage_binaries(self) -> None:
        for executable in self.package.executables:
            source = self.prefix / "bin" / (executable + self.target_info.executable_suffix)
            self.stage_binary(source, executable)

    def asset(self) -> Path:
        asset = self.config.asset
        if not asset:
            raise ValueError("No imported asset configured")
        return download(asset.url, asset.sha256, self.cache)

    def cmake(
        self,
        name: str,
        source: Path,
        definitions: dict[str, str | bool | int],
        *,
        install: bool = False,
        clang_cl: bool = False,
    ) -> Path:
        if clang_cl and self.toolchain.abi != "msvc":
            raise ValueError("clang_cl=True requires the clang-msvc toolchain")
        env = self.build_environment()
        if clang_cl:
            env.update(self._clang_cl_environment())
        build = self.work / self.tier / name
        options: dict[str, str | bool | int] = {
            "BUILD_SHARED_LIBS": False,
            "CMAKE_BUILD_TYPE": "Release",
            "CMAKE_INSTALL_LIBDIR": "lib",
            "CMAKE_PREFIX_PATH": str(self.prefix),
            "CMAKE_FIND_USE_PACKAGE_REGISTRY": False,
            "CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY": False,
            **definitions,
        }
        if BUILDERS[self.target_info.builder].platform is not None:
            options.update(
                CMAKE_FIND_ROOT_PATH=f"{self.prefix};{self.toolchain.sysroot or ''}",
                CMAKE_FIND_ROOT_PATH_MODE_PROGRAM="NEVER",
                CMAKE_FIND_ROOT_PATH_MODE_LIBRARY="ONLY",
                CMAKE_FIND_ROOT_PATH_MODE_INCLUDE="ONLY",
                CMAKE_FIND_ROOT_PATH_MODE_PACKAGE="ONLY",
            )
        options.update(
            CMAKE_INSTALL_PREFIX=str(self.prefix),
            CMAKE_C_COMPILER=env["CC"],
            CMAKE_CXX_COMPILER=env["CXX"],
            CMAKE_AR=shutil.which(env["AR"]) or env["AR"],
            CMAKE_RANLIB=shutil.which(env["RANLIB"]) or env["RANLIB"],
            CMAKE_EXE_LINKER_FLAGS=env["LDFLAGS"],
        )
        if self.windows:
            options.update(
                CMAKE_SYSTEM_NAME="Windows",
                CMAKE_SYSTEM_PROCESSOR="ARM64" if self.target_info.arch == "arm64" else "AMD64",
                CMAKE_LINK_DEPENDS_USE_LINKER=False,
                CMAKE_RC_COMPILER=env["WINDRES"],
            )
        if self.toolchain.abi == "msvc":
            options.update(
                CMAKE_LINKER=self.toolchain.linker,
                CMAKE_MT=self.toolchain.mt or "",
                CMAKE_MSVC_RUNTIME_LIBRARY="MultiThreaded",
                CMAKE_POLICY_DEFAULT_CMP0091="NEW",
                CMAKE_C_COMPILER_TARGET=self.toolchain.host or "",
                CMAKE_CXX_COMPILER_TARGET=self.toolchain.host or "",
            )
        args = [
            f"-D{key}={'ON' if value is True else 'OFF' if value is False else value}" for key, value in options.items()
        ]
        run(["cmake", "-S", source, "-B", build, "-G", "Ninja", *args], env=env)
        run(["cmake", "--build", build, "--parallel", self.jobs], env=env)
        if install:
            run(["cmake", "--install", build], env=env)
        return build

    def _clang_cl_environment(self) -> dict[str, str]:
        if not self.toolchain.host or not self.toolchain.sysroot or not self.toolchain.windres:
            raise ValueError("Incomplete clang-msvc toolchain")
        common = [
            *(f"/clang:{flag}" for flag in self.target_info.cpu_flags[self.tier]),
            f"--target={self.toolchain.host}",
            "/winsysroot",
            self.toolchain.sysroot,
            "/MT",
        ]
        if self.config.lto:
            common.append("/clang:-flto=thin" if self.config.lto == "thin" else "/clang:-flto")
        library_paths = [f"/libpath:{flag[2:]}" for flag in self.toolchain.default_ldflags if flag.startswith("-L")]
        return {
            "CC": "/opt/llvm-mingw/bin/clang-cl",
            "CXX": "/opt/llvm-mingw/bin/clang-cl",
            "AR": "/opt/llvm-mingw/bin/llvm-lib",
            "RANLIB": self.toolchain.ranlib,
            "NM": self.toolchain.nm,
            "LD": self.toolchain.linker,
            "WINDRES": "/opt/llvm-mingw/bin/llvm-rc",
            "RC": "/opt/llvm-mingw/bin/llvm-rc",
            "CFLAGS": shlex.join([*common, *self.config.extra_cflags]),
            "CXXFLAGS": shlex.join([*common, "/EHsc", *self.config.extra_cxxflags]),
            "LDFLAGS": shlex.join([f"/libpath:{self.prefix / 'lib'}", *library_paths, *self.config.extra_ldflags]),
            "CPPFLAGS": shlex.join([f"/I{self.prefix / 'include'}"]),
        }

    def stage_binary(self, source: Path, executable: str) -> None:
        destination = self.stage / self.package.binaries(self.target)[executable][self.tier]
        shutil.copy2(source, destination)
        destination.chmod(0o755)


def produce(root: Path, package: Package, target: str, jobs: int) -> tuple[Path, CheckSuite]:
    target_info = target_spec(target)
    builder = BUILDERS[target_info.builder]
    if builder.platform is not None and os.environ.get("MUXTOOLS_BUILDER") != "1":
        raise ValueError("Container builds must run inside the builder image; use the build command without --inside")
    if builder.platform is None and (
        normalize_os(platform.system()) != target_info.os or normalize_arch(platform.machine()) != target_info.arch
    ):
        raise ValueError(f"Native builds for {target} require {target_info.os}/{target_info.arch}")
    workroot = root / "build" / package.name / target
    workroot.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=workroot))
    stage = work / "stage"
    stage.mkdir()
    context = BuildContext(root, package, target, work, stage, jobs)
    recipe = load_recipe(root, package.name)
    checks = recipe_checks(recipe, package, target)
    recipe.build(context)
    if context.windows and package.type == "source-build":
        for executable in list(stage.glob("*.exe")):
            imports = run(["objdump", "-p", executable], capture=True).stdout
            libraries = re.findall(r"DLL Name: (\S+)", imports)
            if context.toolchain.abi == "mingw-gcc":
                sysroot = Path(run([context.toolchain.cc, "-print-sysroot"], capture=True).stdout.strip())
                for library in libraries:
                    if not library.lower().startswith(("libgcc", "libstdc++", "libwinpthread")):
                        continue
                    matches = list(sysroot.rglob(library))
                    if len(matches) != 1:
                        raise ValueError(f"Cannot locate compiler runtime {library}")
                    shutil.copy2(matches[0], stage / library)
            else:
                reject_static_windows_runtimes(libraries, context.toolchain.abi, executable.name)
    return stage, checks


def builder_configuration(root: Path, target: str) -> dict[str, str | None]:
    backend = target_spec(target).builder
    builder = BUILDERS[backend]
    if builder.platform is None:
        return {"name": backend, "kind": "native", "platform": None}
    lock = tomllib.loads((root / "builder/lock.toml").read_text())
    if lock.get("schema_version") != 3 or backend not in lock.get("builders", {}):
        raise ValueError(f"No locked builder for {target}")
    config = lock["builders"][backend]
    base, image = config.get("base", ""), config.get("image", "")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[0-9a-f]{64}", base):
        raise ValueError(f"Builder {backend} needs a pinned base image")
    if image and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[0-9a-f]{64}", image):
        raise ValueError(f"Builder {backend} image must be pinned by digest")
    return {"name": backend, "kind": "container", "platform": builder.platform, "base": base, "image": image}


def builder_image(root: Path, target: str, override: str | None = None, release: bool = False) -> str:
    if BUILDERS[target_spec(target).builder].platform is None:
        raise ValueError("Native targets do not use builder images")
    image = override or builder_configuration(root, target)["image"]
    if not image:
        raise ValueError("Set builder/lock.toml image to a qualified digest, or use --image for local testing")
    if release and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[0-9a-f]{64}", image):
        raise ValueError("Release builds require a registry image digest")
    return image
