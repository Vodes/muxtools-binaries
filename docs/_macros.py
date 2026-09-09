"""Render package information from manifests without binary build dependencies."""

import tomllib
from html import escape
from pathlib import Path


def package_table(root: Path) -> str:
    manifests = sorted((root / "packages").glob("*/package.toml"))
    if not manifests:
        raise ValueError("No package manifests found")
    rows = ["<table>", "<thead><tr><th>Package</th><th>Description</th><th>Executables</th></tr></thead>", "<tbody>"]
    for path in manifests:
        with path.open("rb") as manifest:
            package = tomllib.load(manifest)
        name = escape(package["name"])
        description = escape(" ".join(package.get("description", "").split()))
        executables = ", ".join(f"<code>{escape(name)}</code>" for name in package["executables"])
        rows.append(f"<tr><td>{name}</td><td>{description}</td><td>{executables}</td></tr>")
    return "\n".join([*rows, "</tbody>", "</table>"])


def define_env(env):
    env.macro(lambda: package_table(Path(__file__).resolve().parents[1]), "available_packages")
