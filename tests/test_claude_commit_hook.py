"""Offline contract tests for the project-level Claude Code main-branch guard (#445)."""
import json
import os
import shutil
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
    return str(path).replace("\\", "/")


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
    assert "python3 -c" in bash_command and "else python " in bash_command
    assert "command -v python3" not in bash_command  # finding a python3 is not the same as being able to run it
    assert "${CLAUDE_PROJECT_DIR}/.claude/hooks/deny_main_commit.py" in bash_command
    assert by_shell["Bash(*)"].get("args", []) == []
    assert by_shell["PowerShell(*)"]["command"] == "python"
    assert by_shell["PowerShell(*)"]["args"] == ["${CLAUDE_PROJECT_DIR}/.claude/hooks/deny_main_commit.py"]
    assert all(hook["onFailure"] == "block" for hook in hooks)
    assert all(1 <= hook["timeout"] <= 15 for hook in hooks)


@pytest.mark.skipif(shutil.which("bash") is None, reason="the Bash handler needs bash")
@pytest.mark.parametrize("python3_works", [False, True])
def test_the_bash_handler_runs_whichever_python_can_actually_run(python3_works, tmp_path):
    """A Windows machine can have a `python3` on PATH that is the Microsoft Store stub: `command -v` finds it, then it
    exits with 'Python was not found'. The handler must fall back to `python` instead of failing, because a failing
    handler blocks every Bash command (`onFailure: "block"`)."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    python = Path(sys.executable).as_posix()
    (stubs / "python").write_text(f'#!/bin/sh\nexec "{python}" "$@"\n', encoding="utf-8", newline="\n")
    if python3_works:
        (stubs / "python3").write_text(f'#!/bin/sh\nexec "{python}" "$@"\n', encoding="utf-8", newline="\n")
    else:
        (stubs / "python3").write_text("#!/bin/sh\necho 'Python was not found' >&2\nexit 9\n", encoding="utf-8", newline="\n")
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    command = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    repo = _repo(tmp_path / "canonical", branch="feat/synthetic")
    environment = {**os.environ, "PATH": str(stubs) + os.pathsep + os.environ["PATH"],
                   "CLAUDE_PROJECT_DIR": str(REPO_ROOT)}
    result = subprocess.run(
        ["bash", "-c", command], input=json.dumps(_payload("git status", repo)), env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""  # a harmless command on a feature branch: no decision, nothing blocked


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


# A commit on main must be recognised however the command is dressed up (review findings CG452-1 to CG452-4 and the
# bypasses found beside them). The command text is built from pieces so this file never contains a bare commit line.
_COMMIT = "git " + "commit -m synthetic"


@pytest.mark.parametrize("template", [
    "command {commit}",
    "builtin {commit}",
    "env GIT_OPTIONAL_LOCKS=0 {commit}",
    "env -i {commit}",
    "time {commit}",
    "nohup {commit}",
    "GIT_AUTHOR_NAME=Synthetic {commit}",
    "\\{commit}",
    "if true; then {commit}; fi",
    "for i in 1; do {commit}; done",
    "while true; do {commit}; break; done",
    "{{ {commit}; }}",
    "! {commit}",
    "printf '%s' \"$({commit})\"",
    "echo `{commit}`",
    "echo \"`{commit}`\"",
    "echo $(bash -c '{commit}')",
    "eval '{commit}'",
    "bash -c '{commit}'",
    "git -c alias.zz=commit zz -m synthetic",
])
def test_wrapped_or_nested_commit_on_main_is_denied(template, tmp_path):
    repo = _repo(tmp_path / "canonical")
    _assert_denied(_invoke(template.format(commit=_COMMIT), repo))


@pytest.mark.parametrize("template", [
    "sudo {commit}",
    "exec {commit}",
    "echo synthetic | xargs {commit}",
    "timeout 5 {commit}",
])
def test_commit_behind_an_argument_taking_wrapper_asks_instead_of_allowing(template, tmp_path):
    repo = _repo(tmp_path / "canonical")
    _assert_asks(_invoke(template.format(commit=_COMMIT), repo))


def test_commit_through_a_configured_git_alias_is_denied(tmp_path):
    repo = _repo(tmp_path / "canonical")
    _git(repo, "config", "alias.ci", "commit")
    _git(repo, "config", "alias.save", "ci")
    _assert_denied(_invoke("git ci -m synthetic", repo))
    _assert_denied(_invoke("git save -m synthetic", repo))


def test_shell_alias_cannot_be_read_so_it_asks(tmp_path):
    repo = _repo(tmp_path / "canonical")
    _git(repo, "config", "alias.sh", "!echo synthetic")
    _assert_asks(_invoke("git sh", repo))


def test_alias_that_is_not_a_commit_is_left_alone(tmp_path):
    repo = _repo(tmp_path / "canonical")
    _git(repo, "config", "alias.st", "status")
    assert _decision(_invoke("git st", repo)) is None


@pytest.mark.parametrize("template", [
    "git --git-dir={path}/.git --work-tree={path} commit -m synthetic",
    "git --git-dir {path}/.git --work-tree {path} commit -m synthetic",
    "git --git-dir={path}/.git commit -m synthetic",
    "GIT_DIR={path}/.git GIT_WORK_TREE={path} git commit -m synthetic",
    "GIT_WORK_TREE={path} git commit -m synthetic",
    "export GIT_DIR={path}/.git; git commit -m synthetic",
    "export GIT_WORK_TREE={path} GIT_DIR={path}/.git && git commit -m synthetic",
])
def test_repository_selectors_pointing_at_main_are_followed_from_a_fork(template, tmp_path):
    canonical = _repo(tmp_path / "canonical")
    fork = _repo(tmp_path / "fork", origin="https://github.com/someoneelse/open-cam-framework.git")
    _assert_denied(_invoke(template.format(path=_bash_path(canonical)), fork))


def test_powershell_environment_selectors_pointing_at_main_are_followed(tmp_path):
    canonical = _repo(tmp_path / "canonical")
    fork = _repo(tmp_path / "fork", origin="https://github.com/someoneelse/open-cam-framework.git")
    path = _bash_path(canonical)
    command = f"$env:GIT_DIR='{path}/.git'; $env:GIT_WORK_TREE='{path}'; {_COMMIT}"
    _assert_denied(_invoke(command, fork, tool_name="PowerShell"))


def test_repository_selector_that_cannot_be_resolved_asks(tmp_path):
    fork = _repo(tmp_path / "fork", origin="https://github.com/someoneelse/open-cam-framework.git")
    _assert_asks(_invoke("GIT_DIR=/elsewhere/not-dot-git " + _COMMIT, fork))


@pytest.mark.parametrize(("tool", "template"), [
    ("PowerShell", "& {commit}"),
    ("PowerShell", "git.exe commit -m synthetic"),
    ("PowerShell", "Write-Output \"$({commit})\""),
    ("PowerShell", "iex '{commit}'"),
])
def test_powershell_forms_of_a_commit_on_main_are_denied(tool, template, tmp_path):
    repo = _repo(tmp_path / "canonical")
    _assert_denied(_invoke(template.format(commit=_COMMIT), repo, tool_name=tool))


@pytest.mark.parametrize("command", [
    "echo '$(" + _COMMIT + ")'",
    "echo 'text with `" + _COMMIT + "` inside single quotes'",
    "git log --grep=commit",
    "git commit-tree HEAD^{tree} -m synthetic",
    "git status",
    "git show HEAD",
])
def test_commands_that_only_mention_or_resemble_a_commit_are_not_blocked(command, tmp_path):
    repo = _repo(tmp_path / "canonical")
    assert _decision(_invoke(command, repo)) is None


def test_commit_in_a_fork_through_alias_or_selector_defaults_stays_unaffected(tmp_path):
    fork = _repo(tmp_path / "fork", origin="https://github.com/someoneelse/open-cam-framework.git")
    _git(fork, "config", "alias.ci", "commit")
    assert _decision(_invoke("git ci -m synthetic", fork)) is None
    assert _decision(_invoke("command " + _COMMIT, fork)) is None


# Second-round review findings (CG452-5, CG452-6 and the siblings found while reproducing them).
@pytest.mark.parametrize("template", [
    "Start-Process -FilePath git -ArgumentList 'commit -m synthetic' -Wait",
    "Start-Process git -ArgumentList 'commit','-m','synthetic' -NoNewWindow -Wait",
    "Start-Process -FilePath git.exe -ArgumentList \"commit -m synthetic\"",
    "saps git 'commit -m synthetic'",
    "Start-Process -FilePath powershell -ArgumentList '-Command', 'git commit -m synthetic'",
])
def test_powershell_start_process_launching_a_commit_on_main_is_denied(template, tmp_path):
    repo = _repo(tmp_path / "canonical")
    _assert_denied(_invoke(template, repo, tool_name="PowerShell"))


def test_start_process_working_directory_selects_the_checkout(tmp_path):
    canonical = _repo(tmp_path / "canonical")
    fork = _repo(tmp_path / "fork", origin="https://github.com/someoneelse/open-cam-framework.git")
    command = f"Start-Process -FilePath git -ArgumentList 'commit -m synthetic' -WorkingDirectory '{_bash_path(canonical)}'"
    _assert_denied(_invoke(command, fork, tool_name="PowerShell"))
    assert _decision(_invoke("Start-Process -FilePath git -ArgumentList 'commit -m synthetic'", fork,
                             tool_name="PowerShell")) is None


def test_start_process_of_an_unreadable_program_asks_and_of_other_programs_is_left_alone(tmp_path):
    repo = _repo(tmp_path / "canonical")
    _assert_asks(_invoke("Start-Process -FilePath $program -ArgumentList 'commit -m synthetic'", repo,
                         tool_name="PowerShell"))
    assert _decision(_invoke("Start-Process notepad.exe -ArgumentList 'notes.txt'", repo, tool_name="PowerShell")) is None


def test_relative_c_options_chain_from_each_other_like_git(tmp_path):
    root = tmp_path
    canonical = _repo(root / "canonical")
    nested = root / "nested"
    nested.mkdir()
    outside = root / "outside"
    outside.mkdir()
    _assert_denied(_invoke("git -C nested -C ../canonical commit -m synthetic", root))
    _assert_denied(_invoke(f"git -C {_bash_path(canonical)}/.. --git-dir=canonical/.git --work-tree=canonical commit -m synthetic",
                           outside))


def test_chained_relative_c_cannot_be_mistaken_for_a_non_canonical_checkout(tmp_path):
    """The second -C is relative to the first; resolving it against the session directory finds a different checkout."""
    fork = _repo(tmp_path / "fork", origin="https://github.com/someoneelse/open-cam-framework.git")
    _repo(fork / "deep" / "canonical")
    (fork / "deep" / "zzz").mkdir()
    # Resolved against the session directory this would be tmp/canonical, which is an unrelated fork checkout.
    _repo(tmp_path / "canonical", origin="https://github.com/someoneelse/open-cam-framework.git")
    _assert_denied(_invoke("git -C deep/zzz -C ../canonical commit -m synthetic", fork))


@pytest.mark.parametrize(("tool", "template"), [
    ("PowerShell", "cmd /c {commit}"),
    ("PowerShell", "cmd.exe /c {commit}"),
    ("PowerShell", "powershell -NoProfile -Command {commit}"),
    ("PowerShell", "pwsh -c {commit}"),
    ("PowerShell", "cmd /c \"{commit}\""),
])
def test_unquoted_arguments_after_a_shell_command_flag_are_part_of_the_command(tool, template, tmp_path):
    repo = _repo(tmp_path / "canonical")
    _assert_denied(_invoke(template.format(commit=_COMMIT), repo, tool_name=tool))


@pytest.mark.parametrize(("tool", "command"), [
    ("Bash", "g=git; $g commit -m synthetic"),
    ("Bash", "g=git; \"$g\" commit -m synthetic"),
    ("Bash", "${GIT:-git} commit -m synthetic"),
    ("Bash", "$(echo git) commit -m synthetic"),
    ("Bash", "cmd=\"" + _COMMIT + "\"; $cmd"),
    ("PowerShell", "$g='git'; & $g commit -m synthetic"),
    ("PowerShell", "Invoke-Expression ('gi'+'t commit -m synthetic')"),
    ("Bash", "eval \"$(echo git) commit -m synthetic\""),
])
def test_a_command_built_at_run_time_that_mentions_commit_asks(tool, command, tmp_path):
    repo = _repo(tmp_path / "canonical")
    _assert_asks(_invoke(command, repo, tool_name=tool))


@pytest.mark.parametrize("command", [
    "$PY -m pytest -q",
    "$EDITOR notes.txt",
    "echo \"$(date)\"",
    "x=$(pwd); echo $x",
])
def test_run_time_command_words_that_do_not_mention_commit_are_not_questioned(command, tmp_path):
    repo = _repo(tmp_path / "canonical")
    assert _decision(_invoke(command, repo)) is None


# Third-round review finding PX452-1: script blocks, function bodies and same-line PowerShell aliases.
@pytest.mark.parametrize(("tool", "template"), [
    ("PowerShell", "function f {{ {commit} }}; f"),
    ("PowerShell", "1 | ForEach-Object {{ {commit} }}"),
    ("PowerShell", "Invoke-Command -ScriptBlock {{ {commit} }}"),
    ("PowerShell", "Start-Job {{ {commit} }}"),
    ("PowerShell", "$sb = {{ {commit} }}; & $sb"),
    ("Bash", "function f {{ {commit}; }}; f"),
    ("Bash", "f() {{ {commit}; }}; f"),
])
def test_commit_inside_a_function_body_or_script_block_on_main_is_denied(tool, template, tmp_path):
    repo = _repo(tmp_path / "canonical")
    _assert_denied(_invoke(template.format(commit=_COMMIT), repo, tool_name=tool))


@pytest.mark.parametrize("command", [
    "Set-Alias g git; g commit -m synthetic",
    "sal g git; g commit -m synthetic",
    "New-Alias -Name g -Value git; g commit -m synthetic",
])
def test_a_powershell_alias_defined_on_the_same_line_is_resolved(command, tmp_path):
    repo = _repo(tmp_path / "canonical")
    _assert_denied(_invoke(command, repo, tool_name="PowerShell"))


@pytest.mark.parametrize(("tool", "command"), [
    ("PowerShell", "Get-ChildItem | ForEach-Object { $_.Name }"),
    ("PowerShell", "function f { Write-Output synthetic }; f"),
    ("PowerShell", "Set-Alias ll Get-ChildItem; ll"),
    ("Bash", "echo ${HOME}"),
    ("Bash", "git log --format='{%H}' -1"),
    ("Bash", "f() { echo synthetic; }; f"),
])
def test_blocks_and_aliases_that_do_not_run_a_commit_are_not_blocked(tool, command, tmp_path):
    repo = _repo(tmp_path / "canonical")
    assert _decision(_invoke(command, repo, tool_name=tool)) is None
