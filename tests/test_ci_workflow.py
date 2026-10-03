"""Structural guards for .github/workflows/ci.yml (issues #138, #141, #142, #144).

A workflow is configuration that nothing exercises until it runs on GitHub, so each property that matters is
asserted here from the file itself, and each assertion is also shown to be able to fail (the parse helpers are run
on deliberately bad workflows). `zizmor` is NOT run from here: a test that skips where the tool is absent makes
the passed-test count differ between environments, which badges/test-count.json cannot allow; CI's `security` job
runs it on every pull request, and `zizmor .github/workflows` does so locally. No YAML library is needed: the
workflow is laid out in a fixed, conventional shape.
"""
import re
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USES_RE = re.compile(r"^\s*-?\s*uses:\s*(\S+)(?:\s+#\s*(\S+))?\s*$")
REQUIRED_JOBS = ("test", "test-windows", "runtime-smoke", "security")


def workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


def split_jobs(text):
    """{job id: text of that job} for a workflow whose jobs are 2-space-indented keys under `jobs:`."""
    _, _, after = text.partition("\njobs:\n")
    jobs, current = {}, None
    for line in after.splitlines():
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            current = match.group(1)
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return {name: "\n".join(lines) for name, lines in jobs.items()}


def uses_lines(text):
    return [m.groups() for line in text.splitlines() if (m := USES_RE.match(line))]


def unpinned_actions(text):
    """`uses:` references that are not a full 40-hex commit SHA (a tag or branch can move)."""
    return [ref for ref, _ in uses_lines(text) if "@" in ref and not SHA_RE.match(ref.split("@", 1)[1])]


def job_permissions(job_text):
    """The `permissions:` mapping declared directly on a job, or None."""
    match = re.search(r"^    permissions:\n((?:      .+\n?)+)", job_text + "\n", re.M)
    if not match:
        return None
    return dict(re.findall(r"^\s+([a-z-]+):\s*(\S+)\s*$", match.group(1), re.M))


# ---------------------------------------------------------------------------
# Helpers can fail
# ---------------------------------------------------------------------------

def test_the_helpers_detect_unpinned_actions_and_missing_permissions():
    bad = textwrap.dedent("""\
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v7
              - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97  # v7.0.0
          b:
            permissions:
              contents: write
              id-token: write
            steps: []
        """)
    assert unpinned_actions(bad) == ["actions/checkout@v7"]
    jobs = split_jobs("\n" + bad)
    assert job_permissions(jobs["a"]) is None
    assert job_permissions(jobs["b"]) == {"contents": "write", "id-token": "write"}


# ---------------------------------------------------------------------------
# The real workflow
# ---------------------------------------------------------------------------

def test_the_expected_jobs_exist_under_their_required_names():
    jobs = split_jobs(workflow_text())
    assert set(REQUIRED_JOBS) <= set(jobs), sorted(jobs)
    for job_id in REQUIRED_JOBS:       # a job's display name is what a required status check refers to
        name = re.search(r"^    name:\s*(\S+)\s*$", jobs[job_id], re.M)
        assert name is None or name.group(1) == job_id, (job_id, name and name.group(1))


def test_every_action_is_pinned_to_a_full_commit_sha_with_a_release_comment():
    text = workflow_text()
    assert uses_lines(text), "no actions found"
    assert unpinned_actions(text) == []
    for ref, comment in uses_lines(text):
        assert comment and re.match(r"^v\d", comment), f"{ref} needs a trailing '# vX.Y.Z' comment for Dependabot"


def test_the_workflow_grants_nothing_by_default_and_each_job_only_reads_contents():
    text = workflow_text()
    assert re.search(r"^permissions:\s*\{\}\s*$", text, re.M), "top-level `permissions: {}` is required"
    for job_id, body in split_jobs(text).items():
        assert job_permissions(body) == {"contents": "read"}, f"{job_id}: {job_permissions(body)}"


def test_no_checkout_leaves_the_token_in_the_repository_configuration():
    text = workflow_text()
    for job_id, body in split_jobs(text).items():
        checkouts = body.count("actions/checkout@")
        assert checkouts >= 1, job_id
        assert len(re.findall(r"persist-credentials:\s*false", body)) == checkouts, job_id


def test_the_workflow_only_runs_on_push_to_main_and_pull_requests_never_pull_request_target():
    text = workflow_text()
    assert "pull_request_target" not in text and "workflow_run" not in text
    assert re.search(r"^  push:\n    branches: \[ \"main\" \]", text, re.M)
    assert re.search(r"^  pull_request:\n    branches: \[ \"main\" \]", text, re.M)


def test_the_test_job_measures_and_enforces_coverage_from_the_pyproject_floors():
    body = split_jobs(workflow_text())["test"]
    assert "--cov" in body and "--cov-report=xml" in body and "--cov-report=json" in body
    assert "python scripts/check_coverage.py coverage.json" in body
    diff_step = re.search(r"- name: Enforce coverage of the lines this pull request changes\n(.*?)\n\n", body, re.S)
    assert diff_step and "github.event_name == 'pull_request'" in diff_step.group(1)
    assert re.search(r"run: diff-cover coverage\.xml\s*$", diff_step.group(1), re.M), \
        "the floor and base come from [tool.diff_cover], not from flags here"
    assert "fetch-depth: 0" in body, "diff-cover needs the base branch"
    for flag in ("--fail-under", "--cov-fail-under", "--update-snapshots", "--report-only"):
        assert flag not in body, f"{flag} would bypass or duplicate the pyproject.toml configuration"


def test_the_count_check_in_the_test_job_runs_after_the_suite_and_never_writes():
    body = split_jobs(workflow_text())["test"]
    assert body.index("pytest tests/") < body.index("check_test_count.py pytest_output.txt")
    assert "check_test_count.py pytest_output.txt --write" not in body


def test_the_windows_job_runs_the_suite_on_windows():
    body = split_jobs(workflow_text())["test-windows"]
    assert "runs-on: windows-latest" in body and "pytest tests/" in body


def test_the_runtime_job_installs_only_the_runtime_requirements():
    body = split_jobs(workflow_text())["runtime-smoke"]
    installs = [i for i in re.findall(r"pip install (.+)", body) if i != "--upgrade pip"]
    assert installs == ["-r requirements.txt"]
    assert "tests/runtime_smoke.py --expect-clean" in body


def test_the_security_job_audits_every_requirements_file_and_the_workflows():
    body = split_jobs(workflow_text())["security"]
    for requirements in ("requirements.txt", "requirements-dev.txt", "requirements-security.txt"):
        assert f"-r {requirements}" in body.split("pip-audit", 1)[1].splitlines()[0]
    assert "zizmor .github/workflows" in body
    assert "-r requirements-security.txt" in body.split("pip-audit", 1)[0]


def test_the_environment_and_coverage_records_are_uploaded_even_when_a_step_fails():
    for job_id in ("test", "runtime-smoke"):
        body = split_jobs(workflow_text())[job_id]
        assert "pip freeze >" in body
        step = next(s for s in body.split("\n      - name: ") if "actions/upload-artifact@" in s)
        assert "if: always()" in step and "retention-days" in step, job_id


def test_the_security_requirements_file_names_both_tools():
    names = {re.split(r"[<>=!~ ]", line.strip())[0] for line in
             (REPO_ROOT / "requirements-security.txt").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")}
    assert names == {"pip-audit", "zizmor"}

