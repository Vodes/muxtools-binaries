import pytest

from muxtools_binaries.models import Package


@pytest.fixture
def package():
    """Synthetic source state, independent of every real package's release cycle."""
    return Package.model_validate(
        dict(
            name="example",
            version="1.0",
            version_code=1,
            type="source-build",
            source=dict(repository="https://example.test/source", tag="v1.0", commit="0" * 40),
            targets={target: {} for target in ("linux-x86_64", "windows-x86_64")},
            executables={"example": ["--version"]},
        )
    )


@pytest.fixture
def recipe_root(tmp_path):
    path = tmp_path / "packages" / "example"
    path.mkdir(parents=True)
    (path / "recipe.py").write_text(
        "from muxtools_binaries.checks import default_checks as checks\n"
        "from muxtools_binaries.updates import source_update as discover_update\n"
        "def build(ctx): pass\n"
    )
    return tmp_path
