"""Guard: confidential locations must stay untracked AND git-ignored (issue #149).

CLAUDE.md's confidentiality rule says anything derived from real deal material lives in git-ignored
locations, and `.gitignore` lists them -- but nothing enforced it. `git add -f`, a rename, or an edit
to `.gitignore` could put confidential material into a public, forkable repository with every check
green. These tests run in the ordinary `test` job and fail, naming the offending path, when:

1. `git ls-files` lists any file under a protected location (tracked);
2. a protected location is no longer ignored by *the repository's own* `.gitignore` (a developer's
   global excludes file must not be able to mask a missing rule);
3. a `.gitignore` entry exists that is neither protected nor explicitly classified as not confidential
   (so a new ignore line forces a decision and the protected list cannot silently fall behind).

The guard's own logic is tested against throwaway git repositories, never the real working tree.
Adding a new confidential location means adding it to PROTECTED_PATHS in the same PR as its
`.gitignore` line.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Locations that must never be tracked. A trailing "/" means "everything under this directory".
PROTECTED_PATHS = (
    "inputs/",
    "deals/",
    "templates/local/",
    "config/style_guide.md",
    "config/credit_policy.md",
    "config/credit_policy_notes.md",
    "config/spreading_conventions.json",
    "config/deal_learnings.md",
    "evals/results/",
)

# Tracked files that are deliberately allowed under a protected location: path -> why. Empty today.
# An entry needs a real reason (for example a `.gitkeep` placeholder that holds no data).
ALLOWED_TRACKED = {}

# `.gitignore` entries that are intentionally NOT in PROTECTED_PATHS, each with the reason.
NOT_CONFIDENTIAL = {
    "__pycache__/": "Python bytecode cache; build noise, not data",
    "*.pyc": "Python bytecode; build noise, not data",
    ".venv/": "local virtual environment",
    "venv/": "local virtual environment",
    "pytest_output.txt": "CI's own scratch file (scripts/check_test_count.py)",
    ".env": "secrets file; covered by the repository's secret scanning and push protection (see #149)",
    "*.key": "secret key files; covered by the repository's secret scanning and push protection (see #149)",
}


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")


def _require_git():
    """Skip when git is missing locally -- but never silently in CI, where a skip would be a hole."""
    if shutil.which("git") is None:
        if os.environ.get("CI"):
            pytest.fail("git is not available in CI, so the confidential-path guard cannot run")
        pytest.skip("git not available")


def tracked_offenders(repo, protected=PROTECTED_PATHS, allowed=None):
    """Every tracked file under a protected location that is not explicitly allowed."""
    allowed = ALLOWED_TRACKED if allowed is None else allowed
    offenders = set()
    for path in protected:
        result = _git(repo, "ls-files", "-z", "--", path)
        if result.returncode != 0:  # never read a git failure as "nothing tracked"
            raise RuntimeError(f"git ls-files failed for {path!r}: {result.stderr.strip()}")
        offenders.update(f for f in result.stdout.split("\0") if f and f not in allowed)
    return sorted(offenders)


def _probe(path):
    """A path that sits inside a protected directory (ignore rules for a directory match what is in it)."""
    return f"{path}__ignore_probe__/probe.txt" if path.endswith("/") else path


def unprotected_paths(repo, protected=PROTECTED_PATHS):
    """(path, reason) for each protected location the repository's own .gitignore does not ignore.
    `--no-index` makes the answer independent of whether a file is already tracked, and `-v` names the
    rule's source so a global excludes file or .git/info/exclude cannot stand in for .gitignore."""
    problems = []
    for path in protected:
        result = _git(repo, "check-ignore", "--no-index", "-v", "--", _probe(path))
        if result.returncode == 1:
            problems.append((path, "no ignore rule matches it"))
        elif result.returncode != 0:
            raise RuntimeError(f"git check-ignore failed for {path!r}: {result.stderr.strip()}")
        else:
            # "<source>:<line>:<pattern>\t<path>"; exit 0 also covers a NEGATED pattern (`!path`), which
            # reports the match but means the path is NOT ignored.
            source, _line, pattern = result.stdout.split("\t", 1)[0].rsplit(":", 2)
            if pattern.startswith("!"):
                problems.append((path, f"re-included by the negated rule {pattern!r} in {source}"))
            elif source != ".gitignore":
                problems.append((path, f"only ignored by {source}, not by the repository's .gitignore"))
    return problems


