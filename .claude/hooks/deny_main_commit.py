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

_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)
_GIT_SELECTORS = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"}
_POWERSHELL_ENV = re.compile(r"\$env:(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.+)", re.IGNORECASE | re.DOTALL)
_GIT_NAMES = {"git", "git.exe"}
_SHELL_NAMES = {"bash", "sh", "zsh", "dash", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "cmd", "cmd.exe"}
# Words that only change how the next command runs and take no argument of their own.
_KEYWORDS = {"if", "then", "else", "elif", "while", "until", "do", "!", "{", "}", "command", "builtin", "time", "nohup", "coproc"}
# Wrappers whose own arguments vary too much to parse: the command they run is found by looking for the first git
# or shell word, and a denial reached that way is downgraded to a question.
_WRAPPERS = {
    "sudo", "doas", "xargs", "nice", "timeout", "stdbuf", "setsid", "ionice", "exec", "find", "parallel",
    "su", "runuser", "chroot", "flock",
}


def _git_env():
    """Remove Git's repository/index override variables before inspecting the named checkout."""
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}


def _run_git(path, *args):
    try:
        return subprocess.run(  # noqa: S603 -- fixed argv with shell=False; local Git metadata only
            ["git", "-C", str(path), *args],  # noqa: S607 -- fixed executable, no shell/network
            stdin=subprocess.DEVNULL,
            capture_output=True,
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
    if executable == "eval" or (shell == "PowerShell" and executable in {"iex", "invoke-expression"}):
        return (" ".join(tokens[1:]), shell) if len(tokens) > 1 else None
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


def _strip_prefixes(tokens):
    """Drop leading control keywords, wrappers and variable assignments in front of the real command.

    Returns (remaining tokens, assignments, heuristic). `heuristic` is True when a wrapper such as sudo or xargs was
    skipped by searching for the first git or shell word, so what follows is a best guess and a denial reached from
    it becomes a question for the person instead.
    """
    assignments = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        name = Path(token).name.casefold()
        if _ASSIGNMENT.fullmatch(token):
            assignments.append(token)
            index += 1
        elif token in _KEYWORDS or name in _KEYWORDS:
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
        elif name == "env":
            index += 1
            while index < len(tokens) and (tokens[index].startswith("-") or _ASSIGNMENT.fullmatch(tokens[index])):
                if _ASSIGNMENT.fullmatch(tokens[index]):
                    assignments.append(tokens[index])
                elif tokens[index] in {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"}:
                    index += 1
                index += 1
        elif name in _WRAPPERS:
            for later in range(index + 1, len(tokens)):
                word = Path(tokens[later]).name.casefold()
                if word in _GIT_NAMES or word in _SHELL_NAMES or word == "eval":
                    return tokens[later:], assignments, True
            return [], assignments, True
        else:
            break
    return tokens[index:], assignments, False


def _assignment_pairs(assignments):
    pairs = {}
    for item in assignments:
        name, _, value = item.partition("=")
        pairs[name] = value
    return pairs


def _environment_target(variables, current):
    """The directory GIT_WORK_TREE / GIT_DIR select, or None when it cannot be told."""
    values = {name.upper(): value for name, value in variables.items()}
    if values.get("GIT_WORK_TREE"):
        return _path_from_argument(values["GIT_WORK_TREE"], current)
    git_dir = values.get("GIT_DIR")
    if git_dir:
        resolved = _path_from_argument(git_dir, current)
        if resolved is not None and resolved.name == ".git":
            return resolved.parent
    return None


def _alias_is_commit(subcommand, aliases, target):
    """Does this Git subcommand name expand to `commit`? True or False; None for a shell alias, which can do anything.

    Aliases are read from the command line (`-c alias.x=...`) and from the target repository's Git configuration. They
    are only read, never run.
    """
    name = subcommand.casefold()
    seen = set()
    for _ in range(3):
        if name in seen:
            return False
        seen.add(name)
        value = aliases.get(name)
        if value is None and target is not None and target.is_dir():
            looked = _run_git(target, "config", "--get", f"alias.{name}")
            value = looked.stdout.strip() if looked.returncode == 0 else None
        if not value:
            return False
        value = value.strip()
        if value.startswith("!"):
            return None
        words = value.split()
        if not words:
            return False
        name = words[0].casefold()
        if name == "commit":
            return True
    return False


def _git_commit_target(remaining, assignments, heuristic, current, env=None):
    """Return (is_commit, targeted directory, certain) for a Git command that may create a commit.

    `remaining` is the command with wrappers and assignments already removed. `certain` is False when the answer is a
    best guess (a wrapper was searched, or a shell alias could not be read), so a denial should become a question.
    """
    if not remaining or Path(remaining[0]).name.casefold() not in _GIT_NAMES:
        return False, None, True
    variables = dict(env or {})
    variables.update(_assignment_pairs(assignments))
    args = remaining[1:]
    target = current
    git_dir = work_tree = None
    aliases = {}
    environment_unknown = False
    for name in variables:
        if name.upper() in _GIT_SELECTORS:
            environment_unknown = True
    index = 0
    while index < len(args):
        arg = args[index]
        value = args[index + 1] if index + 1 < len(args) else None
        if arg == "-C":  # noqa: S105 -- Git directory option, not a credential
            if value is None:
                return True, None, False
            target = _path_from_argument(value, current)
            index += 2
        elif arg == "-c":
            if value and value.casefold().startswith("alias.") and "=" in value:
                key, _, body = value.partition("=")
                aliases[key[len("alias."):].casefold()] = body
            index += 2
        elif arg.startswith("--git-dir=") or arg.startswith("--work-tree="):
            option, _, path = arg.partition("=")
            resolved = _path_from_argument(path, current)
            if option == "--git-dir":
                git_dir = (resolved,)
            else:
                work_tree = (resolved,)
            index += 1
        elif arg in {"--git-dir", "--work-tree"}:
            resolved = _path_from_argument(value, current) if value is not None else None
            if arg == "--git-dir":
                git_dir = (resolved,)
            else:
                work_tree = (resolved,)
            index += 2
        elif arg in {"--namespace", "--config-env"}:
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            break
    else:
        return False, None, True
    if work_tree is not None:
        target = work_tree[0]
    elif git_dir is not None:
        target = git_dir[0].parent if git_dir[0] is not None and git_dir[0].name == ".git" else None
    if environment_unknown and work_tree is None and git_dir is None:
        target = _environment_target(variables, current)
    subcommand = args[index]
    if subcommand.casefold() == "commit":
        return True, target, not heuristic
    verdict = _alias_is_commit(subcommand, aliases, target)
    if verdict is False:
        return False, None, True
    return True, target, verdict is True and not heuristic


def _substitutions(segment, shell):
    """The command text inside every $(...) and, in Bash, `...` that the shell would run, quotes respected.

    Text in single quotes is literal and skipped. Text in double quotes is not: $(...) there still runs.
    """
    bodies = []
    quote = None
    index = 0
    length = len(segment)
    while index < length:
        char = segment[index]
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
        elif (char == "\\" and shell == "Bash") or (shell == "PowerShell" and ord(char) == 96):
            index += 2
        elif char == "'" and quote is None:
            quote = "'"
            index += 1
        elif char == '"':
            quote = None if quote == '"' else '"'
            index += 1
        elif char == "$" and segment.startswith("$(", index):
            depth, cursor, inner = 1, index + 2, None
            while cursor < length and depth:
                inner_char = segment[cursor]
                if inner:
                    if inner_char == inner:
                        inner = None
                    elif inner_char == "\\" and inner == '"':
                        cursor += 1
                elif inner_char in {"'", '"'}:
                    inner = inner_char
                elif inner_char == "(":
                    depth += 1
                elif inner_char == ")":
                    depth -= 1
                cursor += 1
            bodies.append(segment[index + 2:cursor - 1] if depth == 0 else segment[index + 2:])
            index = cursor
        elif char == "`" and shell == "Bash":
            end = segment.find("`", index + 1)
            bodies.append(segment[index + 1:end] if end != -1 else segment[index + 1:])
            index = end + 1 if end != -1 else length
        else:
            index += 1
    return bodies


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
    env = {}
    try:
        segments = _split_commands(command, shell)
    except ValueError:
        return "ask", "Cannot parse the shell command confidently; confirm the commit target manually."
    for segment in segments:
        for body in _substitutions(segment, shell):
            result = _inspect_command(body, shell, current, depth + 1)
            if result and result[0] == "deny":
                return result
            if result and result[0] == "ask":
                pending_ask = pending_ask or result
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
        powershell_set = _POWERSHELL_ENV.fullmatch(segment.strip()) if shell == "PowerShell" else None
        if powershell_set:
            env[powershell_set.group("name")] = powershell_set.group("value").strip().strip("'\"")
            continue
        changed, updated_cwd = _change_directory(tokens, current, shell)
        if changed:
            current = updated_cwd
            continue
        remaining, assignments, heuristic = _strip_prefixes(tokens)
        nested = _nested_shell(remaining, shell)
        if nested is not None:
            nested_command, nested_shell_type = nested
            result = _inspect_command(nested_command, nested_shell_type, current, depth + 1)
            if result and result[0] == "deny":
                return result if not heuristic else (
                    "ask", "A wrapped shell command may run `git commit` on main/master; confirm it before it runs.")
            if result and result[0] == "ask":
                pending_ask = pending_ask or result
            continue
        if not remaining:
            env.update(_assignment_pairs(assignments))
            continue
        if Path(remaining[0]).name.casefold() in {"export", "declare", "typeset", "set"}:
            env.update(_assignment_pairs([word for word in remaining[1:] if _ASSIGNMENT.fullmatch(word)]))
            continue
        is_commit, target, certain = _git_commit_target(remaining, assignments, heuristic, current, env)
        if not is_commit:
            continue
        result = _classify_repository(target)
        if result and result[0] == "deny" and not certain:
            result = (
                "ask",
                "This command may run `git commit` on main/master through a wrapper, alias or repository selector "
                "that cannot be fully resolved; confirm it before it runs.",
            )
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
