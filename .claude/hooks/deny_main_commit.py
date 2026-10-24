"""Offline PreToolUse guard against commits on main/master in the canonical OpenCAM repo.

This hook inspects the command as data. It never executes, evaluates, or contacts a remote
using the supplied shell command. Git is invoked only with fixed argument vectors to inspect
the local repository root, origin URL, and checked-out branch.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

_CANONICAL_REPO = "shamikm88/open-cam-framework"
_GIT_TIMEOUT_SECONDS = 2
_MAX_NESTED_SHELLS = 5


def _git_env():
    """Remove Git's repository/index override variables before inspecting the named checkout."""
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}


def _run_git(path, *args):
    try:
        return subprocess.run(  # noqa: S603, S607 -- fixed argv; local Git metadata only, no shell/network
            ["git", "-C", str(path), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_git_env(),
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("local Git inspection failed") from exc


def _canonical_origin(origin):
    """Accept the canonical GitHub HTTPS/SSH URL forms, not similarly named forks."""
    raw = origin.strip()
    if not raw:
        return False
    scp = re.fullmatch(r"(?:[^@/:]+@)?github\.com:(?P<path>[^?#]+)", raw, flags=re.IGNORECASE)
    if scp:
        path = scp.group("path")
    else:
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return False
        if parsed.scheme.casefold() not in {"https", "ssh"} or (parsed.hostname or "").casefold() != "github.com":
            return False
        path = parsed.path
    path = path.strip("/")
    if path.casefold().endswith(".git"):
        path = path[:-4]
    return path.casefold() == _CANONICAL_REPO


def _unquote(token):
    """Remove one pair of matching outer quotes from a PowerShell token."""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        quote = token[0]
        token = token[1:-1]
        if quote == "'":
            token = token.replace("''", "'")
        elif quote == '"':
            token = token.replace('""', '"')
    return token


def _heredoc_markers(line):
    """Find Bash << delimiters outside quoted strings."""
    found = []
    quote = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\":
                escaped = True
            elif char == '"':
                quote = None
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if line.startswith("<<<", index):
            index += 3
            continue
        if line.startswith("<<", index):
            cursor = index + 2
            strip_tabs = cursor < len(line) and line[cursor] == "-"
            if strip_tabs:
                cursor += 1
            while cursor < len(line) and line[cursor] in " \t":
                cursor += 1
            if cursor < len(line) and line[cursor] in {"'", '"'}:
                delimiter_quote = line[cursor]
                cursor += 1
                end = line.find(delimiter_quote, cursor)
                if end < 0:
                    index += 2
                    continue
                delimiter = line[cursor:end]
                cursor = end + 1
            else:
                start = cursor
                while cursor < len(line) and line[cursor] not in " \t\r\n;&|":
                    cursor += 1
                delimiter = line[start:cursor]
            if delimiter:
                found.append((delimiter, strip_tabs))
            index = max(cursor, index + 2)
            continue
        index += 1
    return found


def _strip_bash_heredocs(command):
    """Remove here-document bodies before examining executable shell syntax."""
    lines = command.splitlines(keepends=True)
    output = []
    index = 0
    while index < len(lines):
        line = lines[index]
        output.append(line)
        markers = _heredoc_markers(line)
        index += 1
        for delimiter, strip_tabs in markers:
            while index < len(lines):
                candidate = lines[index].rstrip("\r\n")
                closing = candidate.lstrip("\t") if strip_tabs else candidate
                if closing == delimiter:
                    index += 1
                    break
                index += 1
            output.append("\n")
    return "".join(output)


def _powershell_here_start(line):
    """Return the start and quote of an unquoted PowerShell here-string opener on a line."""
    quote = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote is not None:
            if quote == "'" and line.startswith("''", index):
                index += 2
                continue
            if quote == '"' and ord(char) == 96:
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if line.startswith("@'", index) or line.startswith('@"', index):
            quote_char = line[index + 1]
            if line[index + 2:].strip() == "":
                return index, quote_char
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
        elif ord(char) == 96:
            index += 2
            continue
        index += 1
    return None


def _strip_powershell_here_strings(command):
    lines = command.splitlines(keepends=True)
    output = []
    index = 0
    while index < len(lines):
        line = lines[index]
        opener = _powershell_here_start(line.rstrip("\r\n"))
        if opener is None:
            output.append(line)
            index += 1
            continue
        start, quote = opener
        output.append(line[:start] + "\n")
        closing = quote + "@"
        index += 1
        while index < len(lines) and lines[index].strip() != closing:
            index += 1
        if index < len(lines):
            index += 1
        output.append("\n")
    return "".join(output)


def _split_commands(command, shell):
    """Split at unquoted shell control operators, preserving quoted mentions as arguments."""
    segments = []
    current = []
    quote = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if quote is not None:
            current.append(char)
            if shell == "PowerShell" and quote == "'" and command.startswith("''", index):
                current.append("'")
                index += 2
                continue
            if shell == "PowerShell" and quote == '"' and ord(char) == 96:
                escaped = True
                index += 1
                continue
            if shell == "Bash" and quote == '"' and char == "\\":
                escaped = True
                index += 1
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if shell == "PowerShell" and ord(char) == 96:
            current.append(char)
            escaped = True
            index += 1
            continue
        if shell == "Bash" and char == "\\":
            current.append(char)
            escaped = True
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue
        if char == "#" and (not current or current[-1].isspace()):
            while index < len(command) and command[index] != "\n":
                index += 1
            continue
        if char in {";", "&", "|", "(", ")", "\n", "!"}:
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            while index + 1 < len(command) and command[index + 1] == char and char in {"&", "|"}:
                index += 1
            index += 1
            continue
        current.append(char)
        index += 1

    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    if quote is not None:
        raise ValueError("unclosed quote in shell command")
    return segments


def _tokens(segment, shell):
    tokens = shlex.split(segment, comments=True, posix=(shell != "PowerShell"))
    if shell == "PowerShell":
        tokens = [_unquote(token) for token in tokens]
    return tokens


def _path_from_argument(argument, current):
    argument = _unquote(argument)
    expanded = os.path.expanduser(os.path.expandvars(argument))
    if not expanded or any(marker in expanded for marker in ("$env:", "${", "%")):
        return None
    target = Path(expanded)
    if not target.is_absolute():
        if current is None:
            return None
        target = current / target
    return target.resolve(strict=False)


def _change_directory(tokens, current, shell):
    if not tokens:
        return False, current
    name = Path(tokens[0]).name.casefold()
    if name not in {"cd", "set-location", "sl", "pushd"}:
        return False, current
    args = tokens[1:]
    if not args:
        return True, Path.home()
    if args[0] == "--":
        args = args[1:]
    elif name in {"set-location", "sl"} and args[0].casefold() in {"-path", "-literalpath"}:
        args = args[1:]
    elif shell == "PowerShell" and args[0].startswith("-"):
        return True, None
    if len(args) != 1:
        return True, None
    return True, _path_from_argument(args[0], current)


def _nested_shell(tokens, shell):
    if not tokens:
        return None
    executable = Path(tokens[0]).name.casefold()
    flags = {"bash": {"-c"}, "sh": {"-c"}, "zsh": {"-c"}, "dash": {"-c"}}
    if executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        flags[executable] = {"-command", "-c"}
    elif executable in {"cmd", "cmd.exe"}:
        flags[executable] = {"/c"}
    allowed = flags.get(executable)
    if not allowed:
        return None
    for index, token in enumerate(tokens[1:], start=1):
        if token.casefold() in allowed and index + 1 < len(tokens):
            nested_shell_type = "PowerShell" if executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"} else "Bash"
            return tokens[index + 1], nested_shell_type
    return None


def _git_commit_target(tokens, current):
    """Return (is_commit, targeted directory) for a direct Git commit command."""
    if not tokens:
        return False, None
    index = 0
    while index < len(tokens) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index]):
        index += 1
    if index >= len(tokens) or Path(tokens[index]).name.casefold() not in {"git", "git.exe"}:
        return False, None
    index += 1
    target = current
    while index < len(tokens):
        token = tokens[index]
        if token == "-C":
            if index + 1 >= len(tokens):
                return True, None
            target = _path_from_argument(tokens[index + 1], current)
            index += 2
            continue
        if token in {"-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return (token.casefold() == "commit"), (target if token.casefold() == "commit" else None)
    return False, None


def _classify_repository(path):
    """Return (decision, reason) for a local commit target; None decision means no restriction."""
    if path is None or not path.is_dir():
        return "ask", "Cannot determine the target repository; confirm the commit target manually."
    top = _run_git(path, "rev-parse", "--show-toplevel")
    if top.returncode:
        return "ask", "Cannot determine the target repository; confirm the commit target manually."
    root = Path(top.stdout.strip()).resolve()
    origin = _run_git(root, "config", "--local", "--get", "remote.origin.url")
    if origin.returncode or not origin.stdout.strip():
        return "ask", "Cannot verify the repository origin; confirm the commit target manually."
    if not _canonical_origin(origin.stdout):
        return None, ""
    branch = _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch.returncode or not branch.stdout.strip():
        return "ask", "Cannot determine the checked-out branch; confirm the commit target manually."
    current_branch = branch.stdout.strip().casefold()
    if current_branch in {"main", "master"}:
        return (
            "deny",
            "Commit blocked on main/master in the OpenCAM repository. Create or switch to a feature branch in a worktree, then commit there.",
        )
    return None, ""


def _inspect_command(command, shell, initial_cwd, depth=0):
    if depth > _MAX_NESTED_SHELLS:
        return "ask", "Nested shell command is too deep to classify safely; confirm the commit target manually."
    command = _strip_powershell_here_strings(command) if shell == "PowerShell" else _strip_bash_heredocs(command)
    current = initial_cwd
    pending_ask = None
    try:
        segments = _split_commands(command, shell)
    except ValueError:
        return "ask", "Cannot parse the shell command confidently; confirm the commit target manually."
    for segment in segments:
        try:
            tokens = _tokens(segment, shell)
        except ValueError:
            pending_ask = pending_ask or (
                "ask",
                "Cannot parse the shell command confidently; confirm the commit target manually.",
            )
            continue
        if not tokens:
            continue
        changed, updated_cwd = _change_directory(tokens, current, shell)
        if changed:
            current = updated_cwd
            continue
        nested = _nested_shell(tokens, shell)
        if nested is not None:
            nested_command, nested_shell_type = nested
            result = _inspect_command(nested_command, nested_shell_type, current, depth + 1)
            if result and result[0] == "deny":
                return result
            if result and result[0] == "ask":
                pending_ask = pending_ask or result
            continue
        is_commit, target = _git_commit_target(tokens, current)
        if not is_commit:
            continue
        result = _classify_repository(target)
        if result and result[0] == "deny":
            return result
        if result and result[0] == "ask":
            pending_ask = pending_ask or result
    return pending_ask


def _emit(decision, reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=True))


def main():
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
        tool_name = payload.get("tool_name")
        if tool_name not in {"Bash", "PowerShell"}:
            return 0
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
            raise ValueError("hook input has no shell command")
        raw_cwd = payload.get("cwd") or os.getcwd()
        if not isinstance(raw_cwd, str):
            raise ValueError("hook input cwd is not text")
        cwd = Path(raw_cwd).expanduser().resolve(strict=False)
        shell = "PowerShell" if tool_name == "PowerShell" else "Bash"
        result = _inspect_command(tool_input["command"], shell, cwd)
        if result is not None:
            _emit(result[0], result[1])
        return 0
    except Exception as exc:
        _emit("deny", f"Commit guard error; blocking the command because the safety check failed ({type(exc).__name__}).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
