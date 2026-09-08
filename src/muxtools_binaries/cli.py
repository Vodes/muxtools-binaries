import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx2

from .artifacts import metadata, pack, read_metadata
from .build import builder_image, produce
from .io import run
from .models import load_packages
from .testing import structural, test_archive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="muxtools-build")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    packaging = commands.add_parser("package")
    packaging.add_argument("stage", type=Path)
    packaging.add_argument("--output", type=Path, default=Path("dist"))
    matrix = commands.add_parser("matrix")
    matrix.add_argument("--packages", default="")
    matrix.add_argument("--changed-from")
    build = commands.add_parser("build")
    build.add_argument("package")
    build.add_argument("--target", required=True)
    build.add_argument("--image")
    build.add_argument("--inside", action="store_true")
    build.add_argument("--channel", choices=["test", "release"], default="test")
    build.add_argument("--jobs", type=int, default=min(os.cpu_count() or 2, 8))
    build.add_argument("--revision")
    test = commands.add_parser("test")
    test.add_argument("archive", type=Path)
    test.add_argument("--structural-only", action="store_true")
    test.add_argument("--report", type=Path)
    release = commands.add_parser("publish")
    release.add_argument("--artifacts", type=Path, required=True)
    release.add_argument("--repository", required=True)
    release.add_argument("--publish", action="store_true")
    updates = commands.add_parser("updates")
    updates.add_argument("--package")
    updates.add_argument("--apply", action="store_true")
    return parser


def _matrix(root: Path, names: str, changed_from: str | None) -> dict[str, list[dict[str, str]]]:
    packages = load_packages(root, names.split(",") if names else None)
    if changed_from and not names:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", changed_from, "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        paths = [path for path in result.stdout.split("\0") if path]
        changed: set[str] = set()
        for path in paths:
            if _ignored_matrix_path(path):
                continue
            parts = path.split("/")
            if parts[0] == "packages" and len(parts) > 2:
                if parts[1] in packages:
                    changed.add(parts[1])
                continue
            changed = set(packages)
            break
        packages = {name: pkg for name, pkg in packages.items() if name in changed}
    return {
        "include": [
            {
                "package": p.name,
                "target": target,
                "runner": "windows-2022" if target.startswith("windows") else "ubuntu-24.04",
            }
            for p in packages.values()
            for target in p.targets
        ]
    }


def _ignored_matrix_path(path: str) -> bool:
    """Return whether a changed path cannot affect a package build."""
    lowered = path.casefold()
    name = lowered.rsplit("/", 1)[-1]
    return (
        lowered in {"readme", "readme.md", "readme.rst", "readme.txt"}
        or lowered == ".editorconfig"
        or lowered.startswith(("docs/", "context/", ".vscode/", ".idea/"))
        or name.endswith((".code-workspace", ".sublime-project", ".sublime-workspace"))
    )


def _build_in_container(root: Path, args: argparse.Namespace, image: str, revision: str) -> None:
    user: list[str] = []
    if sys.platform != "win32":
        user = ["--user", f"{os.getuid()}:{os.getgid()}"]
    run(
        [
            "docker",
            "run",
            "--rm",
            *user,
            "-v",
            f"{root}:/work",
            "-w",
            "/work",
            "-e",
            "UV_PROJECT_ENVIRONMENT=/tmp/muxtools-venv",
            "-e",
            "UV_CACHE_DIR=/tmp/uv-cache",
            image,
            "uv",
            "run",
            "--frozen",
            "muxtools-build",
            "build",
            args.package,
            "--inside",
            "--target",
            args.target,
            "--image",
            image,
            "--revision",
            revision,
            "--channel",
            args.channel,
            "--jobs",
            args.jobs,
        ]
    )


def _build(root: Path, args: argparse.Namespace) -> None:
    package = load_packages(root, [args.package])[args.package]
    if args.target not in package.targets:
        raise ValueError(f"Unsupported target for {package.name}: {args.target}")
    image = builder_image(root, args.image, args.channel == "release")
    revision = args.revision or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if not args.inside:
        _build_in_container(root, args, image, revision)
    else:
        stage, _ = produce(root, package, args.target, args.jobs)
        data = metadata(package, args.target, revision, image, args.channel)
        structural(stage, data)
        print(pack(stage, data, root / "dist"))


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        if args.command == "validate":
            packages = load_packages(root)
            print(f"Validated {len(packages)} packages")
        elif args.command == "package":
            data = read_metadata(args.stage)
            structural(args.stage, data)
            print(pack(args.stage, data, args.output))
        elif args.command == "matrix":
            print(json.dumps(_matrix(root, args.packages, args.changed_from)))
        elif args.command == "build":
            _build(root, args)
        elif args.command == "test":
            print(
                test_archive(
                    args.archive.resolve(),
                    smoke=not args.structural_only,
                    report=args.report,
                    audio_source=root / "tests/data/audio/wav_source.wav",
                )
            )
        elif args.command == "publish":
            from .release import publish

            publish(root, args.artifacts, args.repository, args.publish)
        elif args.command == "updates":
            from .updates import discover

            discover(root, args.package, args.apply)
    except (ValueError, OSError, subprocess.CalledProcessError, httpx2.HTTPError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
