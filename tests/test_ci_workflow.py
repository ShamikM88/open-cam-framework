"""Structural guards for .github/workflows/ci.yml (issues #138, #141, #142, #144).

A workflow is configuration that nothing exercises until it runs on GitHub, so each property that matters is
asserted here from the file itself, and each assertion is also shown to be able to fail (the parse helpers are run
on deliberately bad workflows). `zizmor` is NOT run from here: a test that skips where the tool is absent makes
the passed-test count differ between environments, which badges/test-count.json cannot allow; CI's `security` job
runs it on every pull request, and `zizmor .github/workflows` does so locally. No YAML library is needed: the
workflow is laid out in a fixed, conventional shape.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CONSTRAINTS_FILE = REPO_ROOT / "constraints.txt"
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
    assert "pytest tests/ -n 4 --dist=loadfile --durations=30 --cov" in body
    assert "--durations=30" in body, "keep slow-test profiling visible on Ubuntu"
    assert "--cov" in body and "--cov-report=xml" in body and "--cov-report=json" in body
    assert "python scripts/check_coverage.py coverage.json" in body
    diff_step = re.search(r"- name: Enforce coverage of the lines this pull request changes\n(.*?)\n\n", body, re.S)
    assert diff_step and "github.event_name == 'pull_request'" in diff_step.group(1)
    assert re.search(r"run: diff-cover coverage\.xml --config-file pyproject\.toml\s*$", diff_step.group(1), re.M), \
        "the floor and base come from [tool.diff_cover]; diff-cover reads it only with --config-file"
    critical_step = re.search(
        r"- name: Enforce coverage of the lines this pull request changes in the governance modules\n(.*?)\n\n", body, re.S)
    assert critical_step and "github.event_name == 'pull_request'" in critical_step.group(1)
    assert re.search(r"run: diff-cover coverage\.xml --config-file diff_cover_critical\.toml\s*$", critical_step.group(1),
                     re.M), "the stricter tier is its own configuration file, and diff-cover reads it only with --config-file"
    assert "fetch-depth: 0" in body, "diff-cover needs the base branch"
    for flag in ("--fail-under", "--cov-fail-under", "--update-snapshots", "--report-only"):
        assert flag not in body, f"{flag} would bypass or duplicate the pyproject.toml configuration"


def ci_diff_cover_command(step_name="Enforce coverage of the lines this pull request changes"):
    step = re.search(rf"- name: {re.escape(step_name)}\n(.*?)\n\n", split_jobs(workflow_text())["test"], re.S).group(1)
    return shlex.split(re.search(r"run: (diff-cover .+)$", step, re.M).group(1))


def ci_critical_diff_cover_command():
    return ci_diff_cover_command("Enforce coverage of the lines this pull request changes in the governance modules")


def _git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                    *args], cwd=cwd, check=True, capture_output=True, env=env, timeout=60)


def _measure(cwd, run_script):
    """Run run_script under coverage.py (ignoring the repository's own coverage config) and write coverage.xml."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COV_CORE", "COVERAGE"))}
    for command in (["run", "--rcfile=cov.rc", "--branch", "--source=scripts", "--data-file=.cov", run_script],
                    ["xml", "--rcfile=cov.rc", "--data-file=.cov", "-o", "coverage.xml"]):
        subprocess.run([sys.executable, "-m", "coverage", *command], cwd=cwd, check=True, capture_output=True,
                       env=env, timeout=120)


def test_the_diff_cover_step_really_fails_on_an_uncovered_change_and_passes_on_a_covered_one(tmp_path):
    """The command line CI runs, executed for real: a gate that is configured but not read cannot fail (it was
    exactly that before this test existed)."""
    command = ci_diff_cover_command()
    assert command[:1] == ["diff-cover"]
    run_diff_cover = [sys.executable, "-m", "diff_cover.diff_cover_tool", *command[1:]]
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "run.py").write_text("import sys\nsys.path.insert(0, 'scripts')\nimport m\nm.f()\n", encoding="utf-8")
    (tmp_path / "cov.rc").write_text("", encoding="utf-8")
    shutil.copy(REPO_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")     # the real [tool.diff_cover]
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")           # compare_branch = origin/main

    changed = "def f():\n    return 1\n\n\ndef g():\n    return 2\n\n\ndef h():\n    return 3\n"
    (tmp_path / "scripts" / "m.py").write_text(changed, encoding="utf-8")
    _measure(tmp_path, "run.py")                                                # g and h are never called
    uncovered = subprocess.run(run_diff_cover, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert uncovered.returncode != 0 and "Failure" in uncovered.stderr, uncovered.stdout + uncovered.stderr

    (tmp_path / "run.py").write_text("import sys\nsys.path.insert(0, 'scripts')\nimport m\nm.f()\nm.g()\nm.h()\n",
                                     encoding="utf-8")
    _measure(tmp_path, "run.py")
    covered = subprocess.run(run_diff_cover, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert covered.returncode == 0, covered.stdout + covered.stderr

    without_config = subprocess.run([sys.executable, "-m", "diff_cover.diff_cover_tool", "coverage.xml"],
                                    cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert without_config.returncode == 0       # documents why --config-file is needed: no flag, no floor


def _tiered_repo(tmp_path, module):
    """A repository whose `scripts/<module>.py` changes 41 measured lines, one of which (97.6%) no test runs."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / f"{module}.py").write_text("X = 0\n", encoding="utf-8")
    (tmp_path / "run.py").write_text(f"import sys\nsys.path.insert(0, 'scripts')\nimport {module}\n", encoding="utf-8")
    (tmp_path / "cov.rc").write_text("", encoding="utf-8")
    for name in ("pyproject.toml", "diff_cover_critical.toml"):
        shutil.copy(REPO_ROOT / name, tmp_path / name)                           # the real floors
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    lines = [f"V{i} = {i}" for i in range(39)] + ["def g():", "    return 2"]
    (tmp_path / "scripts" / f"{module}.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _measure(tmp_path, "run.py")                                                 # g is never called


def _run_tier(tmp_path, command):
    return subprocess.run([sys.executable, "-m", "diff_cover.diff_cover_tool", *command[1:]], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)


def test_a_changed_governance_module_is_held_to_98_percent_and_any_other_file_to_95(tmp_path):
    """The two commands CI runs, executed for real against 97.6% coverage of the changed lines: the 95% tier passes
    it; the 98% tier fails it for a governance module and does not look at any other file."""
    general, critical = ci_diff_cover_command(), ci_critical_diff_cover_command()
    assert critical[:1] == ["diff-cover"] and "diff_cover_critical.toml" in critical

    governance = tmp_path / "governance"
    governance.mkdir()
    _tiered_repo(governance, "policy_engine")
    assert _run_tier(governance, general).returncode == 0, "97.6% clears the 95% tier"
    failed = _run_tier(governance, critical)
    assert failed.returncode != 0 and "Failure" in failed.stderr, failed.stdout + failed.stderr

    other = tmp_path / "other"
    other.mkdir()
    _tiered_repo(other, "some_helper")
    assert _run_tier(other, general).returncode == 0
    assert _run_tier(other, critical).returncode == 0, "the 98% tier ignores a file that is not a governance module"

    (governance / "run.py").write_text(
        "import sys\nsys.path.insert(0, 'scripts')\nimport policy_engine\npolicy_engine.g()\n", encoding="utf-8")
    _measure(governance, "run.py")
    assert _run_tier(governance, critical).returncode == 0, "fully covered changed lines pass"


def test_runs_that_pipe_into_tee_fail_when_the_first_command_fails():
    body = split_jobs(workflow_text())["test"]
    step = next(s for s in body.split("\n      - name: ") if "| tee pytest_output.txt" in s)
    assert "set -o pipefail" in step and step.index("set -o pipefail") < step.index("pytest tests/")


def test_no_run_block_interpolates_a_github_expression_into_the_shell():
    """`${{ github.head_ref }}` (or any user-controlled value) inside a `run:` is script injection. zizmor reports
    it, but the security job is not a required check, so this test makes it a failing test as well."""
    for path in all_workflows():
        for job_id, body in split_jobs(path.read_text(encoding="utf-8")).items():
            for step in body.split("\n      - name: "):
                if "\n        run:" in step:
                    run_part = step.split("\n        run:", 1)[1]
                    assert "${{" not in run_part, (path.name, job_id, step.splitlines()[0])


def test_in_progress_runs_are_cancelled_for_pull_requests_only():
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow_text()


def test_the_count_check_in_the_test_job_runs_after_the_suite_and_never_writes():
    body = split_jobs(workflow_text())["test"]
    assert body.index("pytest tests/") < body.index("check_test_count.py pytest_output.txt")
    assert "check_test_count.py pytest_output.txt --write" not in body


def test_the_windows_job_runs_the_suite_on_windows():
    body = split_jobs(workflow_text())["test-windows"]
    assert "runs-on: windows-latest" in body and "pytest tests/ -n 2 --dist=loadfile --durations=30" in body
    assert "pytest-xdist>=3.8.0" in (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")


def test_the_runtime_job_installs_only_the_runtime_requirements():
    body = split_jobs(workflow_text())["runtime-smoke"]
    installs = [line.strip() for line in body.splitlines() if re.search(r"^\s*pip install ", line)]
    assert installs == ["pip install -c constraints.txt -r requirements.txt"]
    assert "tests/runtime_smoke.py --expect-clean" in body


def test_every_python_install_profile_uses_the_shared_constraints_file():
    for job_id in REQUIRED_JOBS:
        body = split_jobs(workflow_text())[job_id]
        install_lines = [line for line in body.splitlines() if re.search(r"\bpip install\b", line)]
        assert install_lines, job_id
        assert all("-c constraints.txt" in line for line in install_lines), (job_id, install_lines)
        assert "constraints.txt" in body, f"{job_id} cache must include the constraints file"

    mutation = MUTATION_WORKFLOW.read_text(encoding="utf-8")
    mutation_job = split_jobs(mutation)["mutation"]
    assert "pip install -c constraints.txt -r requirements-dev.txt -r requirements-mutation.txt" in mutation_job
    assert "pip freeze > pip-freeze-mutation.txt" in mutation_job
    assert "pip-freeze-mutation.txt" in mutation_job

    benchmark = BENCHMARK_WORKFLOW.read_text(encoding="utf-8")
    benchmark_job = split_jobs(benchmark)["benchmark"]
    assert "pip install -c constraints.txt -r requirements-dev.txt" in benchmark_job
    assert "constraints.txt" in benchmark_job


def test_constraints_file_uniquely_pins_every_declared_requirement():
    pins = {}
    for raw_line in CONSTRAINTS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.partition("#")[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        assert match, f"constraints.txt entries must be exact pins: {raw_line!r}"
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        assert name not in pins, f"duplicate normalized constraint: {name}"
        pins[name] = match.group(2)

    for filename in ("requirements.txt", "requirements-dev.txt", "requirements-security.txt",
                     "requirements-mutation.txt"):
        path = REPO_ROOT / filename
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.partition("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            # The distribution name runs up to the first extras bracket, marker, space or version operator.
            name_part = re.split(r"[\s;\[<>=!~]", line, maxsplit=1)[0]
            has_operator = any(op in line for op in ("===", "==", "~=", "!=", "<=", ">=", "<", ">"))
            assert re.fullmatch(r"[A-Za-z0-9_.-]+", name_part) and has_operator, (
                f"unrecognised requirement syntax in {filename}: {raw_line!r}")
            name = re.sub(r"[-_.]+", "-", name_part).lower()
            assert name in pins, f"{filename} dependency {name} has no exact constraint pin"


def test_the_security_job_audits_every_requirements_file_and_the_workflows():
    body = split_jobs(workflow_text())["security"]
    audit_line = next(line.strip() for line in body.splitlines() if line.strip().startswith("run: pip-audit"))
    assert "-r constraints.txt" in audit_line
    for requirements in ("requirements.txt", "requirements-dev.txt", "requirements-security.txt",
                         "requirements-mutation.txt"):
        assert f"-r {requirements}" in audit_line
    assert "zizmor .github/workflows" in body
    assert "pip install -c constraints.txt -r requirements-security.txt" in body


def test_the_environment_and_coverage_records_are_uploaded_even_when_a_step_fails():
    for job_id in ("test", "runtime-smoke", "security"):
        body = split_jobs(workflow_text())[job_id]
        assert "pip freeze >" in body
        step = next(s for s in body.split("\n      - name: ") if "actions/upload-artifact@" in s)
        assert "if: always()" in step and "retention-days" in step, job_id


def test_the_security_requirements_file_names_both_tools():
    names = {re.split(r"[<>=!~ ]", line.strip())[0] for line in
             (REPO_ROOT / "requirements-security.txt").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")}
    assert names == {"pip-audit", "zizmor"}


# ---------------------------------------------------------------------------
# Ignoring a reviewed advisory is documented, deliberate and never a weakening of the audit (issue #141)
# ---------------------------------------------------------------------------

IGNORE_VULN_RE = re.compile(r"--ignore-vuln(?:\s+|=)([^\s\\]+)")
IGNORE_COMMENT = (r"^\s*#\s*pip-audit ignore:\s*{id}\s+--\s+\S.*--\s+reason:\s+\S.*--\s+revisit by:\s+"
                  r"\d{{4}}-\d{{2}}-\d{{2}}\s*$")
WEAKENINGS = ("continue-on-error", "|| true", "||true", "--no-deps", "--disable-pip")


def ignored_advisories(job_text):
    """IDs passed to pip-audit as `--ignore-vuln <ID>` (or `=<ID>`) anywhere in the job."""
    return IGNORE_VULN_RE.findall(job_text)


def undocumented_ignores(job_text):
    """Ignored IDs with no comment of the documented shape:
    `# pip-audit ignore: <ID> -- <package> -- reason: <why> -- revisit by: YYYY-MM-DD`."""
    lines = job_text.splitlines()
    return [advisory for advisory in ignored_advisories(job_text)
            if not any(re.match(IGNORE_COMMENT.format(id=re.escape(advisory)), line) for line in lines)]


def test_the_security_job_ignores_no_advisory_without_a_documented_reason():
    body = split_jobs(workflow_text())["security"]
    assert undocumented_ignores(body) == []


def test_the_security_job_has_nothing_that_weakens_the_audit():
    body = split_jobs(workflow_text())["security"]
    code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    for weakening in WEAKENINGS:
        assert weakening not in code, weakening
    assert "pip-audit -r requirements.txt" in code and "zizmor .github/workflows" in code


def test_the_ignore_checks_can_fail():
    """The two checks above must be able to catch what they exist for."""
    bare = "run: pip-audit -r requirements.txt --ignore-vuln GHSA-xxxx-yyyy-zzzz"
    assert ignored_advisories(bare) == ["GHSA-xxxx-yyyy-zzzz"]
    assert undocumented_ignores(bare) == ["GHSA-xxxx-yyyy-zzzz"]
    assert ignored_advisories("pip-audit --ignore-vuln=PYSEC-2024-1 \\\n --ignore-vuln CVE-2025-1") == [
        "PYSEC-2024-1", "CVE-2025-1"]
    documented = ("# pip-audit ignore: GHSA-xxxx-yyyy-zzzz -- examplepkg -- reason: only reachable through a "
                  "feature this project does not use -- revisit by: 2027-01-31\n" + bare)
    assert undocumented_ignores(documented) == []
    for incomplete in ("# pip-audit ignore: GHSA-xxxx-yyyy-zzzz -- examplepkg -- revisit by: 2027-01-31",
                       "# pip-audit ignore: GHSA-xxxx-yyyy-zzzz -- examplepkg -- reason:  -- revisit by: 2027-01-31",
                       "# pip-audit ignore: GHSA-xxxx-yyyy-zzzz -- examplepkg -- reason: ok -- revisit by: soon",
                       "# ignore GHSA-xxxx-yyyy-zzzz because reasons",
                       "# pip-audit ignore: GHSA-other -- examplepkg -- reason: ok -- revisit by: 2027-01-31"):
        assert undocumented_ignores(incomplete + "\n" + bare) == ["GHSA-xxxx-yyyy-zzzz"], incomplete
    assert undocumented_ignores("run: pip-audit -r requirements.txt") == []


def test_the_advisory_ignore_procedure_is_documented_where_a_contributor_looks():
    for name in ("CLAUDE.md", "docs/testing.md"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "--ignore-vuln" in text and "pip-audit" in text, name
    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "# pip-audit ignore: <ID> -- <package> -- reason:" in claude and "revisit by: YYYY-MM-DD" in claude


# ---------------------------------------------------------------------------
# Every workflow file (issue #138, #141) and the weekly mutation workflow (issue #146)
# ---------------------------------------------------------------------------

MUTATION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "mutation.yml"
MODEL_FRESHNESS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "model-freshness.yml"
WIKI_PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "wiki-publish.yml"


def all_workflows():
    files = sorted(WORKFLOW.parent.glob("*.y*ml"))
    assert len(files) >= 2, files
    return files


def test_every_workflow_file_is_sha_pinned_least_privilege_and_credential_free():
    for path in all_workflows():
        text = path.read_text(encoding="utf-8")
        assert unpinned_actions(text) == [], path.name
        assert re.search(r"^permissions:\s*\{\}\s*$", text, re.M), path.name
        assert "pull_request_target" not in text and "workflow_run" not in text, path.name
        for job_id, body in split_jobs(text).items():
            # The two documented permission exceptions (like model-freshness's issues: write):
            # wiki-publish's single publish job holds contents: write to push the generated wiki (#192).
            if path.name == "model-freshness.yml":
                expected_permissions = {"contents": "read", "issues": "write"}
            elif path.name == "wiki-publish.yml":
                expected_permissions = {"contents": "write"}
            else:
                expected_permissions = {"contents": "read"}
            assert job_permissions(body) == expected_permissions, (path.name, job_id)
            assert len(re.findall(r"persist-credentials:\s*false", body)) == body.count("actions/checkout@"), \
                (path.name, job_id)
            assert "timeout-minutes:" in body, (path.name, job_id)

    # Issue #212's benchmark must be manual-only and compare serial, 2-worker and 4-worker
    # full-suite runs on both operating systems; it must never add work to every PR.
    benchmark = (WORKFLOW.parent / "ci-parallelism-benchmark.yml").read_text(encoding="utf-8")
    triggers = benchmark.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert set(re.findall(r"^  ([a-z_]+):", triggers, re.M)) == {"workflow_dispatch"}
    assert "os: [ubuntu-latest, windows-latest]" in benchmark
    assert "workers: [1, 2, 4]" in benchmark
    assert "repeat: [1, 2, 3]" in benchmark
    assert "max-parallel: 6" in benchmark
    assert "pytest tests/ -n 2 --dist=loadfile --durations=30" in benchmark
    assert "pytest tests/ -n 4 --dist=loadfile --durations=30" in benchmark
    assert "python scripts/check_test_count.py pytest_output.txt" in benchmark
    assert "python scripts/check_coverage.py coverage.json" in benchmark
    assert "diff-cover coverage.xml --config-file pyproject.toml" in benchmark


# ---------------------------------------------------------------------------
# The parallelism benchmark kept for the next tuning pass (issue #269)
# ---------------------------------------------------------------------------

BENCHMARK_WORKFLOW = WORKFLOW.parent / "ci-parallelism-benchmark.yml"
SUMMARY_STEP = "Write the timing to the job summary"


def benchmark_summary_step():
    """(the summary step's YAML before `run:`, the Python it runs). The step uses `shell: python`, so its script is
    read out of the workflow and run by the tests exactly as the runner would run it."""
    text = BENCHMARK_WORKFLOW.read_text(encoding="utf-8")
    (step,) = [s for s in text.split("\n      - name: ") if s.startswith(SUMMARY_STEP)]
    head, _, body = step.partition("\n        run: |\n")
    lines = []
    for line in body.splitlines():
        if line.strip() and not line.startswith("          "):
            break
        lines.append(line)
    return head, textwrap.dedent("\n".join(lines))


def run_benchmark_summary(tmp_path, log_bytes, os_name, workers, repeat):
    """Run the step's script in `tmp_path` with the environment the runner gives it; return the summary's lines."""
    if log_bytes is not None:
        (tmp_path / "pytest_output.txt").write_bytes(log_bytes)
    summary = tmp_path / "step_summary.md"
    summary.write_text("an earlier step's summary\n", encoding="utf-8")
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(summary), "BENCHMARK_OS": os_name,
           "BENCHMARK_WORKERS": workers, "BENCHMARK_REPEAT": repeat}
    _, script = benchmark_summary_step()
    subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, check=True, capture_output=True,
                   timeout=60)
    return summary.read_text(encoding="utf-8").splitlines()


def test_the_benchmark_header_carries_its_instructions_and_points_at_the_runbook():
    """The workflow is kept for a later tuning pass, so it says how to use it where a maintainer will find it: never a
    gate, what it costs, what branch to run it on, medians not single runs, and where the last baseline is."""
    header = BENCHMARK_WORKFLOW.read_text(encoding="utf-8").split("\non:\n", 1)[0]
    for phrase in ("Never a gate", "18 jobs", "badges/test-count.json", "median of the three repeats",
                   "issues/212#issuecomment-", '"Re-tuning test parallelism"'):
        assert phrase in header, phrase
    runbook = (REPO_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    assert "\n## Re-tuning test parallelism\n" in runbook, "the header names a runbook section that must exist"


def test_the_benchmark_summary_step_runs_last_on_every_job_and_takes_the_matrix_from_the_environment():
    """`if: always()`: a job whose gate failed still reports its timing. The matrix values reach the script through
    `env:`, never through an expression inside `run:` (the injection test above checks every workflow)."""
    head, script = benchmark_summary_step()
    assert "if: always()" in head and "shell: python" in head
    for variable, key in (("BENCHMARK_OS", "os"), ("BENCHMARK_WORKERS", "workers"), ("BENCHMARK_REPEAT", "repeat")):
        assert f"{variable}: ${{{{ matrix.{key} }}}}" in head
        assert f'os.environ["{variable}"]' in script
    text = BENCHMARK_WORKFLOW.read_text(encoding="utf-8")
    assert text.index(f"- name: {SUMMARY_STEP}") > text.rindex("pytest tests/"), "after every suite run"


LINUX_LOG = (
    "============================= test session starts ==============================\n"
    "Name                              Stmts   Miss Branch BrPart  Cover   Missing\n"
    "TOTAL                              4439     45   1600     44    98%\n"
    "================= 2850 passed, 5 xfailed in 86.41s (0:01:26) =================\n")
WINDOWS_LOG = "============ 2850 passed, 5 xfailed in 57.20s ============\n"
FAILED_LOG = "== short test summary info ==\nFAILED tests/test_x.py::test_y\n== 1 failed, 2849 passed in 90.02s (0:01:30) ==\n"


@pytest.mark.parametrize("log_bytes, matrix, row", [
    (LINUX_LOG.encode("utf-8"), ("ubuntu-latest", "4", "2"),
     "| ubuntu-latest | 4 | 2 | 86.41 | 98% | 2850 passed, 5 xfailed |"),
    (WINDOWS_LOG.encode("utf-16"), ("windows-latest", "2", "1"),                  # an older Tee-Object: UTF-16, BOM
     "| windows-latest | 2 | 1 | 57.20 | n/a | 2850 passed, 5 xfailed |"),
    (WINDOWS_LOG.encode("utf-8"), ("windows-latest", "1", "3"),
     "| windows-latest | 1 | 3 | 57.20 | n/a | 2850 passed, 5 xfailed |"),
    (FAILED_LOG.encode("utf-8"), ("ubuntu-latest", "1", "1"),
     "| ubuntu-latest | 1 | 1 | 90.02 | n/a | 1 failed, 2849 passed |"),
    (b"collected 2850 items\n\xff\xfe broken \x80 bytes\n" + WINDOWS_LOG.encode("utf-8"), ("ubuntu-latest", "2", "2"),
     "| ubuntu-latest | 2 | 2 | 57.20 | n/a | 2850 passed, 5 xfailed |"),
    (b"collected 2850 items\n", ("ubuntu-latest", "4", "1"),                     # cut off before the summary line
     "| ubuntu-latest | 4 | 1 | n/a | n/a | no pytest summary line in pytest_output.txt |"),
    (None, ("windows-latest", "4", "2"),                                          # no log at all
     "| windows-latest | 4 | 2 | n/a | n/a | no pytest summary line in pytest_output.txt |"),
])
def test_the_benchmark_summary_step_writes_one_row_with_pytests_own_timing(tmp_path, log_bytes, matrix, row):
    """The row holds pytest's reported elapsed time (not the job's duration), the coverage total when the log has one,
    and the result line; a missing or truncated log becomes a row saying so, never a failed step. It appends, so an
    earlier step's summary is kept."""
    assert run_benchmark_summary(tmp_path, log_bytes, *matrix) == [
        "an earlier step's summary",
        "| OS | Workers | Repeat | pytest elapsed (s) | Coverage total | Result |",
        "| :--- | ---: | ---: | ---: | ---: | :--- |",
        row]


def test_model_freshness_workflow_is_weekly_and_manual_only_never_a_pull_request_check():
    text = MODEL_FRESHNESS_WORKFLOW.read_text(encoding="utf-8")
    triggers = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert set(re.findall(r"^  ([a-z_]+):", triggers, re.M)) == {"schedule", "workflow_dispatch"}
    assert re.search(r'cron: "\d{1,2} \d{1,2} \* \* [0-6]"', triggers)
    assert "pull_request" not in triggers and "push:" not in triggers
    assert "pull_request_target" not in text and "workflow_run" not in text
    assert re.search(r"^permissions:\s*\{\}\s*$", text, re.M)
    jobs = split_jobs(text)
    assert list(jobs) == ["model_freshness"]
    assert job_permissions(jobs["model_freshness"]) == {"contents": "read", "issues": "write"}
    assert "GITHUB_TOKEN: ${{ github.token }}" in text
    assert 'export GH_TOKEN="$GITHUB_TOKEN"' in text
    assert "--state open" in text and "--json number,title" in text
    assert 'select(.title == "Model freshness report")' in text
    assert 'gh issue edit "$issue_number"' in text
    assert 'gh issue create --repo "$GITHUB_REPOSITORY" --title "Model freshness report"' in text
    assert "--add-label tech-debt" in text and "--label tech-debt" in text
    assert "No model freshness findings; no issue created or updated." in text
    assert "check_model_freshness.py --json model-freshness-report.json" in text
    assert "contents: write" not in text and "issues: read" not in text
    assert "config/settings.json" not in text.split("run:", 1)[-1]
    assert "git push" not in text


def test_the_mutation_workflow_is_weekly_and_manual_only_never_on_a_pull_request_or_push():
    text = MUTATION_WORKFLOW.read_text(encoding="utf-8")
    triggers = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert set(re.findall(r"^  ([a-z_]+):", triggers, re.M)) == {"schedule", "workflow_dispatch"}
    assert re.search(r'cron: "\d{1,2} \d{1,2} \* \* [0-6]"', triggers), "a weekly schedule: fixed minute, hour and weekday"


def test_the_mutation_workflow_is_a_diagnostic_report_not_a_gate():
    jobs = split_jobs(MUTATION_WORKFLOW.read_text(encoding="utf-8"))
    assert list(jobs) == ["mutation"]
    body = jobs["mutation"]
    assert "runs-on: ubuntu-latest" in body                       # mutmut forks: no Windows
    assert int(re.search(r"timeout-minutes:\s*(\d+)", body).group(1)) <= 120
    run_step = next(s for s in body.split("\n      - name: ") if s.startswith("Run mutmut"))
    assert "continue-on-error: true" in run_step                  # survivors never fail the job by themselves...
    assert int(re.search(r"timeout-minutes:\s*(\d+)", run_step).group(1)) < 120, "a step timeout, so reports run"
    final = body.split("- name: Fail if mutmut produced no result", 1)[1]
    assert "if: always()" in final and "ran == 0" in final         # ...but a run that tested nothing does
    assert body.index("ln -s scripts src") < body.index("mutmut run")
    upload = next(s for s in body.split("\n      - name: ") if "actions/upload-artifact@" in s)
    assert "if: always()" in upload and "mutmut-run.log" in upload and "mutmut-cicd-stats.json" in upload
    assert "mutants/src/*.meta" in upload and "mutants/scripts" not in upload    # the src alias is the real path
    assert 's["no_tests"]' not in final.split("ran = ", 1)[1].splitlines()[0], "no_tests is not a run mutant"
    assert "--fail-under" not in body and "--min" not in body, "no threshold is enforced until the first run is read"


def test_the_mutation_workflow_produces_the_per_module_report_without_ever_failing_over_it():
    body = split_jobs(MUTATION_WORKFLOW.read_text(encoding="utf-8"))["mutation"]
    collect = next(s for s in body.split("\n      - name: ") if s.startswith("Collect results"))
    assert "mutmut results --all true > mutation-all.txt" in collect       # every mutant, killed ones included
    report = collect.split("if ! python scripts/mutation_report.py", 1)[1]
    assert report.startswith(" mutation-all.txt --json mutation-report.json")
    assert "if ! python scripts/mutation_report.py" in collect and "could not be produced" in collect
    upload = next(s for s in body.split("\n      - name: ") if "actions/upload-artifact@" in s)
    assert "mutation-all.txt" in upload and "mutation-report.json" in upload


def test_the_mutation_workflow_takes_a_name_filter_and_a_full_flag_that_never_reach_a_script_directly():
    """Issue #435: manual runs may retest chosen modules; the inputs go through env and a strict character check."""
    text = MUTATION_WORKFLOW.read_text(encoding="utf-8")
    dispatch = text.split("  workflow_dispatch:\n", 1)[1].split("\npermissions:", 1)[0]
    assert re.search(r"^      mutants:\n(?:        .*\n)*?        type: string", dispatch, re.M)
    assert re.search(r"^      full:\n(?:        .*\n)*?        type: boolean", dispatch, re.M)
    for line in text.splitlines():
        if "inputs." in line and "${{" in line:
            assert re.fullmatch(r"\s+(MUTANTS|FULL): \$\{\{ inputs\.(mutants|full) \}\}", line), line
    body = split_jobs(text)["mutation"]
    mode = next(s for s in body.split("\n      - name: ") if s.startswith("Decide the run mode"))
    assert "grep -Eq '^[A-Za-z0-9_.*? ]+$'" in mode and "exit 1" in mode
    assert "date -u +%d" in mode and '"$EVENT" = "schedule"' in mode and "-le 7" in mode   # first Monday: a full run
    run_step = next(s for s in body.split("\n      - name: ") if s.startswith("Run mutmut"))
    assert 'read -r -a globs <<< "$MUTANTS"' in run_step and 'mutmut run "${globs[@]}"' in run_step


def test_the_mutation_workflow_reuses_verdicts_between_runs_and_retests_the_survivors():
    """Issue #435: restore before the run, save after the report, never save a filtered run, rerun survivors."""
    body = split_jobs(MUTATION_WORKFLOW.read_text(encoding="utf-8"))["mutation"]
    steps = body.split("\n      - name: ")
    names = [step.split("\n", 1)[0] for step in steps[1:]]
    order = ["Decide the run mode", "Restore the mutation results", "Run mutmut", "Collect results",
             "Save the mutation results"]
    assert [names.index(name) for name in order] == sorted(names.index(name) for name in order)
    restore = steps[1 + names.index("Restore the mutation results")]
    save = steps[1 + names.index("Save the mutation results")]
    assert "actions/cache/restore@" in restore and "actions/cache/save@" in save
    assert "steps.mode.outputs.mode != 'full'" in restore              # a full run starts from nothing
    assert "if: always() && steps.mode.outputs.mode != 'filtered'" in save
    key = "hashFiles('requirements-dev.txt', 'requirements-mutation.txt', 'constraints.txt', 'pyproject.toml')"
    assert key in restore and key in save and "github.run_id" in save
    run_step = steps[1 + names.index("Run mutmut")]
    assert "rm -rf mutants" in run_step.split("full)", 1)[1].split(";;", 1)[0]
    survivors = run_step.split("RESTORED:-", 1)[1]
    assert "mutmut results | awk" in survivors and "mutmut run $(cat survivors.txt)" in survivors


def test_the_mutation_job_is_not_one_of_the_required_checks():
    required = set(REQUIRED_JOBS)
    assert "mutation" not in required
    assert "mutation" not in split_jobs(workflow_text())


def test_mutmut_is_installed_by_the_mutation_workflow_only():
    mutation_requirements = (REPO_ROOT / "requirements-mutation.txt").read_text(encoding="utf-8")
    assert re.search(r"^mutmut>=\d", mutation_requirements, re.M)
    assert "mutmut" not in (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "mutmut" not in workflow_text()
    assert "-r requirements-mutation.txt" in MUTATION_WORKFLOW.read_text(encoding="utf-8")


SUPPORTING_MUTATED_MODULES = {"calibrate"}      # the calibrate chunker/merge helpers (issue #146): scored, not critical


def test_the_mutation_config_mutates_exactly_the_critical_modules_plus_the_named_supporting_ones():
    tomllib = pytest.importorskip("tomllib")
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
    critical = {f"src/{m}.py" for m in config["opencam"]["coverage"]["critical_modules"]}   # src/ is scripts/
    supporting = {f"src/{m}.py" for m in SUPPORTING_MUTATED_MODULES}
    assert set(config["mutmut"]["only_mutate"]) == critical | supporting
    assert "tests/test_calibrate.py" in config["mutmut"]["pytest_add_cli_args_test_selection"]
    for test_file in config["mutmut"]["pytest_add_cli_args_test_selection"]:
        assert (REPO_ROOT / test_file).is_file(), test_file
    assert ".github" in config["mutmut"]["also_copy"]
    assert "docs/examples" in config["mutmut"]["also_copy"]    # test_deal_export reads docs/examples/synthetic_co (issue #376)
    assert config["mutmut"]["source_paths"] == ["src"]
    for module in config["opencam"]["coverage"]["critical_modules"]:
        assert (REPO_ROOT / "scripts" / f"{module}.py").is_file(), module


def test_the_model_freshness_workflow_is_the_documented_unpinned_exception():
    """Every other install uses constraints.txt; this one must not, and the operations guide says so."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "model-freshness.yml").read_text(encoding="utf-8")
    assert "constraints.txt" not in workflow, "the freshness report must see the newest SDK, not the pinned one"
    operations = " ".join((REPO_ROOT / "docs" / "operations.md").read_text(encoding="utf-8").split())
    assert "`model-freshness` workflow is the one deliberate exception" in operations


def test_the_wiki_publish_workflow_publishes_docs_one_way_and_never_runs_on_a_pull_request():
    """The wiki publisher (#192): one job, main-only, never on a fork or a pull request, and not a
    required check. docs/ is canonical; the wiki is generated and overwritten on every publish."""
    text = WIKI_PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    triggers = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert set(re.findall(r"^  ([a-z_]+):", triggers, re.M)) == {"push", "workflow_dispatch"}
    assert re.search(r'^    branches: \[ "main" \]$', triggers, re.M)
    assert re.findall(r'^      - "(.+)"$', triggers, re.M) == [
        "docs/**", "scripts/wiki_publish.py", ".github/workflows/wiki-publish.yml"]
    for forbidden in ("pull_request", "workflow_run", "gollum", "schedule"):
        assert forbidden not in text, forbidden
    assert re.search(r"^permissions:\s*\{\}\s*$", text, re.M)
    assert re.search(r"^concurrency:\n  group: wiki-publish\n  cancel-in-progress: false$", text, re.M)
    jobs = split_jobs(text)
    assert list(jobs) == ["publish"]
    body = jobs["publish"]
    assert "runs-on: ubuntu-latest" in body
    assert re.search(r"timeout-minutes:\s*10\b", body)
    assert job_permissions(body) == {"contents": "write"}
    # the job-level guard is the exact conjunction: main only, and never a fork (a fork's
    # wiki is not this repository's); either guard alone, or an OR, must not pass
    guard = re.search(r"^    if: (.+)$", body, re.M)
    assert guard and guard.group(1) == "github.ref == 'refs/heads/main' && github.event.repository.fork == false"
    # one pinned action only; no credentials left in the checkout; no Python setup or install
    assert uses_lines(text) == [("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1")]
    assert "persist-credentials: false" in body and "fetch-depth: 1" in body
    assert "setup-python" not in text and "pip install" not in text
    # the token and the run's own facts reach the step only through env: mappings, and
    # nothing is interpolated into the shell
    step = next(s for s in body.split("\n      - name: ") if s.startswith("Publish the wiki"))
    for mapping in ("GITHUB_TOKEN: ${{ github.token }}",
                    "GITHUB_REPOSITORY: ${{ github.repository }}",
                    "GITHUB_SHA: ${{ github.sha }}"):
        assert mapping in step, mapping
    assert 'python3 scripts/wiki_publish.py --repo "$GITHUB_REPOSITORY" --publish --source-sha "$GITHUB_SHA"' in step
    assert "${{" not in step.split("run:", 1)[1]


def test_the_wiki_publish_guard_and_path_assertions_can_fail():
    """The exact-conjunction guard check and the exact paths list above must be able to catch
    what they exist for (the helper-test precedent: an assertion that cannot fail pins nothing)."""
    expected_guard = "github.ref == 'refs/heads/main' && github.event.repository.fork == false"

    def guard_of(text):
        match = re.search(r"^    if: (.+)$", text, re.M)
        return match and match.group(1) == expected_guard

    assert guard_of(f"    if: {expected_guard}\n")
    assert not guard_of("    if: github.ref == 'refs/heads/main' || github.event.repository.fork == false\n")
    assert not guard_of("    if: github.ref == 'refs/heads/main'\n")
    assert not guard_of("    if: github.event.repository.fork == false\n")
    assert not guard_of("no guard here\n")

    def paths_of(text):
        return re.findall(r'^      - "(.+)"$', text, re.M)

    good_paths = 'on:\n  push:\n    branches: [ "main" ]\n    paths:\n      - "docs/**"\n      - "scripts/wiki_publish.py"\n      - ".github/workflows/wiki-publish.yml"\n'
    assert paths_of(good_paths) == ["docs/**", "scripts/wiki_publish.py", ".github/workflows/wiki-publish.yml"]
    assert paths_of(good_paths + '      - "**"\n') == ["docs/**", "scripts/wiki_publish.py",
                                                    ".github/workflows/wiki-publish.yml", "**"]


def test_contents_write_appears_in_no_other_workflow():
    """wiki-publish is the only workflow allowed to hold contents: write.

    (model-freshness.yml's issues: write is the other documented write permission,
    which is why this test pins contents: write specifically.)"""
    for path in all_workflows():
        if path.name == "wiki-publish.yml":
            continue
        assert "contents: write" not in path.read_text(encoding="utf-8"), path.name


def test_the_wiki_publish_workflow_is_the_documented_write_permission_exception():
    """Like model-freshness's issues: write, the one-job contents: write is documented in the runbook."""
    operations = " ".join((REPO_ROOT / "docs" / "operations.md").read_text(encoding="utf-8").split())
    assert "`wiki-publish` workflow is the one deliberate write-permission exception" in operations