def unclassified_gitignore_entries(gitignore_text, protected=PROTECTED_PATHS, not_confidential=None):
    """`.gitignore` entries that are neither protected nor classified as not confidential."""
    not_confidential = NOT_CONFIDENTIAL if not_confidential is None else not_confidential
    entries = [line.strip() for line in gitignore_text.splitlines()
               if line.strip() and not line.strip().startswith("#")]
    return [e for e in entries if e not in protected and e not in not_confidential]


def format_offenders(offenders):
    return ("Confidential material is tracked by git: " + ", ".join(offenders) + ". Untrack it with "
            "`git rm -r --cached <path>` and make sure .gitignore still covers it; if a tracked file here is "
            "deliberate (for example a `.gitkeep`), add it to ALLOWED_TRACKED with a reason.")


def format_unprotected(problems):
    return ("A confidential location is not git-ignored: " + "; ".join(f"{p} ({why})" for p, why in problems)
            + ". Restore its rule in the repository's .gitignore.")


# ---------------------------------------------------------------------------
# The real repository
# ---------------------------------------------------------------------------

@pytest.fixture
def real_repo():
    _require_git()
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout (for example a source archive)")
    return REPO_ROOT


def test_no_confidential_path_is_tracked_by_git(real_repo):
    offenders = tracked_offenders(real_repo)
    assert not offenders, format_offenders(offenders)


def test_every_confidential_path_is_ignored_by_the_repos_own_gitignore(real_repo):
    problems = unprotected_paths(real_repo)
    assert not problems, format_unprotected(problems)


def test_every_gitignore_entry_is_classified():
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    unclassified = unclassified_gitignore_entries(text)
    assert not unclassified, (
        f".gitignore entries {unclassified} are in neither PROTECTED_PATHS nor NOT_CONFIDENTIAL in "
        "tests/test_confidential_paths.py. Decide which they are: confidential material belongs in "
        "PROTECTED_PATHS; build noise or secrets already covered elsewhere goes in NOT_CONFIDENTIAL "
        "with a reason.")


def test_the_allow_list_and_the_classification_carry_reasons():
    for table in (ALLOWED_TRACKED, NOT_CONFIDENTIAL):
        for path, reason in table.items():
            assert isinstance(reason, str) and reason.strip(), f"{path} needs a reason"


def test_a_protected_path_is_never_also_classified_as_not_confidential():
    assert not set(PROTECTED_PATHS) & set(NOT_CONFIDENTIAL)


# ---------------------------------------------------------------------------
# The guard itself, against throwaway repositories
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    """A throwaway git repository whose .gitignore covers every protected path."""
    _require_git()
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-q").returncode == 0
    # keep the developer's own excludes out of these tests unless a test installs one on purpose
    _git(root, "config", "core.excludesFile", os.devnull)
    (root / ".gitignore").write_text("\n".join(PROTECTED_PATHS) + "\n", encoding="utf-8")
    return root


def _force_add(repo, relative, content="private"):
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    result = _git(repo, "add", "-f", "--", relative)
    assert result.returncode == 0, result.stderr


def test_a_clean_repository_passes_both_checks(repo):
    assert tracked_offenders(repo) == []
    assert unprotected_paths(repo) == []


def test_tracking_an_ordinary_file_elsewhere_is_fine(repo):
    _force_add(repo, "scripts/tool.py", "print('hi')")
    _force_add(repo, "evals/baselines/summary.json", "{}")  # a deliberate manual baseline is tracked
    assert tracked_offenders(repo) == []


@pytest.mark.parametrize("relative", [
    "inputs/calibration_samples/sample.pdf",
    "deals/Some Company/Proposal_2026-01-01/state.json",
    "deals/Some Company/_learnings.md",
    "templates/local/cam/asset_finance_cam.md",
    "config/style_guide.md",
    "config/credit_policy.md",
    "config/credit_policy_notes.md",
    "config/spreading_conventions.json",
    "config/deal_learnings.md",
    "evals/results/20260101T000000Z/results.json",
])
def test_a_force_added_protected_file_is_caught_and_named(repo, relative):
    _force_add(repo, relative)
    offenders = tracked_offenders(repo)
    assert offenders == [relative]
    assert relative in format_offenders(offenders)


