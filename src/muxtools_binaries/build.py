import os
import re
import shlex
import shutil
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field

from .checks import CheckSuite
from .io import download, run
from .models import TARGETS, Model, Package, Source
from .recipes import load_recipe, recipe_checks, recipe_options

CPU_FLAGS = {
    "baseline": ["-march=x86-64-v2"],
    "avx2": ["-march=x86-64-v3"],
    "avx512": ["-march=x86-64-v4"],
    "zn4": ["-march=znver4", "-mno-sse4a", "-mno-avx512bf16"],
}
TRIPLE = "x86_64-w64-mingw32"

LINUX_ARM_TRIPLE = "aarch64-unknown-linux-gnu"
LINUX_ARM_ROOT = Path("/opt/cross") / LINUX_ARM_TRIPLE
MACOS_ROOT = Path("/opt/osxcross")
XWIN_ROOT = Path("/opt/xwin")
LLVM_MINGW_ROOT = Path("/opt/llvm-mingw")


def host_triple(target: str, compiler: str) -> str:
    info = TARGETS[target]
    if info.os == "macos":
        return f"{info.arch}-apple-darwin24.5"
    if info.os == "windows":
        if compiler == "clang-msvc":
            return f"{'aarch64' if info.arch == 'arm64' else 'x86_64'}-pc-windows-msvc"
        return "aarch64-w64-mingw32" if info.arch == "arm64" else TRIPLE
    return LINUX_ARM_TRIPLE if info.arch == "arm64" else "x86_64-pc-linux-gnu"


def compiler_tools(target: str, compiler: str) -> dict[str, str]:
    info = TARGETS[target]
    if compiler not in info.compilers:
        raise ValueError(f"Unsupported compiler {compiler} for {target}")
    triple = host_triple(target, compiler)
    if target == "windows-arm64" and compiler == "clang":
        return {
            key: str(LLVM_MINGW_ROOT / "bin" / tool)
            for key, tool in dict(
                CC=f"{triple}-clang",
                CXX=f"{triple}-clang++",
                AR="llvm-ar",
                RANLIB="llvm-ranlib",
                NM="llvm-nm",
                LD="ld.lld",
                WINDRES=f"{triple}-windres",
            ).items()
        }
    if compiler == "clang-msvc":
        return dict(
            CC="clang-cl",
            CXX="clang-cl",
            AR="llvm-lib",
            RANLIB="llvm-ranlib",
            NM="llvm-nm",
            LD="lld-link",
            WINDRES="llvm-rc",
            MT="llvm-mt",
        )
    if info.os == "macos":
        return {
            key: f"{triple}-{tool}"
            for key, tool in dict(CC="clang", CXX="clang++", AR="ar", RANLIB="ranlib", NM="nm", LD="ld").items()
        }
    clang = compiler == "clang"
    prefix = f"{triple}-" if not clang and (info.os == "windows" or info.arch == "arm64") else ""
    tools = dict(
        CC=prefix + ("clang" if clang else "gcc"),
        CXX=prefix + ("clang++" if clang else "g++"),
        AR=prefix + ("llvm-ar" if clang else "gcc-ar"),
        RANLIB=prefix + ("llvm-ranlib" if clang else "gcc-ranlib"),
        NM=prefix + ("llvm-nm" if clang else "gcc-nm"),
        LD="ld.lld" if clang else prefix + "ld",
    )
    if info.os == "windows":
        tools["WINDRES"] = f"{TRIPLE}-windres"
    return tools


