"""Compile and check the supported compiler/target/LTO combinations."""

import os
import shlex
import tempfile
from pathlib import Path

from muxtools_binaries.build import BuildContext
from muxtools_binaries.io import run
from muxtools_binaries.models import Package
from muxtools_binaries.targets import TARGETS, TOOLCHAINS


def main() -> None:
    root = Path.cwd()
    output = root / "dist/qualification"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        source = work / "hello.cpp"
        source.write_text(
            '#include <iostream>\n#include <thread>\n#if defined(__x86_64__) || defined(_M_X64)\n#include <immintrin.h>\n#endif\n#ifdef _WIN32\n#include <windows.h>\n#endif\nint main(){ std::thread t([]{}); t.join(); std::cout << "ok\\n"; }\n'
        )
        builder = os.environ["MUXTOOLS_BUILDER_ID"]
        targets = [name for name, target in TARGETS.items() if target.builder == builder]
        for target in targets:
            target_info = TARGETS[target]
            target_output = output / target
            target_output.mkdir(parents=True, exist_ok=True)
            for toolchain in TOOLCHAINS[target]:
                for lto in (False, "full", "thin") if toolchain == "clang" else (False, "full"):
                    package = Package.model_validate(
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
                    ctx = BuildContext(root, package, target, work, work, 1)
                    env = ctx.build_environment()
                    name = f"{target}-{toolchain}-{lto}" + target_info.executable_suffix
                    run(
                        [
                            env["CXX"],
                            *shlex.split(env["CXXFLAGS"]),
                            source,
                            "-pthread",
                            *shlex.split(env["LDFLAGS"]),
                            "-o",
                            target_output / name,
                        ]
                    )
                    if target_info.os == "linux":
                        run([target_output / name])
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
    from muxtools_binaries.testing import structural

    for target in targets:
        target_info = TARGETS[target]
        structural(
            output / target,
            dict(
                name="qualify",
                target=target,
                binaries={"hello": {"baseline": f"{target}-clang-thin{target_info.executable_suffix}"}},
                runtime={"requirements": ["libgcc_s.so.1"] if target_info.os == "linux" else []},
            ),
        )
    (output / "tool-versions.txt").write_text(
        "\n".join(
            run([tool, "--version"], capture=True).stdout.splitlines()[0]
            for tool in [
                "gcc",
                "clang",
                "ld",
                "ld.lld",
                "cmake",
                "ninja",
                "meson",
                *(["nasm", "yasm", "gperf", "x86_64-w64-mingw32-gcc"] if builder == "manylinux-x86_64" else []),
            ]
        )
    )


if __name__ == "__main__":
    main()
