"""Deterministic advisory report comparing changed paths to the PR's declared file scope."""
import argparse
import re
import sys
import unicodedata
from functools import cache
from pathlib import Path

SCOPE_HEADING = re.compile(r"^##\s+Declared file scope\s*$")
NEXT_SECTION = re.compile(r"^##(?!#)\s+")
SCOPE_BULLET = re.compile(r"^-\s+\x60([^\x60\r\n]+)\x60$")
DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
PROTECTED_PATTERNS = (
    "agents/*.md",
    ".github/workflows/**",
    "pyproject.toml",
    "config/**",
    "badges/test-count.json",
    ".claude/**",
    "diff_cover_critical.toml",
)
BROAD_PATTERNS = {"*", "**", "**/*"}
CATEGORY_ORDER = (
    "IN_SCOPE",
    "OUT_OF_SCOPE",
    "PROTECTED_DECLARED",
    "PROTECTED_UNDECLARED",
)
MAX_PATH_ROWS = 100


class ScopeInputError(ValueError):
    """An input is malformed or contains an unsafe repository path."""


def _contains_control(value):
    return any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value)


def _component_regex(component):
    """Compile one POSIX glob component; a slash is never matched here."""
    pieces = []
    index = 0
    while index < len(component):
        char = component[index]
        if char == "*":
            pieces.append("[^/]*")
            index += 1
        elif char == "?":
            pieces.append("[^/]")
            index += 1
        elif char == "[":
            close = component.find("]", index + 1)
            if close < 0:
                raise ScopeInputError("malformed scope pattern")
            raw_class = component[index + 1:close]
            if not raw_class or "[" in raw_class:
                raise ScopeInputError("malformed scope pattern")
            negated = raw_class[0] in {"!", "^"}
            class_chars = raw_class[1:] if negated else raw_class
            if not class_chars:
                raise ScopeInputError("malformed scope pattern")
            if class_chars.startswith("^"):
                class_chars = r"\^" + class_chars[1:]
            class_text = ("^" if negated else "") + class_chars
            try:
                re.compile("[" + class_text + "]")
            except re.error as exc:
                raise ScopeInputError("malformed scope pattern") from exc
            pieces.append("[" + class_text + "]")
            index = close + 1
        else:
            pieces.append(re.escape(char))
            index += 1
    return re.compile("".join(pieces))


def normalize_path(value, pattern=False):
    """Normalize a repo-relative path/glob identically on Windows and POSIX."""
    if not isinstance(value, str) or not value or _contains_control(value):
        raise ScopeInputError("unsafe path input")
    raw = value.replace("\\", "/")
    if raw.startswith("/") or DRIVE_PREFIX.match(raw):
        raise ScopeInputError("unsafe path input")
    trailing_slash = raw.endswith("/")
    if trailing_slash and not pattern:
        raise ScopeInputError("changed-file list contains a directory path")

    components = []
    for component in raw.split("/"):
        if component in {"", "."}:
            continue
        if component == "..":
            raise ScopeInputError("unsafe path input")
        components.append(component)

    if pattern and trailing_slash:
        components.extend(("**", "*"))
    if not components:
        raise ScopeInputError("empty path input")

    if pattern:
        for component in components:
            if "**" in component and component != "**":
                raise ScopeInputError("malformed scope pattern")
            if component != "**":
                _component_regex(component)
    return "/".join(components)


def _matches_normalized(path, pattern):
    path_parts = tuple(path.split("/"))
    pattern_parts = tuple(pattern.split("/"))
    component_regexes = tuple(None if part == "**" else _component_regex(part) for part in pattern_parts)

    @cache
    def visit(path_index, pattern_index):
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        if pattern_parts[pattern_index] == "**":
            if visit(path_index, pattern_index + 1):
                return True
            return path_index < len(path_parts) and visit(path_index + 1, pattern_index)
        if path_index == len(path_parts):
            return False
        matcher = component_regexes[pattern_index]
        return bool(matcher.fullmatch(path_parts[path_index])) and visit(path_index + 1, pattern_index + 1)

    return visit(0, 0)


def matches_glob(path, pattern):
    """Match case-sensitively using POSIX, repo-relative glob semantics."""
    normalized_path = normalize_path(path)
    normalized_pattern = normalize_path(pattern, pattern=True)
    return _matches_normalized(normalized_path, normalized_pattern)


def _scope_section_lines(body):
    """Return the one declared-scope section's lines, or None when the section is absent."""
    lines = body.splitlines()
    headings = []
    in_comment = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped[4:]:
                in_comment = True
            continue
        if SCOPE_HEADING.fullmatch(stripped):
            headings.append(index)
    if len(headings) > 1:
        raise ScopeInputError("malformed declared file scope")
    if not headings:
        return None

    section = []
    in_comment = False
    for line in lines[headings[0] + 1:]:
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                after = stripped.split("-->", 1)[1]
                in_comment = False
                if after.strip():
                    raise ScopeInputError("malformed declared file scope")
            continue
        if NEXT_SECTION.match(stripped):
            break
        if not stripped:
            continue
        if stripped.startswith("<!--"):
            tail = stripped[4:]
            if "-->" in tail:
                after = tail.split("-->", 1)[1]
                if after.strip():
                    raise ScopeInputError("malformed declared file scope")
            else:
                in_comment = True
            continue
        if "<!--" in stripped or "-->" in stripped:
            raise ScopeInputError("malformed declared file scope")
        section.append(stripped)
    if in_comment:
        raise ScopeInputError("malformed declared file scope")
    return section


