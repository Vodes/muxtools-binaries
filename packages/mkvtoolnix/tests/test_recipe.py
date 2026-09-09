import os
import shutil
import subprocess
from pathlib import Path

import pytest

from muxtools_binaries.build import BuildContext
from muxtools_binaries.models import load_packages
from muxtools_binaries.recipes import load_recipe

ROOT = Path(__file__).resolve().parents[3]
recipe = load_recipe(ROOT, "mkvtoolnix")


@pytest.mark.skipif(os.name == "nt", reason="POSIX AppImage wrappers")
def test_appimage_wrappers_preserve_argv0_from_unrelated_directory(tmp_path, monkeypatch):
    package = load_packages(ROOT, ["mkvtoolnix"])["mkvtoolnix"]
    stage = tmp_path / "package with spaces"
    stage.mkdir()
    ctx = BuildContext(ROOT, package, "linux-x86_64", tmp_path, stage, 1)
    monkeypatch.setattr(ctx, "asset", lambda: Path(shutil.which("bash")))
    recipe.build(ctx)
    for name in package.executables:
        result = subprocess.run(
            [stage / name, "--noprofile", "--norc", "-c", 'printf "%s" "$0"'],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout == name
