import importlib.util
import os
import re
import shlex
import shutil
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path

from .io import download, run
from .models import Package, Source

CPU_FLAGS = {
    "baseline": ["-march=x86-64-v2"],
    "avx2": ["-march=x86-64-v3"],
    "avx512": ["-march=x86-64-v4"],
    "zn4": ["-march=znver4", "-mno-sse4a", "-mno-avx512bf16"],
}
TRIPLE = "x86_64-w64-mingw32"


class BuildContext:
    def __init__(self, root: Path, package: Package, target: str, work: Path, stage: Path, jobs: int) -> None:
        self.root, self.package, self.target = root, package, target
        self.work, self.stage, self.jobs = work, stage, jobs
        self.config = package.targets[target]
        self.windows = target.startswith("windows-")
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

    def environment(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CPATH", "LIBRARY_PATH", "PKG_CONFIG_PATH"}
        }
        clang = self.config.compiler == "clang"
        cc = "clang" if clang else "gcc"
        cxx = "clang++" if clang else "g++"
        tools = {
            "CC": cc,
            "CXX": cxx,
            "AR": "llvm-ar" if clang else "gcc-ar",
            "RANLIB": "llvm-ranlib" if clang else "gcc-ranlib",
            "NM": "llvm-nm" if clang else "gcc-nm",
        }
        common = CPU_FLAGS[self.tier].copy()
        link = ["-static-libstdc++"]
        if "-shared-libgcc" not in self.config.extra_ldflags:
            link.append("-static-libgcc")
        if clang:
            link.append("-fuse-ld=lld")
            if not self.windows:
                gcc_directory = Path(run(["gcc", "-print-libgcc-file-name"], capture=True).stdout.strip()).parent
                common.append(f"--gcc-install-dir={gcc_directory}")
        if self.windows:
            if clang:
                sysroot = run([f"{TRIPLE}-gcc", "-print-sysroot"], capture=True).stdout.strip()
                if (Path(sysroot) / "mingw").is_dir():
                    sysroot = str(Path(sysroot) / "mingw")
                common += [f"--target={TRIPLE}", f"--sysroot={sysroot}"]
                libgcc = Path(run([f"{TRIPLE}-gcc", "-print-libgcc-file-name"], capture=True).stdout.strip()).parent
                link += [f"-L{libgcc}", "-static", "-pthread"]
                # Clang's MinGW discovery does not cover Fedora's RPM directory layout.
                search = run([f"{TRIPLE}-g++", "-E", "-x", "c++", "-", "-v"], capture=True)
                includes = search.stderr.split("#include <...> search starts here:")[-1].split("End of search list.")[0]
                cxx_includes = [
                    arg
                    for line in includes.splitlines()
                    if Path(line.strip()).is_dir() and "/c++" in line
                    for arg in ("-isystem", line.strip())
                ]
            else:
                tools = {key: f"{TRIPLE}-{value}" for key, value in tools.items()}
                link += ["-static"]
                cxx_includes = []
            tools["WINDRES"] = f"{TRIPLE}-windres"
        else:
            cxx_includes = []
        if self.config.lto:
            common += ["-flto=thin" if self.config.lto == "thin" else "-flto"]
        env.update(tools)
        env.update(
            CFLAGS=shlex.join(common + self.config.extra_cflags),
            CXXFLAGS=shlex.join(common + cxx_includes + self.config.extra_cxxflags),
            LDFLAGS=shlex.join(common + link + [f"-L{self.prefix / 'lib'}"] + self.config.extra_ldflags),
            CPPFLAGS=shlex.join([f"-I{self.prefix / 'include'}"]),
            PKG_CONFIG_LIBDIR=str(self.prefix / "lib/pkgconfig"),
            PKG_CONFIG_PATH="",
            SOURCE_DATE_EPOCH="0",
        )
        return env

    def autotools(self, name: str, source: Path, options: Sequence[str] = ()) -> None:
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
        if self.windows:
            args.append(f"--host={TRIPLE}")
        run(args, cwd=build, env=env)
        run(["make", f"-j{self.jobs}"], cwd=build, env=env)
        run(["make", "install"], cwd=build, env=env)

    def stage_binaries(self) -> None:
        for executable, variants in self.package.binaries(self.target).items():
            source = self.prefix / "bin" / (executable + (".exe" if self.windows else ""))
            shutil.copy2(source, self.stage / variants[self.tier])
            (self.stage / variants[self.tier]).chmod(0o755)

    def asset(self) -> Path:
        asset = self.config.asset
        if not asset:
            raise ValueError("No imported asset configured")
        return download(asset.url, asset.sha256, self.cache)


def produce(root: Path, package: Package, target: str, jobs: int) -> tuple[Path, BuildContext]:
    if os.environ.get("MUXTOOLS_BUILDER") != "1":
        raise ValueError("Builds must run inside the builder image; use the build command without --inside")
    workroot = root / "build" / package.name / target
    workroot.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=workroot))
    stage = work / "stage"
    stage.mkdir()
    context = BuildContext(root, package, target, work, stage, jobs)
    recipe_path = root / "packages" / package.name / "recipe.py"
    spec = importlib.util.spec_from_file_location(f"recipe_{package.name}", recipe_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load {recipe_path}")
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    recipe.build(context)
    if context.windows and package.type == "source-build":
        sysroot = Path(run([f"{TRIPLE}-gcc", "-print-sysroot"], capture=True).stdout.strip())
        for executable in list(stage.glob("*.exe")):
            imports = run(["objdump", "-p", executable], capture=True).stdout
            for library in re.findall(r"DLL Name: (\S+)", imports):
                if library.lower().startswith(("libgcc", "libstdc++", "libwinpthread")):
                    matches = list(sysroot.rglob(library))
                    if len(matches) != 1:
                        raise ValueError(f"Cannot locate compiler runtime {library}")
                    shutil.copy2(matches[0], stage / library)
    return stage, context


def builder_image(root: Path, override: str | None = None, release: bool = False) -> str:
    image = override or tomllib.loads((root / "builder/lock.toml").read_text())["image"]
    if not image:
        raise ValueError("Set builder/lock.toml image to a qualified digest, or use --image for local testing")
    if release and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[0-9a-f]{64}", image):
        raise ValueError("Release builds require a registry image digest")
    return image
