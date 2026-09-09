import shutil
import subprocess
from pathlib import Path

import pytest

from muxtools_binaries.cli import _matrix


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def _commit(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)
    return _git(root, "rev-parse", "HEAD")


def _package(root: Path, name: str) -> None:
    package = root / "packages" / name
    package.mkdir(parents=True)
    (package / "package.toml").write_text(
        f'''schema_version = 1
name = "{name}"
version = "1.0"
version_code = 1
type = "source-build"

[source]
repository = "https://example.test/{name}"
tag = "v1.0"
commit = "{"0" * 40}"

[targets."linux-x86_64"]

[executables]
{name} = ["--version"]
'''
    )
    (package / "recipe.py").write_text("# package recipe\n")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "Matrix tests")
    _package(root, "alpha")
    _package(root, "beta")
    return root, _commit(root, "base")


def _names(root: Path, changed_from: str | None = None, packages: str = "") -> set[str]:
    return {entry["package"] for entry in _matrix(root, packages, changed_from)["include"]}


def test_package_change_selects_owner_with_mixed_docs(tmp_path):
    root, base = _repo(tmp_path)
    (root / "packages" / "alpha" / "recipe.py").write_text("# changed\n")
    (root / "docs").mkdir()
    (root / "docs" / "build.md").write_text("documentation\n")
    head = _commit(root, "change alpha and docs")

    assert _names(root, base) == {"alpha"}
    assert _names(root, head) == set()


def test_new_package_with_local_tests_selects_only_new_package(tmp_path):
    root, base = _repo(tmp_path)
    _package(root, "gamma")
    local_tests = root / "packages/gamma/tests"
    local_tests.mkdir()
    (local_tests / "test_recipe.py").write_text("def test_recipe(): pass\n")
    _commit(root, "add gamma and its tests")
    assert _names(root, base) == {"gamma"}


def test_docs_only_and_empty_diff_have_empty_matrix(tmp_path):
    root, base = _repo(tmp_path)
    assert _names(root, base) == set()

    (root / "README.md").write_text("read me\n")
    (root / "context").mkdir()
    (root / "context" / "notes.md").write_text("notes\n")
    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text("{}\n")
    _commit(root, "documentation and editor settings")

    assert _names(root, base) == set()


@pytest.mark.parametrize(
    "path",
    [
        "src/shared.py",
        "tests/test_shared.py",
        "builder/lock.toml",
        ".github/workflows/build.yml",
        "pyproject.toml",
        "uv.lock",
        "packages/shared.py",
        "src/readme.py",
    ],
)
def test_shared_change_selects_all_packages(tmp_path, path):
    root, base = _repo(tmp_path)
    shared = root / path
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text("shared = True\n")
    _commit(root, "change shared runtime")

    assert _names(root, base) == {"alpha", "beta"}


def test_deleted_package_does_not_select_remaining_packages(tmp_path):
    root, base = _repo(tmp_path)
    shutil.rmtree(root / "packages" / "alpha")
    _commit(root, "remove alpha")

    assert _names(root, base) == set()


def test_package_rename_sees_new_owner(tmp_path):
    root, base = _repo(tmp_path)
    _git(root, "mv", "packages/alpha", "packages/gamma")
    package_toml = root / "packages" / "gamma" / "package.toml"
    package_toml.write_text(
        package_toml.read_text().replace('name = "alpha"', 'name = "gamma"').replace("alpha =", "gamma =")
    )
    _commit(root, "rename alpha to gamma")

    assert _names(root, base) == {"gamma"}


def test_nul_delimited_special_path_and_manual_selection(tmp_path):
    root, base = _repo(tmp_path)
    (root / "packages" / "alpha" / "recipe with\nnewline.py").write_text("# changed\n")
    _commit(root, "add specially named package file")

    assert _names(root, base) == {"alpha"}
    assert _names(root, base, "beta") == {"beta"}
    assert _names(root) == {"alpha", "beta"}
