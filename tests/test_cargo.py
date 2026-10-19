import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from muxtools_binaries.build import BuildContext
from muxtools_binaries.models import Package


def _context(package, tmp_path, target):
    definition = package.model_dump()
    definition["targets"] = {target: {"toolchain": "clang", "lto": "thin"}}
    return BuildContext(tmp_path, Package.model_validate(definition), target, tmp_path / "work", tmp_path / "stage", 3)


@pytest.fixture
def cargo_calls(monkeypatch):
    calls = []

    def run(args, **kwargs):
        if args[0] != "cargo":
            return SimpleNamespace(stdout="/toolchain/gcc/libgcc.a\n")
        env = kwargs["env"]
        config = Path(env["CARGO_HOME"]) / "config.toml"
        calls.append(
            SimpleNamespace(
                args=args, env=dict(env), config=tomllib.loads(config.read_text()) if config.exists() else {}
            )
        )
        return SimpleNamespace(stdout='[source.vendored-sources]\ndirectory = "vendor"\n')

    monkeypatch.setattr("muxtools_binaries.build.run", run)
    return calls


def test_cli_build_uses_locked_vendoring_offline(package, tmp_path, cargo_calls):
    ctx = _context(package, tmp_path, "linux-x86_64")

    ctx.cargo_install(tmp_path / "source")

    vendor, install = cargo_calls
    assert "--locked" in vendor.args
    assert install.config["source"]["vendored-sources"]["directory"] == str(vendor.args[-1])
    assert {"--locked", "--offline"} <= set(install.args)


def test_cli_cpu_flags_are_isolated_from_host_settings(package, tmp_path, monkeypatch, cargo_calls):
    monkeypatch.setenv("RUSTFLAGS", "-Ctarget-cpu=native")
    monkeypatch.setenv("CARGO_ENCODED_RUSTFLAGS", "-Ctarget-cpu=native")
    ctx = _context(package, tmp_path, "linux-x86_64")

    ctx.cargo_install(tmp_path / "source")

    install = cargo_calls[-1]
    assert "RUSTFLAGS" not in install.env
    assert install.env["CARGO_ENCODED_RUSTFLAGS"] == "-Ctarget-cpu=x86-64-v2"
    assert "--target=x86_64-unknown-linux-gnu" in install.args


@pytest.mark.parametrize(
    ("target", "triple", "runtime_flag"),
    [
        ("windows-x86_64", "x86_64-pc-windows-gnu", "-Lnative=/toolchain/gcc"),
        ("windows-arm64", "aarch64-pc-windows-gnullvm", "-Ctarget-feature=+crt-static"),
    ],
)
def test_windows_cli_links_the_required_static_runtime(package, tmp_path, cargo_calls, target, triple, runtime_flag):
    ctx = _context(package, tmp_path, target)

    ctx.cargo_install(tmp_path / "source")

    install = cargo_calls[-1]
    assert runtime_flag in install.env["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")
    assert f"--target={triple}" in install.args
    assert install.env[f"CARGO_TARGET_{triple.upper().replace('-', '_')}_LINKER"] == ctx.toolchain.cc


@pytest.mark.parametrize(
    ("target", "target_args"),
    [("linux-x86_64", []), ("windows-arm64", ["--target=aarch64-pc-windows-gnullvm"])],
)
def test_cargo_cinstall_retains_static_library_installation(package, tmp_path, cargo_calls, target, target_args):
    ctx = _context(package, tmp_path, target)

    ctx.cargo_cinstall(tmp_path / "source", features=("capi",))

    install = cargo_calls[-1]
    assert {"--library-type=staticlib", "--features=capi", f"--prefix={ctx.prefix}"} <= set(install.args)
    assert [arg for arg in install.args if arg.startswith("--target=")] == target_args