def parse_scope_body(body):
    """Read declared path patterns. None means the scope is absent or has no patterns."""
    if not isinstance(body, str) or _contains_control(body.replace("\n", "").replace("\r", "").replace("\t", "")):
        raise ScopeInputError("malformed declared file scope")
    section = _scope_section_lines(body)
    if section is None:
        return None
    patterns = []
    for line in section:
        match = SCOPE_BULLET.fullmatch(line)
        if match is None:
            raise ScopeInputError("malformed declared file scope")
        patterns.append(normalize_path(match.group(1), pattern=True))
    return patterns or None


def read_changed_files(path):
    """Read Git's UTF-8 NUL-delimited --name-only output; an empty file is valid."""
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScopeInputError("changed-file list is not UTF-8") from exc
    if not text:
        return []
    if not text.endswith("\0"):
        raise ScopeInputError("malformed changed-file list")
    raw_paths = text[:-1].split("\0")
    paths = [normalize_path(path) for path in raw_paths]
    if len(set(paths)) != len(paths):
        raise ScopeInputError("duplicate changed-file path")
    return paths


def _escape_path(path):
    """Keep a displayed path as inert Markdown text, never a link or HTML."""
    escaped = []
    for char in path:
        if char == "&":
            escaped.append("&amp;")
        elif char == "<":
            escaped.append("&lt;")
        elif char == ">":
            escaped.append("&gt;")
        elif char == chr(96) or char in "\\*_{}[]()!#|:+~":
            escaped.append("\\" + char)
        else:
            escaped.append(char)
    return "".join(escaped)


def build_report(changed_files, patterns):
    """Classify every changed file once and return the stable advisory Markdown report."""
    paths = [normalize_path(path) for path in changed_files]
    if len(set(paths)) != len(paths):
        raise ScopeInputError("duplicate changed-file path")
    normalized_patterns = None if not patterns else [
        normalize_path(pattern, pattern=True) for pattern in patterns
    ]
    active_patterns = normalized_patterns or []
    protected_patterns = [normalize_path(pattern, pattern=True) for pattern in PROTECTED_PATTERNS]
    grouped = {category: [] for category in CATEGORY_ORDER}

    for path in paths:
        in_scope = any(_matches_normalized(path, pattern) for pattern in active_patterns)
        protected = any(_matches_normalized(path, pattern) for pattern in protected_patterns)
        if protected:
            category = "PROTECTED_DECLARED" if in_scope else "PROTECTED_UNDECLARED"
        else:
            category = "IN_SCOPE" if in_scope else "OUT_OF_SCOPE"
        grouped[category].append(path)

    for paths_in_category in grouped.values():
        paths_in_category.sort()

    broad = [pattern for pattern in active_patterns if pattern in BROAD_PATTERNS]
    report = [
        "PR scope check (advisory; not a required check)",
        f"Changed files: {len(paths)}",
        f"Scope patterns: {len(active_patterns)}",
    ]
    if not active_patterns:
        report.append("SCOPE NOT DECLARED")
    report.extend((
        f"IN_SCOPE ordinary: {len(grouped['IN_SCOPE'])}",
        f"OUT_OF_SCOPE ordinary: {len(grouped['OUT_OF_SCOPE'])}",
        f"PROTECTED_DECLARED: {len(grouped['PROTECTED_DECLARED'])}",
        f"PROTECTED_UNDECLARED: {len(grouped['PROTECTED_UNDECLARED'])}",
        f"BROAD_SCOPE: {len(broad)}",
    ))
    report.extend(f"BROAD_SCOPE {_escape_path(pattern)}" for pattern in broad)
    report.append("")

    rows = [
        (category, path)
        for category in CATEGORY_ORDER
        for path in grouped[category]
    ]
    for category, path in rows[:MAX_PATH_ROWS]:
        report.append(f"{category} {_escape_path(path)}")
    omitted = len(rows) - MAX_PATH_ROWS
    if omitted > 0:
        report.append(f"{omitted} more paths omitted")
    return "\n".join(report) + "\n"


def _read_scope_body(path):
    with Path(path).open("r", encoding="utf-8") as source:
        return source.read()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare changed repository paths with a PR's declared file scope (advisory only)."
    )
    parser.add_argument("--changed-files", required=True, metavar="PATH",
                        help="UTF-8 NUL-delimited file list from git diff --name-only -z")
    parser.add_argument("--scope-body", required=True, metavar="PATH",
                        help="UTF-8 Markdown PR body extracted from the GitHub event JSON")
    parser.add_argument("--summary-file", metavar="PATH",
                        help="append the same report to a Markdown summary file")
    args = parser.parse_args(argv)

    try:
        changed_files = read_changed_files(args.changed_files)
        patterns = parse_scope_body(_read_scope_body(args.scope_body))
        report = build_report(changed_files, patterns)
        if args.summary_file:
            summary = Path(args.summary_file)
            with summary.open("a", encoding="utf-8") as destination:
                if summary.exists() and summary.stat().st_size > 0:
                    # Job summaries may already contain text from earlier steps; keep the report on a new line.
                    destination.write("\n")
                destination.write(report)
        print(report, end="")
        return 0
    except (OSError, UnicodeError, ScopeInputError) as exc:
        # Deliberately omit raw body, path and pattern text from diagnostics.
        print(f"error: invalid input or unable to write report ({type(exc).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    raise SystemExit(main())
