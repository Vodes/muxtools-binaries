"""Compile and check the supported compiler/target/LTO combinations."""

import shlex
import tempfile
from pathlib import Path

from muxtools_binaries.build import BuildContext
from muxtools_binaries.io import run
from muxtools_binaries.models import Package


def main() -> None:
    root = Path.cwd()
    output = root / "dist/qualification"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        source = work / "hello.cpp"
        source.write_text(
            '#include <iostream>\n#include <thread>\n#include <immintrin.h>\n#ifdef _WIN32\n#include <windows.h>\n#endif\nint main(){ std::thread t([]{}); t.join(); std::cout << "ok\\n"; }\n'
        )
        for target in ("linux-x86_64", "windows-x86_64"):
            for compiler in ("gcc", "clang"):
                for lto in (False, "full", "thin") if compiler == "clang" else (False, "full"):
                    package = Package.model_validate(
                        dict(
                            name="qualify",
                            version="1",
                            version_code=1,
                            type="source-build",
                            source=dict(repository="https://example.invalid", tag="1", commit="0" * 40),
                            targets={
                                target: dict(
                                    compiler=compiler,
                                    lto=lto,
                                    extra_ldflags=["-shared-libgcc"] if target.startswith("linux") else [],
                                )
                            },
                            executables={"hello": []},
                        )
                    )
                    ctx = BuildContext(root, package, target, work, work, 1)
                    env = ctx.environment()
                    name = f"{target}-{compiler}-{lto}" + (".exe" if ctx.windows else "")
                    run(
                        [
                            env["CXX"],
                            *shlex.split(env["CXXFLAGS"]),
                            source,
                            "-pthread",
                            *shlex.split(env["LDFLAGS"]),
                            "-o",
                            output / name,
                        ]
                    )
                    if not ctx.windows:
                        run([output / name])
                    for tier in ("baseline", "avx2", "avx512", "zn4"):
                        ctx.tier = tier
                        flags = ctx.environment()
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

    structural(
        output,
        dict(
            name="qualify",
            target="linux-x86_64",
            binaries={"hello": {"baseline": "linux-x86_64-clang-thin"}},
            runtime={"requirements": ["libgcc_s.so.1"]},
        ),
    )
    (output / "tool-versions.txt").write_text(
        "\n".join(
            run([tool, "--version"], capture=True).stdout.splitlines()[0]
            for tool in (
                "gcc",
                "clang",
                "ld",
                "ld.lld",
                "x86_64-w64-mingw32-gcc",
                "cmake",
                "ninja",
                "meson",
                "nasm",
                "yasm",
            )
        )
    )


if __name__ == "__main__":
    main()
