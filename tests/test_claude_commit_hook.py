"""Offline contract tests for the project-level Claude Code main-branch guard (#445)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".claude" / "hooks" / "deny_main_commit.py"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
CANONICAL_ORIGIN = "https://github.com/ShamikM88/open-cam-framework.git"


def _git(cwd, *args, check=True):
    """Run Git against a throwaway fixture only; never inherit hook-provided GIT_* redirections."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=15,
    )
    if check and result.returncode:
        raise AssertionError(f"git {args!r} failed ({result.returncode}): {result.stderr}")
    return result


def _bash_path(path):
    """Render a local path with forward slashes for Bash, including on Windows."""
    return str(path).replace("\\\\", "/")


def _repo(path, branch="main", origin=CANONICAL_ORIGIN):
    path.mkdir(parents=True)
    _git(path, "init", "--initial-branch", branch)
    _git(path, "config", "user.name", "Synthetic Test")
    _git(path, "config", "user.email", "synthetic@example.invalid")
    (path / "seed.txt").write_text("synthetic only\n", encoding="utf-8")
    _git(path, "add", "seed.txt")
    _git(path, "commit", "-m", "synthetic seed")
    if origin is not None:
        _git(path, "remote", "add", "origin", origin)
    return path


def _payload(command, cwd, tool_name="Bash"):
    return {
        "session_id": "synthetic-session",
        "transcript_path": "",
        "cwd": str(cwd),
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": command},
    }


def _invoke(command, cwd, tool_name="Bash"):
    return subprocess.run(
        [sys.executable, str(HOOK)], cwd=str(cwd),
        input=json.dumps(_payload(command, cwd, tool_name)),
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )


def _decision(result):
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def _assert_denied(result):
    decision = _decision(result)
    assert decision["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = decision["hookSpecificOutput"]["permissionDecisionReason"].casefold()
    assert "main" in reason or "master" in reason
    assert "worktree" in reason or "feature branch" in reason
    return decision


def _assert_asks(result):
    decision = _decision(result)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert decision["hookSpecificOutput"]["permissionDecisionReason"]
    return decision


def test_project_settings_register_exactly_one_pretooluse_guard_with_both_shell_paths():
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    pretool = settings["hooks"]["PreToolUse"]
    assert len(pretool) == 1
    entry = pretool[0]
    assert entry["matcher"] == "Bash|PowerShell"
    hooks = entry["hooks"]
    assert len(hooks) == 2
    by_shell = {hook.get("if"): hook for hook in hooks}
    assert set(by_shell) == {"Bash(*)", "PowerShell(*)"}
    assert all(hook["type"] == "command" for hook in hooks)
    bash_command = by_shell["Bash(*)"]["command"]
    assert "command -v python3" in bash_command and "else python " in bash_command
    assert "${CLAUDE_PROJECT_DIR}/.claude/hooks/deny_main_commit.py" in bash_command
    assert by_shell["Bash(*)"].get("args", []) == []
    assert by_shell["PowerShell(*)"]["command"] == "python"
    assert by_shell["PowerShell(*)"]["args"] == ["${CLAUDE_PROJECT_DIR}/.claude/hooks/deny_main_commit.py"]
    assert all(hook["onFailure"] == "block" for hook in hooks)
    assert all(1 <= hook["timeout"] <= 15 for hook in hooks)


@pytest.mark.parametrize("branch", ["main", "master"])
def test_commit_on_main_or_master_is_denied(branch, tmp_path):
    repo = _repo(tmp_path / "canonical", branch=branch)
    _assert_denied(_invoke("git commit -m synthetic", repo))


def test_commit_on_feature_branch_is_left_to_normal_permissions(tmp_path):
    repo = _repo(tmp_path / "canonical")
    _git(repo, "switch", "-c", "feat/synthetic")
    assert _decision(_invoke("git commit -m synthetic", repo)) is None


@pytest.mark.parametrize("origin", [
    "https://github.com/SomeoneElse/open-cam-framework.git",
    "git@github.com:someoneelse/open-cam-framework.git",
    "https://example.invalid/a-different-repository.git",
])
def test_commit_in_other_repository_or_fork_is_unaffected(origin, tmp_path):
    repo = _repo(tmp_path / "other", origin=origin)
    assert _decision(_invoke("git commit -m synthetic", repo)) is None


def test_commit_in_repository_without_origin_asks_instead_of_allowing(tmp_path):
    repo = _repo(tmp_path / "unknown", origin=None)
    _assert_asks(_invoke("git commit -m synthetic", repo))


def test_commit_from_detached_head_asks_instead_of_allowing(tmp_path):
    repo = _repo(tmp_path / "canonical")
    _git(repo, "checkout", "--detach")
    _assert_asks(_invoke("git commit -m synthetic", repo))


@pytest.mark.parametrize(("tool", "command"), [
    ("Bash", 'echo "git commit -m not-a-command"'),
    ("PowerShell", 'Write-Output "git commit -m not-a-command"'),
])
def test_quoted_mentions_of_git_commit_do_not_trigger(tool, command, tmp_path):
    repo = _repo(tmp_path / "canonical")
    assert _decision(_invoke(command, repo, tool_name=tool)) is None


def test_bash_heredoc_body_mentioning_git_commit_does_not_trigger(tmp_path):
    repo = _repo(tmp_path / "canonical")
    command = "cat <<'EOF'\ngit commit -m not-a-command\nEOF\n"
    assert _decision(_invoke(command, repo)) is None


def test_powershell_here_string_mentioning_git_commit_does_not_trigger(tmp_path):
    repo = _repo(tmp_path / "canonical")
    command = "Write-Output @'\ngit commit -m not-a-command\n'@\n"
    assert _decision(_invoke(command, repo, tool_name="PowerShell")) is None


def test_nested_bash_c_commit_is_still_denied(tmp_path):
    repo = _repo(tmp_path / "canonical")
    _assert_denied(_invoke('bash -c "git commit -m synthetic"', repo))


def test_cd_then_commit_and_set_location_then_commit_are_resolved(tmp_path):
    repo = _repo(tmp_path / "canonical")
    parent = tmp_path / "outside"
    parent.mkdir()
    _assert_denied(_invoke(f"cd {_bash_path(repo)} && git commit -m synthetic", parent))
    _assert_denied(_invoke(f"Set-Location {repo}; git commit -m synthetic", parent, tool_name="PowerShell"))


def test_git_c_target_is_resolved_even_when_session_cwd_is_elsewhere(tmp_path):
    repo = _repo(tmp_path / "canonical")
    outside = tmp_path / "outside"
    outside.mkdir()
    _assert_denied(_invoke(f"git -C {_bash_path(repo)} commit -m synthetic", outside))


def test_linked_worktree_checked_out_on_main_is_denied(tmp_path):
    repo = _repo(tmp_path / "canonical")
    worktree = tmp_path / "shared-main-worktree"
    _git(repo, "worktree", "add", "--force", str(worktree), "main")
    _assert_denied(_invoke("git commit -m synthetic", worktree))


def test_hook_errors_return_a_deny_decision_instead_of_failing_open(tmp_path):
    result = subprocess.run(
        [sys.executable, str(HOOK)], cwd=str(tmp_path), input="{malformed",
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    decision = _decision(result)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "error" in decision["hookSpecificOutput"]["permissionDecisionReason"].casefold()