class AutotoolsOptions(Model):
    configure: list[str] = Field(default_factory=list)
    dependencies: dict[str, list[str]] = Field(default_factory=dict)


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
        self.target_info = TARGETS[target]
        self.windows = self.target_info.os == "windows"
        self.macos = self.target_info.os == "macos"
        self.msvc = self.config.compiler == "clang-msvc"
        self.mingw = self.windows and not self.msvc
        self.llvm_mingw = self.mingw and self.target_info.arch == "arm64"
        self.triple = host_triple(target, self.config.compiler)
        self.cache = root / "build" / "downloads"
        self.tier = "baseline"

    def source(self, name: str, pin: Source) -> Path:
        path = self.work / self.tier / "sources" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "init", path])
        run(["git", "-C", path, "fetch", "--depth=1", pin.repository, pin.commit])
        run(["git", "-C", path, "checkout", "--detach", "FETCH_HEAD"])
        if run(["git", "-C", path, "rev-parse", "HEAD"], capture=True).stdout.strip() != pin.commit:
            raise ValueError(f"Source revision mismatch: {name}")
        run(["git", "-C", path, "tag", pin.tag, pin.commit])
        self.notices(path, name)
        return path

    def notices(self, source: Path, name: str) -> None:
        output = self.stage / "licenses" / name
        for path in source.iterdir():
            if path.is_file() and path.name.upper().startswith(("COPYING", "LICENSE", "NOTICE", "PATENTS")):
                output.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, output / path.name)

    @property
    def prefix(self) -> Path:
        return self.work / self.tier / "prefix"

    @property
    def sysroot(self) -> Path:
        if self.macos:
            return MACOS_ROOT / "SDK/MacOSX15.5.sdk"
        if self.msvc:
            return XWIN_ROOT
        if self.llvm_mingw:
            return LLVM_MINGW_ROOT / self.triple
        if self.target == "linux-arm64":
            return LINUX_ARM_ROOT / self.triple / "sysroot"
        if self.mingw:
            path = Path(run([f"{TRIPLE}-gcc", "-print-sysroot"], capture=True).stdout.strip())
            return path / "mingw" if (path / "mingw").is_dir() else path
        return Path("/")

    def environment(self) -> dict[str, str]:
        removed = {
            "CC",
            "CXX",
            "AR",
            "NM",
            "LD",
            "RANLIB",
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
            "PKG_CONFIG_PATH",
            "PKG_CONFIG_LIBDIR",
            "PKG_CONFIG_SYSROOT_DIR",
            "CMAKE_PREFIX_PATH",
            "CMAKE_TOOLCHAIN_FILE",
            "SDKROOT",
            "MACOSX_DEPLOYMENT_TARGET",
        }
        env = {key: value for key, value in os.environ.items() if key not in removed}
        tools = compiler_tools(self.target, self.config.compiler)
        common = CPU_FLAGS[self.tier].copy() if self.target_info.arch == "x86_64" else ["-march=armv8-a"]
        clang = self.config.compiler == "clang"
        cxx_flags: list[str] = []
        link: list[str] = []
        root = self.sysroot
        if self.msvc:
            common = ["/clang:" + flag for flag in common]
            common += [f"--target={self.triple}", "/winsysroot", str(root), "/MT"]
            cxx_flags = ["/EHsc"]
            arch = "arm64" if self.target_info.arch == "arm64" else "x64"
            # /winsysroot discovers the frozen, versioned SDK/CRT. LIB also supplies lld-link.
            crt = sorted((root / "VC/Tools/MSVC").glob("*"))
            sdk = sorted((root / "Windows Kits/10/Lib").glob("*"))
            if len(crt) != 1 or len(sdk) != 1:
                raise ValueError("Expected one frozen xwin SDK and CRT")
            libraries = [crt[0] / "lib" / arch, sdk[0] / "ucrt" / arch, sdk[0] / "um" / arch]
            link = [f"/libpath:{path}" for path in [self.prefix / "lib", *libraries]]
            env["LIB"] = ";".join(map(str, libraries))
            sdk_headers = root / "Windows Kits/10/Include" / sdk[0].name
            headers = [crt[0] / "include", *[sdk_headers / name for name in ("ucrt", "shared", "um")]]
            env["INCLUDE"] = ";".join(map(str, headers))
            env["RCFLAGS"] = shlex.join([f"/I{path}" for path in headers])
        elif self.macos:
            common += ["-mmacos-version-min=13.0"]
            cxx_flags = ["-stdlib=libc++"]
            env["MACOSX_DEPLOYMENT_TARGET"] = "13.0"
        elif self.llvm_mingw:
            common += [f"--sysroot={root}"]
            link = ["-static", "-pthread"]
        else:
            link = ["-static-libstdc++"]
            if "-shared-libgcc" not in self.config.extra_ldflags:
                link.append("-static-libgcc")
            if clang:
                link.append("-fuse-ld=lld")
                gcc = f"{self.triple}-gcc" if self.target != "linux-x86_64" else "gcc"
                libgcc = Path(run([gcc, "-print-libgcc-file-name"], capture=True).stdout.strip()).parent
                if not self.mingw:
                    common.append(f"--gcc-install-dir={libgcc}")
                if self.target != "linux-x86_64":
                    common += [f"--target={self.triple}", f"--sysroot={root}"]
                if self.mingw:
                    link += [f"-L{libgcc}", "-static", "-pthread"]
                    search = run([f"{TRIPLE}-g++", "-E", "-x", "c++", "-", "-v"], capture=True)
                    includes = search.stderr.split("#include <...> search starts here:")[-1].split(
                        "End of search list."
                    )[0]
                    cxx_flags = [
                        arg
                        for line in includes.splitlines()
                        if Path(line.strip()).is_dir() and "/c++" in line
                        for arg in ("-isystem", line.strip())
                    ]
            elif self.mingw:
                link += ["-static"]
        if self.config.lto:
            flag = "-flto=thin" if self.config.lto == "thin" else "-flto"
            common.append("/clang:" + flag if self.msvc else flag)
        if not self.msvc:
            link = common + link + [f"-L{self.prefix / 'lib'}"]
        env.update(tools)
        env.update(
            CFLAGS=shlex.join(common + self.config.extra_cflags),
            CXXFLAGS=shlex.join(common + cxx_flags + self.config.extra_cxxflags),
            LDFLAGS=shlex.join(link + self.config.extra_ldflags),
            CPPFLAGS=shlex.join([f"-I{self.prefix / 'include'}"]),
            PKG_CONFIG_LIBDIR=os.pathsep.join(
                str(path / suffix)
                for path in (self.prefix, root if self.llvm_mingw else root / "usr")
                for suffix in ("lib/pkgconfig", "share/pkgconfig")
            ),
            PKG_CONFIG_PATH="",
            SOURCE_DATE_EPOCH="0",
        )
        return env

    def autotools(self, name: str, source: Path, options: Sequence[str] = ()) -> None:
        if self.msvc:
            raise ValueError("clang-msvc supports CMake only; Autotools is unsupported")
        env = self.environment()
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
        if self.target != "linux-x86_64":
            host = self.triple.replace("arm64-apple-", "aarch64-apple-")
            args.append(f"--host={host}")
        run(args, cwd=build, env=env)
        run(["make", f"-j{self.jobs}"], cwd=build, env=env)
        run(["make", "install"], cwd=build, env=env)

    def stage_binaries(self) -> None:
        for executable in self.package.executables:
            source = self.prefix / "bin" / (executable + (".exe" if self.windows else ""))
            self.stage_binary(source, executable)

    def asset(self) -> Path:
        asset = self.config.asset
        if not asset:
            raise ValueError("No imported asset configured")
        return download(asset.url, asset.sha256, self.cache)

    def cmake(
        self, name: str, source: Path, definitions: dict[str, str | bool | int], *, install: bool = False
    ) -> Path:
        env = self.environment()
        build = self.work / self.tier / name
        options: dict[str, str | bool | int] = dict(definitions)
        options.update(
            CMAKE_INSTALL_PREFIX=str(self.prefix),
            CMAKE_BUILD_TYPE="Release",
            CMAKE_PREFIX_PATH=str(self.prefix),
            CMAKE_FIND_ROOT_PATH=f"{self.prefix};{self.sysroot}",
            CMAKE_FIND_ROOT_PATH_MODE_PROGRAM="NEVER",
            CMAKE_FIND_ROOT_PATH_MODE_LIBRARY="ONLY",
            CMAKE_FIND_ROOT_PATH_MODE_INCLUDE="ONLY",
            CMAKE_FIND_ROOT_PATH_MODE_PACKAGE="ONLY",
            CMAKE_FIND_USE_PACKAGE_REGISTRY=False,
            CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=False,
            CMAKE_FIND_USE_CMAKE_ENVIRONMENT_PATH=False,
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
        if self.msvc:
            options.update(
                CMAKE_LINKER="lld-link",
                CMAKE_MT="llvm-mt",
                CMAKE_MSVC_RUNTIME_LIBRARY="MultiThreaded",
                CMAKE_POLICY_DEFAULT_CMP0091="NEW",
                CMAKE_C_COMPILER_TARGET=self.triple,
                CMAKE_CXX_COMPILER_TARGET=self.triple,
            )
        elif self.target == "linux-arm64":
            options.update(CMAKE_SYSTEM_NAME="Linux", CMAKE_SYSTEM_PROCESSOR="aarch64", CMAKE_SYSROOT=str(self.sysroot))
        elif self.macos:
            options.update(CMAKE_OSX_DEPLOYMENT_TARGET="13.0", CMAKE_OSX_ARCHITECTURES=self.target_info.arch)
            # OSXCross's toolchain adds a MacPorts prefix and overrides pkg-config.
            # Restore package/sysroot-only discovery after that toolchain is loaded.
            build.mkdir(parents=True, exist_ok=True)
            discovery = build / "discovery.cmake"
            discovery.write_text(
                f'set(CMAKE_FIND_ROOT_PATH "{self.prefix};{self.sysroot}")\n'
                f'set(ENV{{PKG_CONFIG_LIBDIR}} "{env["PKG_CONFIG_LIBDIR"]}")\n'
                'set(ENV{PKG_CONFIG_SYSROOT_DIR} "")\n'
            )
            options["CMAKE_PROJECT_TOP_LEVEL_INCLUDES"] = str(discovery)
        args = [
            f"-D{key}={'ON' if value is True else 'OFF' if value is False else value}" for key, value in options.items()
        ]
        run(
            [f"{self.triple}-cmake" if self.macos else "cmake", "-S", source, "-B", build, "-G", "Ninja", *args],
            env=env,
        )
        run(["cmake", "--build", build, "--parallel", self.jobs], env=env)
        if install:
            run(["cmake", "--install", build], env=env)
        return build

    def stage_binary(self, source: Path, executable: str) -> None:
        destination = self.stage / self.package.binaries(self.target)[executable][self.tier]
        shutil.copy2(source, destination)
        destination.chmod(0o755)


def produce(root: Path, package: Package, target: str, jobs: int) -> tuple[Path, CheckSuite]:
    if os.environ.get("MUXTOOLS_BUILDER") != "1":
        raise ValueError("Builds must run inside the builder image; use the build command without --inside")
    workroot = root / "build" / package.name / target
    workroot.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=workroot))
    stage = work / "stage"
    stage.mkdir()
    context = BuildContext(root, package, target, work, stage, jobs)
    recipe = load_recipe(root, package.name)
    checks = recipe_checks(recipe, package, target)
    recipe.build(context)
    if context.mingw and package.type == "source-build":
        copy_mingw_runtimes(stage, context.sysroot)
    return stage, checks


