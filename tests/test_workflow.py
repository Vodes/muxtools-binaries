import json
import subprocess
from unittest.mock import Mock

import httpx2
import pytest
import tomlkit

from muxtools_binaries.release import GitHub
from muxtools_binaries.workflow import MAIN_REF, _marker, prepare


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def commit(root, message):
    git(root, "add", ".")
    git(root, "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


def manifest(root, package, **changes):
    data = package.model_dump(exclude_none=True, exclude_defaults=True) | changes
    path = root / "packages" / data["name"] / "package.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(data))


@pytest.fixture
def repo(tmp_path, package):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "tests@example.test")
    git(tmp_path, "config", "user.name", "Workflow tests")
    manifest(tmp_path, package)
    return tmp_path, commit(tmp_path, "base")


def github(head, after, *, event="pull_request", label=False, marker=None, expired=False, conclusion="success"):
    same_repo = {"id": 1}
    branch = "updates/example" if event == "workflow_dispatch" else "topic"
    pr = {
        "number": 7,
        "merged_at": "2026-11-25T12:00:00Z",
        "merge_commit_sha": after,
        "base": {"ref": "main", "repo": same_repo},
        "head": {"sha": head, "ref": branch, "repo": same_repo},
        "labels": [{"name": "release"}] if label else [],
    }
    run = {
        "id": 15,
        "run_attempt": 2,
        "event": event,
        "conclusion": conclusion,
        "head_sha": head,
        "head_branch": branch,
        "head_repository": same_repo,
        "pull_requests": [{"number": 7}] if event == "pull_request" else [],
    }
    responses = {
        f"/commits/{after}/pulls": [pr],
        "/actions/workflows/build.yml/runs": {"workflow_runs": [run]},
        "/actions/runs/15/artifacts": {"artifacts": [{"name": marker, "expired": expired}] if marker else []},
    }
    api = Mock(spec=GitHub)
    api.request.side_effect = lambda method, path, **kwargs: responses[path]
    return api


def push(root, before, api):
    return prepare(root, event="push", ref=MAIN_REF, payload={"before": before}, api=api)


def channels(outputs):
    return {(row["package"], row["target"]): row["channel"] for row in json.loads(outputs["matrix"])["include"]}


@pytest.mark.parametrize("source", ["pull_request", "workflow_dispatch"])
def test_squash_merge_reuses_matching_validation(repo, package, source):
    root, before = repo
    git(root, "switch", "-c", "updates/example")
    manifest(root, package, version="2.0", version_code=2)
    head = commit(root, "update package")
    if source == "pull_request":
        git(root, "switch", "-c", "pr-merge", before)
        git(root, "merge", "--no-ff", "--no-edit", head)
        validated, _ = prepare(
            root, event=source, ref="refs/pull/7/merge", payload={"pull_request": {"base": {"sha": before}}}
        )
    else:
        validated, _ = prepare(root, event=source, ref="refs/heads/updates/example", payload={}, packages="example")

    validated_commit = git(root, "rev-parse", "HEAD")
    git(root, "switch", "main")
    git(root, "merge", "--squash", head)
    after = commit(root, "squash merge")
    assert validated_commit != after
    marker = _marker(2, validated["validation_fingerprint"])
    api = github(head, after, event=source, marker=marker)

    outputs, summary = push(root, before, api)
    assert outputs["run_checks"] == "false"
    assert outputs["has_packages"] == "false"
    assert outputs["has_releases"] == "false"
    assert "run 15" in " ".join(summary)
    assert api.request.call_args.kwargs["params"]["name"] == marker


@pytest.mark.parametrize("mismatch", ["tree", "attempt", "failure", "expiry", "unavailable"])
def test_push_revalidates_without_matching_evidence(repo, package, mismatch):
    root, before = repo
    manifest(root, package, version="2.0", version_code=2)
    head = commit(root, "update package")
    validated, _ = prepare(
        root, event="pull_request", ref="refs/pull/7/merge", payload={"pull_request": {"base": {"sha": before}}}
    )
    if mismatch == "tree":
        (root / "README.md").write_text("Changed during merge\n")
    git(root, "add", ".")
    git(root, "commit", "--amend", "-m", "merge")
    after = git(root, "rev-parse", "HEAD")
    marker = _marker(1 if mismatch == "attempt" else 2, validated["validation_fingerprint"])
    api = github(
        head,
        after,
        marker=marker,
        expired=mismatch == "expiry",
        conclusion="failure" if mismatch == "failure" else "success",
    )
    if mismatch == "unavailable":
        lookup = api.request.side_effect

        def unavailable(method, path, **kwargs):
            if path.startswith("/actions/"):
                raise httpx2.ConnectError("unavailable")
            return lookup(method, path, **kwargs)

        api.request.side_effect = unavailable

    outputs, _ = push(root, before, api)
    assert outputs["run_checks"] == "true"
    assert outputs["has_releases"] == "false"
    assert channels(outputs) == {("example", target): "test" for target in package.targets}


