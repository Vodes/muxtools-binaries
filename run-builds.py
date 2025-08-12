import traceback
import sys
import subprocess
import pathlib


cwd = pathlib.Path()

if len(sys.argv) < 2:
    build_files = list(cwd.rglob("build.py"))
    for file in build_files:
        try:
            subprocess.run(f'uv run "{file.resolve()}"', shell=True)
        except:
            traceback.print_exc()
            sys.exit(1)
else:
    for name in sys.argv[1:]:
        project = cwd / name
        if not project.exists() or not project.is_dir() or not (build_file := project / "build.py").exists():
            raise ValueError(f"'{name}' is not an existing or valid project!")
        try:
            subprocess.run(f'uv run "{build_file.resolve()}"', shell=True)
        except:
            traceback.print_exc()
            sys.exit(1)
