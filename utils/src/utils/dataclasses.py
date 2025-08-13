from pydantic import BaseModel
from typing import List, Dict


__all__ = [
    "Version",
    "ToolInfo",
    "ToolsData",
]


class Version(BaseModel):
    version: str
    version_code: str


class ToolInfo(BaseModel):
    contains: List[str]
    supported_platforms: List[str]
    versions: List[Version]


class ToolsData(BaseModel):
    tools: Dict[str, ToolInfo]