def test_matching_tree_with_partial_target_matrix_does_not_skip(repo, package):
    root, before = repo
    manifest(root, package, version="2.0", version_code=2)
    manifest(root, package, name="untested")
    after = commit(root, "update two packages")
    validated, _ = prepare(
        root, event="workflow_dispatch", ref="refs/heads/updates/example", payload={}, packages="example"
    )
    marker = _marker(2, validated["validation_fingerprint"])
    outputs, _ = push(root, before, github(after, after, event="workflow_dispatch", marker=marker))
    assert outputs["run_checks"] == "true"
    assert channels(outputs) == {
        (name, target): "test" for name in ("example", "untested") for target in package.targets
    }


@pytest.mark.parametrize("modes", ["matching", "unlabeled", "no_prior", "direct", "ambiguous"])
def test_only_opted_in_versions_use_release_channel(repo, package, modes):
    root, _ = repo
    manifest(root, package, name="recipe-only")
    before = commit(root, "add recipe-only package")
    earlier = None
    if modes == "ambiguous":
        (root / "README.md").write_text("A separate direct push\n")
        earlier = commit(root, "another change in this push")
    manifest(root, package, version="2.0", version_code=2)
    manifest(root, package, name="new")
    (root / "packages/recipe-only/recipe.py").write_text("# Updated recipe\n")
    after = commit(root, "update packages")
    validated, _ = prepare(
        root, event="pull_request", ref="refs/pull/7/merge", payload={"pull_request": {"base": {"sha": before}}}
    )
    marker = _marker(2, validated["validation_fingerprint"]) if modes == "matching" else None
    api = github(after, after, label=modes != "unlabeled", marker=marker)
    if modes in ("direct", "ambiguous"):
        lookup = api.request.side_effect
        unrelated = earlier or after
        api.request.side_effect = lambda method, path, **kwargs: (
            [] if path == f"/commits/{unrelated}/pulls" else lookup(method, path, **kwargs)
        )

    outputs, _ = push(root, before, api)
    release_names = {"example", "new"} if modes in ("matching", "no_prior") else set()
    test_names = set() if modes == "matching" else {"example", "new", "recipe-only"} - release_names
    assert channels(outputs) == {
        (name, target): channel
        for channel, names in (("release", release_names), ("test", test_names))
        for name in names
        for target in package.targets
    }
    assert outputs["has_releases"] == json.dumps(bool(release_names))


def test_labeled_version_bump_requires_higher_counter(repo, package):
    root, before = repo
    manifest(root, package, version="2.0")
    after = commit(root, "version bump without counter bump")
    with pytest.raises(ValueError, match="requires increasing version_code"):
        push(root, before, github(after, after, label=True))


def test_empty_pr_matrix_can_skip_push_checks(repo):
    root, before = repo
    (root / "README.md").write_text("Documentation only\n")
    after = commit(root, "docs")
    validated, _ = prepare(
        root, event="pull_request", ref="refs/pull/7/merge", payload={"pull_request": {"base": {"sha": before}}}
    )
    marker = _marker(2, validated["validation_fingerprint"])
    outputs, _ = push(root, before, github(after, after, marker=marker))
    assert channels(outputs) == {}
    assert outputs["run_checks"] == "false"


@pytest.mark.parametrize("ref,publish", [(MAIN_REF, True), (MAIN_REF, False), ("refs/heads/topic", True)])
def test_manual_dispatch_requires_main_and_publish_flag(repo, package, ref, publish):
    root, _ = repo
    outputs, _ = prepare(root, event="workflow_dispatch", ref=ref, payload={}, publish=publish)
    assert outputs["run_checks"] == "true"
    assert outputs["validation_fingerprint"] == ""
    channel = "release" if ref == MAIN_REF and publish else "test"
    assert channels(outputs) == {("example", target): channel for target in package.targets}
