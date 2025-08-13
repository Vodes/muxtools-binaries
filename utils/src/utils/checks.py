import json
from pathlib import Path
from .dataclasses import ToolsData

__all__ = ["check_version_exists"]


def check_version_exists(tool: str, version: str) -> bool:
    versions_file = Path(__file__).parent / ".." / ".." / ".." / "versions.json"
    with open(versions_file, "r") as f:
        data = ToolsData(tools=json.load(f))
    return tool in data.tools and [ver for ver in data.tools[tool].versions if ver.version == version]