def test_every_offender_is_named_not_just_the_first(repo):
    _force_add(repo, "inputs/a.pdf")
    _force_add(repo, "deals/Co/state.json")
    assert tracked_offenders(repo) == ["deals/Co/state.json", "inputs/a.pdf"]


def test_an_allow_listed_placeholder_is_permitted_but_only_that_file(repo):
    _force_add(repo, "inputs/.gitkeep", "")
    _force_add(repo, "inputs/real.pdf")
    allowed = {"inputs/.gitkeep": "keeps the empty directory in a fresh clone; holds no data"}
    assert tracked_offenders(repo, allowed=allowed) == ["inputs/real.pdf"]
    assert tracked_offenders(repo, allowed={}) == ["inputs/.gitkeep", "inputs/real.pdf"]


@pytest.mark.parametrize("protected", PROTECTED_PATHS)
def test_removing_the_ignore_rule_for_any_protected_path_is_caught(repo, protected):
    kept = [p for p in PROTECTED_PATHS if p != protected]
    (repo / ".gitignore").write_text("\n".join(kept) + "\n", encoding="utf-8")
    problems = unprotected_paths(repo)
    assert [p for p, _ in problems] == [protected]
    assert protected in format_unprotected(problems)


def test_a_missing_gitignore_flags_every_path(repo):
    (repo / ".gitignore").unlink()
    assert [p for p, _ in unprotected_paths(repo)] == list(PROTECTED_PATHS)


def test_a_developers_global_excludes_cannot_stand_in_for_the_repos_gitignore(repo, tmp_path):
    excludes = tmp_path / "global_ignore"
    excludes.write_text("deals/\n", encoding="utf-8")
    _git(repo, "config", "core.excludesFile", str(excludes))
    kept = [p for p in PROTECTED_PATHS if p != "deals/"]
    (repo / ".gitignore").write_text("\n".join(kept) + "\n", encoding="utf-8")
    problems = unprotected_paths(repo)
    assert [p for p, _ in problems] == ["deals/"]
    assert "not by the repository's .gitignore" in problems[0][1]


def test_info_exclude_cannot_stand_in_for_the_repos_gitignore(repo):
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("inputs/\n", encoding="utf-8")
    (repo / ".gitignore").write_text(
        "\n".join(p for p in PROTECTED_PATHS if p != "inputs/") + "\n", encoding="utf-8")
    assert [p for p, _ in unprotected_paths(repo)] == ["inputs/"]


def test_a_negation_that_unignores_a_protected_path_is_caught(repo):
    (repo / ".gitignore").write_text("\n".join(PROTECTED_PATHS) + "\n!config/style_guide.md\n", encoding="utf-8")
    assert [p for p, _ in unprotected_paths(repo)] == ["config/style_guide.md"]


def test_ignore_status_is_independent_of_whether_a_file_is_already_tracked(repo):
    _force_add(repo, "config/credit_policy.md")
    assert unprotected_paths(repo) == []  # still covered by the rule...
    assert tracked_offenders(repo) == ["config/credit_policy.md"]  # ...and still caught as tracked


def test_a_git_failure_is_an_error_not_a_pass(tmp_path):
    _require_git()
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(RuntimeError, match="git ls-files failed"):
        tracked_offenders(not_a_repo)
    with pytest.raises(RuntimeError, match="git check-ignore failed"):
        unprotected_paths(not_a_repo)


def test_new_unclassified_gitignore_entries_are_flagged():
    text = "# comment\n\ninputs/\nnew_secret_folder/\n__pycache__/\n*.pyc\n"
    assert unclassified_gitignore_entries(text) == ["new_secret_folder/"]
    assert unclassified_gitignore_entries("inputs/\nvenv/\n") == []


def test_missing_git_skips_locally_but_fails_in_ci(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception):
        _require_git()
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="cannot run"):
        _require_git()
