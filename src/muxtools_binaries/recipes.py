import copy
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from .checks import CheckSuite, archive_checks
from .models import Model, Package, load_packages


def load_recipe(root: Path, name: str) -> ModuleType:
    path = root / "packages" / name / "recipe.py"
    module_name = "_recipe_" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()
    spec = importlib.util.spec_from_file_location(module_name, path, submodule_search_locations=[str(path.parent)])
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load {path}")
    recipe = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = recipe
    spec.loader.exec_module(recipe)
    for hook in ("build", "checks", "discover_update"):
        if not callable(getattr(recipe, hook, None)):
            raise ValueError(f"Recipe {path} must define {hook}()")
    return recipe


def recipe_options[T: Model](package: Package, target: str, schema: type[T]) -> T:
    def merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(base)
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    return schema.model_validate(merge(package.build, package.targets[target].build))


def recipe_checks(recipe: ModuleType, package: Package, target: str) -> CheckSuite:
    recipe_options(package, target, getattr(recipe, "Options", Model))
    getattr(recipe, "UpdateOptions", Model).model_validate(package.update)
    suite = CheckSuite.model_validate(recipe.checks(package, target).model_dump())
    suite.validate_binaries(package.binaries(target))
    if {name: check.args for name, check in suite.smoke.items()} != package.executables:
        raise ValueError(f"Recipe smoke arguments differ from {package.name}'s manifest")
    return suite


def checks_for_archive(data: dict[str, Any], root: Path) -> CheckSuite:
    if data["schema_version"] != 3:
        return archive_checks(data)
    package = load_packages(root, [data["name"]])[data["name"]]
    target = data["target"]
    if (
        (data["version"], data["version_code"]) != (package.version, package.version_code)
        or target not in package.targets
        or data["binaries"] != package.binaries(target)
        or data.get("build", {}).get("options", {}) != package.build
        or data.get("build", {}).get("target_options", {}) != package.targets[target].build
    ):
        raise ValueError("Archive differs from the package definition; use the checkout recorded in builder.revision")
    return recipe_checks(load_recipe(root, package.name), package, target)
