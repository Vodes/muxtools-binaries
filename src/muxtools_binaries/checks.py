"""Declarative archive checks; executing them never imports a recipe."""

import re
from string import Formatter
from typing import Annotated, Any, Literal

from pydantic import Field

from .models import Model, Package, safe_path


class CommandCheck(Model):
    args: list[str] = Field(default_factory=list)
    exit_codes: list[int] = Field(default=[0], min_length=1)
    stdout_prefix: str = ""
    stdout_contains: list[str] = Field(default_factory=list)
    timeout: int = Field(default=60, gt=0, le=600)


class AudioCheck(Model):
    kind: Literal["audio"] = "audio"
    command: str
    suffix: str = Field(pattern=r"^[a-zA-Z0-9]+$")
    args: list[str]
    lossless: bool = False


class VideoCheck(Model):
    kind: Literal["video"] = "video"
    command: str
    args: list[str]
    suffix: str = Field(pattern=r"^[a-zA-Z0-9]+$")
    width: int = Field(default=64, gt=0, le=4096, multiple_of=2)
    height: int = Field(default=64, gt=0, le=4096, multiple_of=2)
    bit_depth: int = Field(default=8, ge=8, le=16)


class CheckSuite(Model):
    smoke: dict[str, CommandCheck] = Field(min_length=1)
    functional: list[Annotated[AudioCheck | VideoCheck, Field(discriminator="kind")]] = Field(default_factory=list)
    # Explicit paths include wrappers and private executable resources.
    files: dict[str, Literal["elf", "pe", "script"]] = Field(default_factory=dict)

    def validate_binaries(self, binaries: dict[str, dict[str, str]]) -> None:
        if set(self.smoke) != set(binaries):
            raise ValueError("Checks must cover exactly the declared executables")
        if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", name) for name in binaries):
            raise ValueError("Invalid logical executable name")
        for check in self.functional:
            if check.command not in binaries:
                raise ValueError(f"Check references unknown executable: {check.command}")
            fields = {"source", "output"}
            if isinstance(check, VideoCheck):
                fields |= {"width", "height", "bit_depth"}
            for arg in check.args:
                for _, field, spec, conversion in Formatter().parse(arg):
                    if field is not None and (field not in fields or spec or conversion):
                        raise ValueError(f"Invalid check argument placeholder: {arg}")
        for path in self.files:
            safe_path(path)


def default_checks(package: Package, target: str = "") -> CheckSuite:
    return CheckSuite(smoke={name: CommandCheck(args=args) for name, args in package.executables.items()})


def archive_checks(data: dict[str, Any]) -> CheckSuite:
    if data.get("schema_version") != 2:
        raise ValueError("Native tests require archive metadata schema 2; rebuild the archive to include recipe checks")
    suite = CheckSuite.model_validate(data.get("checks"))
    suite.validate_binaries(data["binaries"])
    if {name: check.args for name, check in suite.smoke.items()} != data["smoke"]:
        raise ValueError("Check arguments differ from executable smoke commands")
    return suite
