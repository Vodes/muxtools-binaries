import io
import os
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from muxtools_binaries.artifacts import pack, read_metadata
from muxtools_binaries.build import BuildContext
from muxtools_binaries.io import extract, sha256
from muxtools_binaries.testing import cpu_state, supports


@pytest.mark.parametrize("linked", [False, True])
def test_unsafe_archive(tmp_path, linked):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as tar:
        member = tarfile.TarInfo("link" if linked else "../escape")
        member.type = tarfile.SYMTYPE if linked else tarfile.REGTYPE
        member.linkname = "../escape" if linked else ""
        tar.addfile(member, io.BytesIO())
    with pytest.raises(ValueError):
        extract(archive, tmp_path / "out", "tar")
    assert not (tmp_path / "escape").exists()


def test_archive_roundtrip(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    binary = stage / "example"
    binary.write_bytes(b"fixture")
    binary.chmod(0o755)
    data = dict(
        schema_version=1,
        name="example",
        version="1",
        version_code=1,
        target="linux-x86_64",
        binaries={"example": {"baseline": "example"}},
        smoke={"example": []},
        provenance={"channel": "test"},
    )
    first = sha256(pack(stage, data, tmp_path / "dist"))
    os.utime(binary, (123, 123))
    archive = pack(stage, data, tmp_path / "dist")
    assert archive.name == f"{data['name']}-{data['version']}-{data['target']}.tar.zst"
    assert sha256(archive) == first
    extract(archive, tmp_path / "unpacked", "tar.zst")
    assert read_metadata(tmp_path / "unpacked") == data
    assert (tmp_path / "unpacked/example").read_bytes() == binary.read_bytes()


def test_cpu_detection_and_os_gate(monkeypatch):
    flags = "cx16 lahf_lm popcnt pni ssse3 sse4_1 sse4_2 avx avx2 bmi1 bmi2 f16c fma abm movbe xsave".split()
    monkeypatch.setattr("muxtools_binaries.testing.get_cpu_info", lambda: {"flags": flags})
    monkeypatch.setattr("muxtools_binaries.testing.sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr("muxtools_binaries.testing.platform.system", lambda: "Linux")
    monkeypatch.setattr("muxtools_binaries.testing.Path.read_text", lambda _: "flags : avx avx2\n")
    features, state = cpu_state()
    assert supports("avx2", features, state)
    assert not supports("avx512", features, state)
    assert not supports("avx2", features - {"fma"}, state)
    monkeypatch.setattr("muxtools_binaries.testing.Path.read_text", lambda _: "flags : sse sse2\n")
    assert not supports("avx2", *cpu_state())
    flags.extend("avx512f avx512bw avx512cd avx512dq avx512vl avx512_vnni 3dnowprefetch".split())
    enabled = Mock(return_value=6)
    monkeypatch.setattr("muxtools_binaries.testing.sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr("ctypes.WinDLL", lambda _: SimpleNamespace(GetEnabledXStateFeatures=enabled), raising=False)
    assert not supports("avx512", *cpu_state())
    enabled.return_value = 0xE6
    assert supports("avx512", *cpu_state())
    assert {"avx512vnni", "prfchw"} <= cpu_state()[0]


def test_shared_runtime_override(package, tmp_path):
    ctx = BuildContext(tmp_path, package, "linux-x86_64", tmp_path, tmp_path, 1)
    assert "-static-libgcc" in ctx.environment()["LDFLAGS"].split()
    ctx.config.extra_ldflags = ["-shared-libgcc"]
    flags = ctx.environment()["LDFLAGS"].split()
    assert "-shared-libgcc" in flags and "-static-libgcc" not in flags
