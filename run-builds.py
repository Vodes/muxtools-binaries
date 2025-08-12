import os
import sys
import subprocess
import pathlib


cwd = pathlib.Path()

if len(sys.argv) < 2:
    build_files = list(cwd.rglob("build.py"))
    for file in build_files:
        subprocess.run(["uv", "run", str(file.resolve())], shell=True)
else:
    for name in sys.argv[1:]:
        project = cwd / name
        if not project.exists() or not project.is_dir() or not (build_file := project / "build.py").exists():
            raise ValueError(f"'{name}' is not an existing or valid project!")

        subprocess.run(["uv", "run", str(build_file.resolve())], shell=True)
