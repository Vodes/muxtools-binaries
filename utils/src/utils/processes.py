import subprocess
import traceback
import sys
import os
from pathlib import Path

__all__ = ["run_cmd"]


def run_cmd(cmd: str, check: bool = True, env: dict | None = None, cwd: Path | str | None = None):
    if not env:
        env = os.environ
    if not cwd:
        cwd = os.getcwd()
    try:
        subprocess.run(cmd, shell=True, check=check, encoding="utf-8", env=env, cwd=cwd)
    except:
        traceback.print_exc()
        sys.exit(1)
