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
