"""Plan the build matrix and release channel for a GitHub Actions run."""

import hashlib
import json
import os
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2

from .cli import _matrix
from .release import GitHub, release_allowed

MAIN_REF = "refs/heads/main"


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def _fingerprint(root: Path, rows: list[dict[str, str]]) -> str:
    targets = sorted((row["package"], row["target"]) for row in rows)
    data = json.dumps([_git(root, "rev-parse", "HEAD^{tree}"), targets])
    return hashlib.sha256(data.encode()).hexdigest()


def _marker(attempt: int, fingerprint: str) -> str:
    return f"binary-validation-v1-{attempt}-{fingerprint}"


def _pages(api: GitHub, path: str, key: str | None = None, **params: str) -> Iterator[dict[str, Any]]:
    page = 1
    while True:
        data = api.request("GET", path, params=dict(params, per_page=100, page=page), timeout=15)
        items = data if key is None else data[key]
        yield from items
        if len(items) < 100:
            return
        page += 1


def _merged_pr(root: Path, before: str, after: str, api: GitHub) -> dict[str, Any] | None:
    commits = _git(root, "rev-list", "--first-parent", f"{before}..{after}").splitlines()
    if not commits or _git(root, "rev-list", "--first-parent", "--skip=1", "-n1", commits[-1]) != before:
        return None

    matches = [
        pr
        for pr in _pages(api, f"/commits/{after}/pulls")
        if pr["merged_at"] and pr["merge_commit_sha"] == after and pr["base"]["ref"] == "main"
    ]
    if len(matches) != 1:
        return None
    pr = matches[0]
    for commit in commits[1:]:
        if {match["number"] for match in _pages(api, f"/commits/{commit}/pulls")} != {pr["number"]}:
            return None
    return pr


def _prior_validation(api: GitHub, pr: dict[str, Any], fingerprint: str) -> int | None:
    head = pr["head"]
    if head["repo"] is None:
        return None
    for run in _pages(
        api, "/actions/workflows/build.yml/runs", "workflow_runs", head_sha=head["sha"], status="success"
    ):
        if (
            run["conclusion"] != "success"
            or run["head_sha"] != head["sha"]
            or run["head_branch"] != head["ref"]
            or (run["head_repository"] or {}).get("id") != head["repo"]["id"]
        ):
            continue
        if run["event"] == "pull_request":
            associated = run["pull_requests"]
            if associated and all(match["number"] != pr["number"] for match in associated):
                continue
        elif not (
            run["event"] == "workflow_dispatch"
            and head["ref"].startswith("updates/")
            and head["repo"]["id"] == pr["base"]["repo"]["id"]
        ):
            continue
        name = _marker(run["run_attempt"], fingerprint)
        artifacts = _pages(api, f"/actions/runs/{run['id']}/artifacts", "artifacts", name=name)
        if any(artifact["name"] == name and artifact["expired"] is False for artifact in artifacts):
            return run["id"]
    return None


def _release_packages(root: Path, before: str, packages: set[str]) -> set[str]:
    # Matrix package names are validated against their manifest directory.
    paths = {name: f"packages/{name}/package.toml" for name in packages}
    previous = set(_git(root, "ls-tree", "-r", "--name-only", before, "--", *paths.values()).splitlines())
    releases = set()
    for name, path in sorted(paths.items()):
        if path not in previous:
            releases.add(name)
            continue
        old = tomllib.loads(_git(root, "show", f"{before}:{path}"))
        new = tomllib.loads((root / path).read_text())
        if new["version"] != old["version"]:
            if new["version_code"] <= old["version_code"]:
                raise ValueError(f"{name}: changing version for release requires increasing version_code")
            releases.add(name)
    return releases


def prepare(
    root: Path,
    *,
    event: str,
    ref: str,
    payload: dict[str, Any],
    packages: str = "",
    publish: bool = False,
    api: GitHub | None = None,
) -> tuple[dict[str, str], list[str]]:
    base = payload.get("before") if event == "push" else None
    if event == "pull_request":
        base = payload["pull_request"]["base"]["sha"]
    if base:
        try:
            base = _git(root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{base}^{{commit}}")
        except subprocess.CalledProcessError as error:
            if event == "pull_request":
                raise ValueError("The pull request base commit is unavailable after checkout") from error
            base = None

    rows = _matrix(root, packages, (base or "HEAD") if event == "push" else base)["include"]
    names = {row["package"] for row in rows}
    releases = names if event == "workflow_dispatch" and release_allowed(event, ref, publish) else set()
    prior_run = None
    summary = []
    if api is not None and event == "push" and ref == MAIN_REF and base and not payload.get("forced"):
        try:
            pr = _merged_pr(root, base, _git(root, "rev-parse", "HEAD"), api)
        except httpx2.HTTPError:
            pr = None
            summary.append(
                "GitHub PR lookup failed. Running regular push validation. Rerun to retry release detection."
            )
        if pr:
            if any(label["name"] == "release" for label in pr["labels"]):
                releases = _release_packages(root, base, names) if names else set()
                if not releases:
                    summary.append(f"PR #{pr['number']} requested a release. No package versions qualify.")
            try:
                prior_run = _prior_validation(api, pr, _fingerprint(root, rows))
            except httpx2.HTTPError:
                summary.append("GitHub validation lookup failed. Running regular push validation.")

    if event == "push":
        if prior_run is None:
            summary.append("Running push validation because no matching successful PR validation was found.")
        else:
            summary.append(f"Skipping repeat checks and test builds. Matching validation passed in run {prior_run}.")
    if releases:
        summary.append(f"Building release artifacts for {', '.join(sorted(releases))} from this run's commit.")

    mark_validation = event == "pull_request" or (
        event == "workflow_dispatch" and bool(packages) and ref == f"refs/heads/updates/{packages}" and not publish
    )
    matrix = [
        row | {"channel": "release" if row["package"] in releases else "test"}
        for row in rows
        if prior_run is None or row["package"] in releases
    ]
    outputs = {
        "matrix": json.dumps({"include": matrix}),
        "has_packages": json.dumps(bool(matrix)),
        "has_releases": json.dumps(bool(releases)),
        "run_checks": json.dumps(prior_run is None),
        "validation_fingerprint": _fingerprint(root, rows) if mark_validation else "",
    }
    return outputs, summary


def main() -> None:
    event = os.environ["GITHUB_EVENT_NAME"]
    root = Path.cwd()
    if _git(root, "rev-parse", "HEAD") != os.environ["GITHUB_SHA"]:
        raise SystemExit("The checkout does not match the workflow commit")
    outputs, summary = prepare(
        root,
        event=event,
        ref=os.environ["GITHUB_REF"],
        payload=json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()),
        packages=os.environ["PACKAGES"],
        publish=os.environ["PUBLISH"] == "true",
        api=GitHub(os.environ["GITHUB_REPOSITORY"]) if event == "push" else None,
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.writelines(f"{key}={value}\n" for key, value in outputs.items())
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
        stream.writelines(f"- {line}\n" for line in summary)


if __name__ == "__main__":
    main()
