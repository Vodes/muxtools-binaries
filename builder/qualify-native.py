"""Run the complete qualification set for this native runner."""

import json
from pathlib import Path

from muxtools_binaries.io import run, sha256
from muxtools_binaries.models import TARGETS, TIERS
from muxtools_binaries.testing import cpu_state, native_target, supports


def main() -> None:
    root = Path("qualification")
    records = json.loads((root / "fixtures.json").read_text())
    target = native_target()
    info = TARGETS[target]
    expected = {
        (compiler, lto, tier)
        for compiler in info.compilers
        for lto in ((False, "full") if compiler == "gcc" else (False, "full", "thin"))
        for tier in (TIERS if info.arch == "x86_64" else ("baseline",))
    }
    actual = [(r["compiler"], r["lto"], r["tier"]) for r in records if r["target"] == target]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("Incomplete or duplicate qualification fixtures")
    features, state = cpu_state() if info.arch == "x86_64" else (set(), 0)
    results = []
    for record in records:
        if record["target"] != target:
            continue
        path = root / record["path"]
        if sha256(path) != record["sha256"]:
            raise ValueError("Altered qualification fixture")
        tier = record["tier"]
        skipped = tier != "baseline" and not supports(tier, features, state)
        if not skipped:
            if not path.stat().st_mode & 0o111:
                path.chmod(0o755)
            run([path.resolve()])
            if info.os == "linux" and tier == "baseline":
                run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network=none",
                        "--platform",
                        "linux/arm64" if info.arch == "arm64" else "linux/amd64",
                        "-v",
                        f"{path.parent.resolve()}:/fixture:ro",
                        "almalinux:9",
                        f"/fixture/{path.name}",
                    ]
                )
        results.append(dict(record, status="skipped" if skipped else "completed"))
    Path(f"qualification-{target}.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
