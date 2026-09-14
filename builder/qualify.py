"""Cross-compile C, C++, exceptions, threads, and Windows resources in isolation."""

import argparse
import json
import tempfile
from pathlib import Path

from muxtools_binaries.binary_checks import runtime_audit, structural
from muxtools_binaries.build import BuildContext
from muxtools_binaries.io import run, sha256
from muxtools_binaries.models import TARGETS, TIERS, Package

CMAKE = """cmake_minimum_required(VERSION 3.21)
project(qualify LANGUAGES C CXX)
find_package(Threads REQUIRED)
add_executable(hello main.cpp cpart.c)
target_compile_features(hello PRIVATE cxx_std_17)
target_link_libraries(hello PRIVATE Threads::Threads)
if(WIN32)
  enable_language(RC)
  target_sources(hello PRIVATE resource.rc)
endif()
install(TARGETS hello RUNTIME DESTINATION bin)
"""
CPP = """#include <thread>
#include <stdexcept>
#include <iostream>
#ifdef _WIN32
#include <windows.h>
#endif
extern "C" int cpart(void);
int main() {
  int result = 0;
  std::thread worker([&] { try { throw std::runtime_error("ok"); }
                          catch (const std::exception&) { result = cpart(); } });
  worker.join();
#ifdef _WIN32
  if (!FindResourceW(nullptr, MAKEINTRESOURCEW(1), MAKEINTRESOURCEW(10))) return 2;
#endif
  std::cout << "ok\\n";
  return result == 42 ? 0 : 1;
}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", nargs="+", choices=list(TARGETS), default=list(TARGETS))
    parser.add_argument("--output", type=Path, default=Path("dist/qualification"))
    args = parser.parse_args()
    root = Path.cwd()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        source = work / "source"
        source.mkdir()
        (source / "CMakeLists.txt").write_text(CMAKE)
        (source / "main.cpp").write_text(CPP)
        (source / "cpart.c").write_text("int cpart(void) { return 42; }\n")
        (source / "resource.rc").write_text("1 RCDATA { 42 }\n")
        for target, info in TARGETS.items():
            if target not in args.targets:
                continue
            for compiler in info.compilers:
                for lto in (False, "full") if compiler == "gcc" else (False, "full", "thin"):
                    for tier in TIERS if info.arch == "x86_64" else ("baseline",):
                        name = f"{target}/{compiler}/{lto}/{tier}"
                        stage = output / name
                        stage.mkdir(parents=True, exist_ok=True)
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
                                        extra_ldflags=["-shared-libgcc"] if target == "linux-x86_64" else [],
                                    )
                                },
                                executables={"hello": []},
                            )
                        )
                        ctx = BuildContext(root, package, target, work / name, stage, 2)
                        ctx.tier = tier
                        ctx.cmake("hello", source, {}, install=True)
                        binary = ctx.prefix / "bin" / ("hello.exe" if ctx.windows else "hello")
                        destination = stage / binary.name
                        destination.write_bytes(binary.read_bytes())
                        destination.chmod(0o755)
                        data = dict(
                            target=target,
                            binaries={"hello": {tier: destination.name}},
                            build=dict(compiler=compiler),
                            runtime=dict(requirements=["libgcc_s.so.1"] if target == "linux-x86_64" else []),
                        )
                        structural(stage, data)
                        runtime_audit(stage, data)
                        records.append(
                            dict(
                                target=target,
                                compiler=compiler,
                                lto=lto,
                                tier=tier,
                                path=destination.relative_to(output).as_posix(),
                                sha256=sha256(destination),
                            )
                        )
                        if target == "linux-x86_64" and tier == "baseline":
                            run([destination])
    (output / "fixtures.json").write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    main()