def copy_mingw_runtimes(stage: Path, sysroot: Path) -> None:
    pending = [path for path in stage.iterdir() if path.suffix.lower() in (".exe", ".dll")]
    bundled = {path.name.casefold() for path in pending}
    available = [path for path in sysroot.rglob("*") if path.suffix.lower() == ".dll" and path.is_file()]
    while pending:
        imports = run(["llvm-readobj", "--coff-imports", pending.pop()], capture=True).stdout
        for library in re.findall(r"Name: (\S+\.dll)", imports, re.I):
            name = library.casefold()
            if name in bundled or not name.startswith(("libgcc", "libstdc++", "libwinpthread", "libc++", "libunwind")):
                continue
            matches = [path for path in available if path.name.casefold() == name]
            if len(matches) != 1:
                raise ValueError(f"Cannot locate compiler runtime {library}")
            destination = stage / library
            shutil.copy2(matches[0], destination)
            bundled.add(name)
            pending.append(destination)


def builder_image(root: Path, override: str | None = None, release: bool = False) -> str:
    image = override or tomllib.loads((root / "builder/lock.toml").read_text())["image"]
    if not image:
        raise ValueError("Set builder/lock.toml image to a qualified digest, or use --image for local testing")
    if release and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[0-9a-f]{64}", image):
        raise ValueError("Release builds require a registry image digest")
    return image
