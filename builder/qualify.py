"""Compile and check the supported compiler, target, frontend, and LTO combinations."""

import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path

from muxtools_binaries.build import BuildContext
from muxtools_binaries.checks import reject_static_windows_runtimes
from muxtools_binaries.io import run
from muxtools_binaries.models import Package
from muxtools_binaries.targets import TARGETS, TOOLCHAINS
from muxtools_binaries.testing import binary_format, structural


def package_for(target: str, toolchain: str, lto: bool | str) -> Package:
    target_info = TARGETS[target]
    return Package.model_validate(
        dict(
            name="qualify",
            version="1",
            version_code=1,
            type="source-build",
            source=dict(repository="https://example.invalid", tag="1", commit="0" * 40),
            targets={
                target: dict(
                    toolchain=toolchain,
                    lto=lto,
                    extra_ldflags=["-shared-libgcc"] if target_info.os == "linux" else [],
                )
            },
            executables={"hello": []},
        )
    )


def qualify_cmake(ctx: BuildContext, source: Path, output: Path) -> None:
    build = ctx.cmake(f"cmake-{ctx.target}", source, {}, clang_cl=True)
    executable = build / "hello.exe"
    binary_format(executable, ctx.target)
    imports = run(["objdump", "-p", executable], capture=True).stdout
    reject_static_windows_runtimes(re.findall(r"DLL Name: (\S+)", imports), ctx.toolchain.abi, executable.name)
    shutil.copy2(executable, output / f"{ctx.target}-clang-msvc-clang-cl.exe")


def main() -> None:
    root = Path.cwd()
    output = root / "dist/qualification"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        c_source = work / "hello.c"
        c_source.write_text('#include <stdio.h>\nint main(void){ puts("ok"); return 0; }\n')
        cxx_source = work / "hello.cpp"
        cxx_source.write_text(
            "#include <iostream>\n#include <thread>\n#if defined(__x86_64__) || defined(_M_X64)\n"
            "#include <immintrin.h>\n#endif\n#ifdef _WIN32\n#include <windows.h>\n#endif\n"
            'int main(){ std::thread t([]{}); t.join(); std::cout << "ok\\n"; }\n'
        )
        cmake_source = work / "cmake-source"
        cmake_source.mkdir()
        (cmake_source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.20)\n"
            "project(qualification LANGUAGES C CXX)\n"
            "add_library(c_qualification STATIC ../hello.c)\n"
            "add_executable(hello ../hello.cpp)\n"
        )
        builder = os.environ["MUXTOOLS_BUILDER_ID"]
        targets = [name for name, target in TARGETS.items() if target.builder == builder]
        for target in targets:
            target_info = TARGETS[target]
            target_output = output / target
            target_output.mkdir(parents=True, exist_ok=True)
            for toolchain_name, toolchain in TOOLCHAINS[target].items():
                if target_info.os == "windows" and toolchain.clang:
                    version = run([toolchain.cc, "--version"], capture=True).stdout.splitlines()[0]
                    if not re.search(r"\b(?:clang )?version 22\.", version):
                        raise ValueError(f"Windows toolchain is not LLVM 22: {version}")
                lto_modes: tuple[bool | str, ...] = (False, "full", "thin") if toolchain.clang else (False, "full")
                for lto in lto_modes:
                    package = package_for(target, toolchain_name, lto)
                    ctx = BuildContext(root, package, target, work, work, 1)
                    env = ctx.build_environment()
                    stem = f"{target}-{toolchain_name}-{lto}"
                    c_output = target_output / f"{stem}-c{target_info.executable_suffix}"
                    cxx_output = target_output / f"{stem}-cxx{target_info.executable_suffix}"
                    run(
                        [
                            env["CC"],
                            *shlex.split(env["CFLAGS"]),
                            c_source,
                            *shlex.split(env["LDFLAGS"]),
                            "-o",
                            c_output,
                        ]
                    )
                    thread_flags = [] if toolchain.abi == "msvc" else ["-pthread"]
                    run(
                        [
                            env["CXX"],
                            *shlex.split(env["CXXFLAGS"]),
                            cxx_source,
                            *thread_flags,
                            *shlex.split(env["LDFLAGS"]),
                            "-o",
                            cxx_output,
                        ]
                    )
                    for executable in (c_output, cxx_output):
                        binary_format(executable, target)
                        if target_info.os == "linux":
                            run([executable])
                        else:
                            imports = run(["objdump", "-p", executable], capture=True).stdout
                            reject_static_windows_runtimes(
                                re.findall(r"DLL Name: (\S+)", imports), toolchain.abi, executable.name
                            )
                    for tier in target_info.cpu_flags:
                        ctx.tier = tier
                        flags = ctx.build_environment()
                        run(
                            [
                                flags["CC"],
                                *shlex.split(flags["CFLAGS"]),
                                "-x",
                                "c",
                                "-c",
                                "/dev/null",
                                "-o",
                                work / "empty.o",
                            ]
                        )
                if target_info.os == "windows" and toolchain_name == "clang-msvc":
                    ctx.tier = "baseline"
                    qualify_cmake(ctx, cmake_source, target_output)
    for target in targets:
        target_info = TARGETS[target]
        structural(
            output / target,
            dict(
                name="qualify",
                target=target,
                binaries={"hello": {"baseline": f"{target}-clang-thin-cxx{target_info.executable_suffix}"}},
                runtime={"requirements": ["libgcc_s.so.1"] if target_info.os == "linux" else []},
            ),
        )
    version_tools = ["gcc", "clang", "ld", "ld.lld", "cmake", "ninja", "meson"]
    if builder == "manylinux-x86_64":
        version_tools += ["nasm", "yasm", "gperf", "x86_64-w64-mingw32-gcc"]
    version_tools += ["/opt/llvm-mingw/bin/clang", "/opt/xwin-tools/xwin"]
    (output / "tool-versions.txt").write_text(
        "\n".join(run([tool, "--version"], capture=True).stdout.splitlines()[0] for tool in version_tools)
    )


if __name__ == "__main__":
    main()
