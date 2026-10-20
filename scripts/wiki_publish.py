"""Generate and publish the GitHub Wiki page tree from the ``docs/`` corpus (issue #192).

The generator reads the allowlisted Markdown pages under ``docs/``, maps them
to wiki page names, rewrites their links to wiki and repository URLs, and
writes the generated tree into an output directory (or validates it in memory
with ``--check``). The output is a pure function of the ``docs/`` tree and
this script -- no timestamps, run ids or environment data.

``--publish`` (PR2) then publishes that tree to the repository's wiki: it
clones the wiki repository (its default branch, whatever it is named), fully
replaces its content with the generated set, makes one commit carrying the
source sha (or none at all when nothing changed) and pushes it back to the
same branch. Generation and validation run before anything is cloned or
written, so a broken corpus pushes nothing. The authentication token is read
only from the ``GITHUB_TOKEN`` environment variable and reaches git only as
an HTTP extra header -- never a URL, a command line shown in logs, an output
line or a file on disk.

Only regular (non-symlink) files matching ``docs/*.md`` and
``docs/examples/synthetic_co/*.md`` are read into the output; linked
repository files are checked for existence and rewritten to URLs, never
copied. No ``anthropic`` dependency.
"""
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class WikiPublishError(Exception):
    """A validation or publishing failure. The message names what failed and
    never echoes a git command line, a token or raw git output."""


# The one allowlist: pages under docs/ that may be read into the output.
PAGE_GLOBS = ("*.md", "examples/synthetic_co/*.md")

NOTICE_TEMPLATE = (
    "> This page is generated from `{source}` in the repository -- `docs/` is "
    "the canonical documentation. Direct wiki edits here are overwritten on "
    "the next publish; make changes in `docs/` by pull request."
)

# A Markdown link: optional image bang, text, destination, optional title.
# Destinations with spaces are not links this corpus uses (same rule as the
# documentation tests) and stay untouched rather than being half-rewritten.
LINK_RE = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\s]+)(\s+\"[^\"]*\")?\)")
EXTERNAL_RE = re.compile(r"^(https?:|mailto:)", re.IGNORECASE)
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
# A fenced code block opens with a run of at least three backticks or tildes;
# a closing fence must use the same character, be at least as long, and be the
# whole line, so a ``` line inside a ```` fence is code text, not a toggle.
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def _fence_delimiter(line: str) -> tuple[str, int] | None:
    """The (character, length) of the fence run starting this line, if any."""
    match = FENCE_RE.match(line)
    if match is None:
        return None
    run = match.group(1)
    return run[0], len(run)


def _lines_with_fences(lines: list[str]):
    """Yield (line number, line, inside a fenced code block) for each line."""
    fence: tuple[str, int] | None = None
    for line_no, line in enumerate(lines, start=1):
        delimiter = _fence_delimiter(line)
        if fence is not None:
            if (
                delimiter is not None
                and delimiter[0] == fence[0]
                and delimiter[1] >= fence[1]
                and line.strip() == delimiter[0] * delimiter[1]
            ):
                fence = None
            yield line_no, line, True
            continue
        if delimiter is not None:
            fence = delimiter
        yield line_no, line, delimiter is not None


def notice_for(source: str) -> str:
    """The generated-source notice every generated page starts with."""
    return NOTICE_TEMPLATE.format(source=source)


def slug(heading_text: str) -> str:
    """GitHub's heading anchor: formatting removed, lower-cased, punctuation
    dropped, spaces to hyphens -- the same rule tests/test_docs.py pins."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading_text)
    text = re.sub(r"[`*_~]", lambda m: "_" if m.group(0) == "_" else "", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def heading_slugs(text: str) -> set[str]:
    """The set of anchors a Markdown file defines, with GitHub's -1, -2
    suffixes for repeated headings. Fenced code blocks are ignored."""
    seen: dict[str, int] = {}
    slugs: set[str] = set()
    for _, line, in_fence in _lines_with_fences(text.split("\n")):
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            base = slug(match.group(1))
            count = seen.get(base, 0)
            seen[base] = count + 1
            slugs.add(base if count == 0 else f"{base}-{count}")
    return slugs


def _read_page(path: Path, source: str) -> str:
    """Read a page strictly as UTF-8; a decode failure names file and line.

    The file is read as bytes and decoded explicitly so the failing line can
    be named (a text-mode open would raise before any line is known).
    """
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        line = data.count(b"\n", 0, error.start) + 1
        raise WikiPublishError(
            f"{source}:{line}: is not valid UTF-8 ({error.reason} at byte {error.start})"
        ) from error


def _page_name(relative: str) -> str:
    """The wiki page name for an allowlisted page's repository-relative path."""
    parts = relative.split("/")
    if relative == "docs/README.md":
        return "Home"
    if parts[0] == "docs" and len(parts) == 2:
        return parts[1][: -len(".md")]
    if parts[:2] != ["docs", "examples"] or parts[2] != "synthetic_co" or len(parts) != 4:
        # Unreachable for discover_pages() output; kept as a guard for direct callers.
        raise WikiPublishError(f"{relative}: not an allowlisted docs/ page")
    return f"examples-synthetic_co-{parts[3][: -len('.md')]}"


def discover_pages(root: Path) -> dict[str, Path]:
    """Map wiki page name -> repository path for every allowlisted page.

    Only regular (non-symlink) files are discovered; a symlinked candidate is
    an error, as is a case-insensitive name collision.
    """
    docs = root / "docs"
    if not docs.is_dir():
        raise WikiPublishError(f"{docs}: no docs/ directory to publish")
    pages: dict[str, Path] = {}
    seen: dict[str, str] = {}
    candidates: list[Path] = []
    for pattern in PAGE_GLOBS:
        # dotfiles (editor or draft artifacts) are not pages
        candidates.extend(path for path in sorted(docs.glob(pattern)) if not path.name.startswith("."))
    for path in sorted(candidates):
        walked = root
        for part in path.relative_to(root).parts[:-1]:
            walked = walked / part
            if walked.is_symlink():
                raise WikiPublishError(
                    f"{walked.relative_to(root).as_posix()}: symlinked directory in the "
                    "publish path is not publishable"
                )
        if path.is_symlink():
            raise WikiPublishError(
                f"{path.relative_to(root).as_posix()}: symlinked pages are not publishable"
            )
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        name = _page_name(relative)
        if name.lower() == "_sidebar":
            raise WikiPublishError(
                f"{relative}: the page name _Sidebar is reserved for the generated sidebar"
            )
        key = name.lower()
        if key in seen:
            raise WikiPublishError(
                f"{relative}: page name collides with {seen[key]} (wiki names are "
                "case-insensitive)"
            )
        seen[key] = relative
        pages[name] = path
    if not pages:
        raise WikiPublishError("docs/: no Markdown pages found to publish")
    return pages


_DRIVE_TARGET_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_absolute_target(text: str) -> bool:
    """Absolute in the repo's POSIX-ish link sense, on every platform.

    ``Path.is_absolute()`` is drive-aware on Windows, so "/etc/passwd" is
    "relative" there; the docs corpus only ever uses slash-rooted or
    drive-letter targets, which are never publishable.
    """
    return text.startswith(("/", "\\")) or bool(_DRIVE_TARGET_RE.match(text))


def _resolve_target(
    file_part: str, page_path: Path, root: Path, line_no: int
) -> tuple[Path, str]:
    """Resolve a relative link destination to (repository path, relative posix
    path), refusing targets that escape the repository or pass through a
    symlink, and requiring the target to exist."""
    source = page_path.relative_to(root).as_posix()
    where = f"{source}:{line_no}"
    if _is_absolute_target(file_part):
        raise WikiPublishError(f"{where}: absolute link target {file_part!r} is not publishable")
    candidate = Path(os.path.normpath(page_path.parent / file_part))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise WikiPublishError(
            f"{where}: link target {file_part!r} resolves outside the repository"
        ) from None
    walked = root
    for part in relative.parts:
        walked = walked / part
        if walked.is_symlink():
            raise WikiPublishError(
                f"{where}: link target {file_part!r} passes through the symlink at "
                f"{walked.relative_to(root).as_posix()}"
            )
    if not walked.exists():
        raise WikiPublishError(f"{where}: link target {file_part!r} does not exist")
    return walked, relative.as_posix()


_BACKTICK_RUN_RE = re.compile(r"`+")
_HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_TABLE_ROW_RE = re.compile(r"^\s{0,3}\|")
_QUOTE_LINE_RE = re.compile(r"^\s{0,3}>")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])(?:\s|$)")
_BLOCK_BREAK_RE = re.compile(r"^\s{0,3}(?:[-*_=\s]{3,})$")


def _code_span_intervals(lines: list[str]) -> dict[int, list[tuple[int, int]]]:
    """The (start, end) character intervals of the inline code spans, per line.

    CommonMark pairs a backtick string with the next backtick string of equal
    length in the same block, and a block's text may soft-wrap across lines:
    a code span can open on one line and close on a later one (finding PX358-3,
    issue #192). The lines are grouped into inline blocks the way the corpus
    uses Markdown: a run of consecutive text lines (a paragraph, or a list
    item with its continuations, with consecutive blockquote lines kept
    together); a heading or table-row line is a block of its own. Fenced code
    carries no inline content and is skipped. Within a block the backtick
    runs are paired left to right, so a shorter run inside a longer span is
    content and a longer run does not close it (CommonMark's rule).
    """
    intervals: dict[int, list[tuple[int, int]]] = {}
    block: list[tuple[int, str]] = []
    last_kind: str | None = None

    def _flush() -> None:
        nonlocal last_kind
        if not block:
            return
        runs: list[tuple[int, int, int]] = []
        for position, (_, line) in enumerate(block):
            for match in _BACKTICK_RUN_RE.finditer(line):
                runs.append((position, match.start(), len(match.group(0))))
        opener_at = 0
        while opener_at < len(runs):
            opener = runs[opener_at]
            for closer_at in range(opener_at + 1, len(runs)):
                if runs[closer_at][2] == opener[2]:
                    _mark_span(opener, runs[closer_at])
                    # Scanning resumes after the closing run: runs inside the
                    # span are content and never open another span.
                    opener_at = closer_at
                    break
            opener_at += 1
        block.clear()
        last_kind = None

    def _mark_span(opener: tuple[int, int, int], closer: tuple[int, int, int]) -> None:
        for position in range(opener[0], closer[0] + 1):
            line_index, line = block[position]
            start = opener[1] if position == opener[0] else 0
            end = closer[1] + closer[2] if position == closer[0] else len(line)
            intervals.setdefault(line_index, []).append((start, end))

    fence: tuple[str, int] | None = None
    for line_index, line in enumerate(lines):
        delimiter = _fence_delimiter(line)
        if fence is not None:
            if (
                delimiter is not None
                and delimiter[0] == fence[0]
                and delimiter[1] >= fence[1]
                and line.strip() == delimiter[0] * delimiter[1]
            ):
                fence = None
            continue  # fenced code has no inline content
        if delimiter is not None:
            _flush()
            fence = delimiter
            continue
        if _BLOCK_BREAK_RE.match(line) or not line.strip():
            _flush()
            continue
        kind = (
            "heading" if _HEADING_LINE_RE.match(line)
            else "table" if _TABLE_ROW_RE.match(line)
            else "quote" if _QUOTE_LINE_RE.match(line)
            else "item" if _LIST_ITEM_RE.match(line)
            else "text"
        )
        if kind in ("heading", "table"):
            # A heading or table row is a block of its own: nothing before it
            # or after it pairs with its backtick runs.
            _flush()
            block.append((line_index, line))
            _flush()
            continue
        if kind == "quote" and last_kind not in (None, "quote"):
            _flush()
        elif kind == "item":
            _flush()
        block.append((line_index, line))
        last_kind = kind
    _flush()
    return intervals


def _reject_indented_fences(lines: list[str], source: str) -> None:
    """Fail closed on a fence-looking line indented four or more spaces.

    Finding PX358-3 (issue #192): such a line is an indented code block in
    CommonMark, not a fence, but it would toggle the generator's fence state
    and wrongly expose or hide the links around it. The generator supports
    only the Markdown subset the corpus uses, so the construct is an error
    naming file and line, not a silent guess.
    """
    for line_no, line in enumerate(lines, start=1):
        match = FENCE_RE.match(line)
        if match is not None and match.start(1) >= 4:
            raise WikiPublishError(
                f"{source}:{line_no}: a fence indented 4 or more spaces is outside "
                "the supported Markdown subset (an indented code block in CommonMark, "
                "not a fence); indent fences by at most three spaces"
            )


def _rewrite_line(
    line: str, line_no: int, page_path: Path, root: Path, repo_url: str,
    page_names: dict[str, str], page_texts: dict[str, str],
    code_spans: list[tuple[int, int]],
) -> str:
    """Rewrite every real link on one line, leaving code spans untouched.

    A link is code only when its opening bracket sits inside a code span --
    which may have opened on an earlier line of the same block, so the spans
    come from `_code_span_intervals`, computed for the whole page. A code
    span may legitimately appear inside link text (``[`CLAUDE.md`](..)``).
    """

    def in_code(start: int) -> bool:
        return any(span_start <= start < span_end for span_start, span_end in code_spans)

    out: list[str] = []
    cursor = 0
    for match in LINK_RE.finditer(line):
        if in_code(match.start()):
            continue
        bang, text, dest, title = match.groups()
        replacement = _rewrite_link(
            bang, text, dest, title, line_no, page_path, root, repo_url, page_names, page_texts
        )
        if replacement is None:
            continue
        out.append(line[cursor:match.start()])
        out.append(replacement)
        cursor = match.end()
    out.append(line[cursor:])
    return "".join(out)


def _rewrite_link(
    bang: str, text: str, dest: str, title: str, line_no: int, page_path: Path,
    root: Path, repo_url: str, page_names: dict[str, str], page_texts: dict[str, str],
) -> str | None:
    """The rewritten `[text](dest)` for one link, or None to leave it alone."""
    source = page_path.relative_to(root).as_posix()
    where = f"{source}:{line_no}"
    if dest.startswith("#"):
        # A same-page anchor: validated against this page's own headings.
        anchor = dest[1:]
        if anchor not in heading_slugs(page_texts[source]):
            raise WikiPublishError(f"{where}: no heading anchor #{anchor} in {source}")
        return None
    if EXTERNAL_RE.match(dest):
        return None
    file_part, _, anchor = dest.partition("#")
    if not file_part:  # pragma: no cover - unreachable via LINK_RE, kept as a guard
        raise WikiPublishError(f"{where}: unparseable link destination {dest!r}")
    target, relative = _resolve_target(file_part, page_path, root, line_no)
    if anchor and target.suffix == ".md":
        target_text = page_texts.get(relative)
        if target_text is None:
            target_text = _read_page(target, relative)
            page_texts[relative] = target_text
        if anchor not in heading_slugs(target_text):
            raise WikiPublishError(
                f"{where}: no heading anchor #{anchor} in {relative}"
            )
    new_dest: str
    if target.is_dir():
        new_dest = f"{repo_url}/tree/main/{relative}"
    elif bang == "!":
        new_dest = f"{repo_url}/blob/main/{relative}?raw=true"
    elif relative in page_names:
        new_dest = f"{repo_url}/wiki/{page_names[relative]}"
    else:
        new_dest = f"{repo_url}/blob/main/{relative}"
    suffix = f"#{anchor}" if anchor else ""
    return f"{bang}[{text}]({new_dest}{suffix}{title or ''})"


def _transform_page(
    text: str, page_path: Path, root: Path, repo_url: str,
    page_names: dict[str, str], page_texts: dict[str, str],
) -> str:
    """Rewrite a page's links and prepend the generated-source notice."""
    source = page_path.relative_to(root).as_posix()
    lines = text.replace("\r\n", "\n").split("\n")
    _reject_indented_fences(lines, source)
    code_spans = _code_span_intervals(lines)
    out: list[str] = []
    for line_no, line, in_fence in _lines_with_fences(lines):
        if in_fence:
            out.append(line)
            continue
        out.append(
            _rewrite_line(
                line, line_no, page_path, root, repo_url, page_names, page_texts,
                code_spans.get(line_no - 1, []),
            )
        )
    return notice_for(source) + "\n\n" + "\n".join(out)


SIDEBAR_HEADING = "## What is in this folder"

# The punctuation a backslash can escape in CommonMark: the escaped
# character is literal text wherever it appears outside a code span.
ESCAPABLE = frozenset(r"!\"#$%&'()*+,-./:;<=>?@[\]^_`{|}~")

# An ATX level-1 heading line: up to three leading spaces (four make an
# indented code block in CommonMark), one '#', required whitespace, then
# the heading text with trailing spaces/tabs removed. '## deeper' does not
# match (the character after the single '#' must be whitespace). The
# optional closing '#' run is stripped separately (CLOSING_HASHES_RE) so it
# only counts when whitespace precedes it: '# Feature#' keeps its hash as
# heading text while '# Feature ###' drops the run.
H1_RE = re.compile(r"^ {0,3}#\s+(.*?)[ \t]*$")

# A closing '#' run: whitespace, the hashes, nothing after but spaces/tabs
# (CommonMark: the run must be preceded by spaces or tabs, followed by
# spaces or tabs only).
CLOSING_HASHES_RE = re.compile(r"[ \t]+#+[ \t]*$")

# A heading whose entire text is one '#' run ('# ###', '# #') is empty: the
# run is a closing sequence preceded by the opening whitespace, so the
# page falls back to its file name; '# ### ###' keeps the first run.
HASH_RUN_RE = re.compile(r"#+")


def _first_heading(text: str) -> str | None:
    """The page's own first ATX level-1 heading, as plain text, or None.

    The first level-1 heading line outside any fenced code block, trailing '#'
    and surrounding whitespace removed, inline markup (links, code spans,
    bold/italic markers) reduced to plain text and whitespace collapsed. A
    page whose only level-1 line sits inside a fenced code block, or that has
    none, has no first heading (the sidebar then falls back to the file name).
    """
    for _line_no, line, in_fence in _lines_with_fences(text.replace("\r\n", "\n").split("\n")):
        if in_fence:
            continue
        match = H1_RE.match(line)
        if match is None:
            continue
        if HASH_RUN_RE.fullmatch(match.group(1)):
            return None
        plain = _plain_text(CLOSING_HASHES_RE.sub("", match.group(1)))
        return plain or None
    return None


def _extract_code_spans(text: str) -> tuple[str, list[str]]:
    """Replace CommonMark code spans with placeholders; return the text and the
    stashed contents. A span opens with a backtick run and closes on the next
    run of exactly the same length (a longer run in between is content, not a
    closer); a run with no matching closer is literal text, not a span.

    Backslash-escaped punctuation is stashed too (as \\x01char\\x01): it is
    literal text, so an escaped backtick cannot open a span and an escaped
    emphasis marker is not emphasis. Backslashes have no effect inside a
    span (CommonMark), so the closer search ignores them."""
    out: list[str] = []
    spans: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "\\" and i + 1 < n and text[i + 1] in ESCAPABLE:
            out.append(f"\x01{text[i + 1]}\x01")
            i += 2
            continue
        if text[i] != "`":
            out.append(text[i])
            i += 1
            continue
        j = i
        while j < n and text[j] == "`":
            j += 1
        run = j - i
        k = j
        while k < n:
            if text[k] != "`":
                k += 1
                continue
            m = k
            while m < n and text[m] == "`":
                m += 1
            if m - k == run:
                content = text[j:k]
                # CommonMark: a span whose content begins and ends with a
                # space character (and is not all spaces) loses one space
                # at each end. Only ASCII spaces count, so a tab is content.
                if len(content) > 1 and content[0] == " " and content[-1] == " " and content.strip(" "):
                    content = content[1:-1]
                spans.append(content)
                out.append(f"\x00{len(spans) - 1}\x00")
                k = m
                break
            k = m  # a run of another length is content, not a closer
        else:
            out.append(text[i:j])  # no matching closer: the run is literal
            i = j
            continue
        i = k
    return "".join(out), spans


# Raw inline HTML, reduced to its rendered text: comments and open/close
# tags render as nothing, so the label keeps only the text between them.
# A quoted attribute value may contain '>' or '<', so values are matched
# whole rather than ending the tag at the first '>' inside quotes.
HTML_COMMENT_RE = re.compile(r"<!--.*?-->")
_HTML_ATTR = r"[^\s<>=]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'>]+))?"
HTML_TAG_RE = re.compile(rf"</?[A-Za-z][A-Za-z0-9-]*(?:\s+{_HTML_ATTR})*\s*/?>")

# A '<' that still starts something tag-like after the two forms above were
# removed is a raw-HTML construct this reducer does not know how to render
# (a processing instruction, declaration, autolink, ...): fail safe by giving
# the page its file-name label rather than guess at a wrong one.
RAW_HTML_LIKE_RE = re.compile(r"<[A-Za-z/?!]")


# A link as the label reducer sees it: [text](destination), an optional
# quoted title, and a destination that may carry one balanced paren pair
# (CommonMark) -- as in 'https://example.test/a_(b))' -- so the pair does
# not end the destination early and leak a stray ')' into the label.
LABEL_LINK_RE = re.compile(
    r"(!?)\[([^\]\n]*)\]\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)(\s+\"[^\"]*\")?\)"
)


def _plain_text(heading: str) -> str | None:
    """Heading text with inline markup reduced to plain text, or None.

    Code spans are stashed first and restored last, so their contents are
    literal text: an underscore inside `` `snake_case` `` is content, not
    emphasis, and backticks inside a span stay put. Underscore emphasis is
    only reduced at word boundaries -- an intraword underscore is content
    (``foo_bar_baz`` has no emphasis in CommonMark), while intraword asterisks
    are still emphasis. Raw inline HTML comments and open/close tags reduce
    to the text between them; any other tag-like construct returns None, so
    the caller falls back to the file-name label instead of a wrong label.
    """
    text, code_spans = _extract_code_spans(heading)
    text = HTML_COMMENT_RE.sub("", text)
    text = HTML_TAG_RE.sub("", text)
    if RAW_HTML_LIKE_RE.search(text):
        return None
    text = LABEL_LINK_RE.sub(r"\2", text)  # [text](target) and images -> text
    text = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", text)  # reference links
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # bold (asterisks; intraword is emphasis)
    text = re.sub(r"(?<!\w)__(?!\s)(.+?)(?<!\s)__(?!\w)", r"\1", text)  # bold underscores
    text = re.sub(r"\*([^*\n]+)\*", r"\1", text)  # italic (asterisks; intraword is emphasis)
    text = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", text)  # italic underscores
    text = re.sub(r"~~(.+?)~~", r"\1", text)  # strikethrough
    # Collapse whitespace before restoring the code spans: a span's content
    # is literal text, so the collapse must not reach inside it (CommonMark
    # preserves a span's interior spacing). Escaped punctuation is restored
    # as the literal character it renders as.
    text = " ".join(text.split())
    text = re.sub(r"\x01(.)\x01", r"\1", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: code_spans[int(m.group(1))], text)
    return text


# The characters that can change how a Markdown link label renders: the label
# delimiters, the escape character itself, and the inline-markup and HTML
# metacharacters (backticks, emphasis, strikethrough, autolinks/entities).
LABEL_ESCAPE_RE = re.compile(r"[\\\[\]`<>*_&~]")


def _escape_label(label: str) -> str:
    """Escape the characters that are significant inside a Markdown link label."""
    return LABEL_ESCAPE_RE.sub(lambda m: f"\\{m.group(0)}", label)


def _sidebar(
    root: Path, page_names: dict[str, str], page_texts: dict[str, str], repo_url: str
) -> str:
    """The `_Sidebar.md` content: the docs index's reading order.

    The docs index table is the sidebar's source: Home first, then one link
    per row, in the order the table gives. Home keeps its label; every other
    entry's label is its page's OWN first heading (the first ATX level-1
    heading line outside any fenced code block, markup reduced to plain text,
    Markdown-significant characters escaped), never a hard-coded name; a page
    with no first heading falls back to its file name. Link targets and order
    are the table's. The pages published from `docs/examples/` close the
    sidebar as a short "Examples" group, each labelled from its own first
    heading (identical labels disambiguated minimally by their file names);
    they are published pages but deliberately not index rows in
    `docs/README.md`, so the group is the only place the sidebar names them.
    """
    lines = page_texts["docs/README.md"].replace("\r\n", "\n").split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == SIDEBAR_HEADING)
    except StopIteration:
        raise WikiPublishError(
            "docs/README.md: no 'What is in this folder' index table to build the sidebar from"
        ) from None
    table: list[tuple[int, str]] = []
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith("|"):
            table.append((i + 1, lines[i].strip()))
        elif table:
            break
        elif lines[i].startswith("#"):
            break  # a new section began before any table
    if not table:
        raise WikiPublishError(
            "docs/README.md: no 'What is in this folder' index table to build the sidebar from"
        )
    entries: list[tuple[str, str]] = [("Home", "Home")]
    table_rels: list[str] = []
    for row_no, (line_no, row) in enumerate(table):
        if set(row) <= {"|", " ", ":", "-"}:
            continue  # the column-separator line
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        match = LINK_RE.search(cells[0])
        if match is None or match.group(1):
            if row_no == 0:
                continue  # the table's header row, not a data row
            raise WikiPublishError(
                f"docs/README.md:{line_no}: index table row {row!r} has no page link in its "
                "first column"
            )
        target, _ = _resolve_target(match.group(3), root / "docs" / "README.md", root, line_no)
        relative = target.relative_to(root).as_posix()
        if relative not in page_names:
            raise WikiPublishError(
                f"docs/README.md:{line_no}: index table row points at {relative}, which is not a "
                "published page"
            )
        table_rels.append(relative)
        label = _first_heading(page_texts[relative]) or Path(relative).stem
        entries.append((label, page_names[relative]))
    top_level = {
        relative
        for relative in page_names
        if relative != "docs/README.md" and relative.count("/") == 1
    }
    missing = top_level - set(table_rels)
    if missing:
        raise WikiPublishError(
            "docs/README.md: page(s) missing from the 'What is in this folder' index table: "
            + ", ".join(sorted(missing))
        )
    lines = [f"- [{_escape_label(label)}]({repo_url}/wiki/{name})" for label, name in entries]
    examples = sorted(relative for relative in page_names if relative.startswith("docs/examples/"))
    if examples:
        example_labels = {relative: _first_heading(page_texts[relative]) or Path(relative).stem
                          for relative in examples}
        duplicated = {label for label in example_labels.values()
                      if list(example_labels.values()).count(label) > 1}
        lines.append("")
        lines.append("**Examples**")
        lines.append("")
        for relative in examples:
            label = example_labels[relative]
            if label in duplicated:  # disambiguate identical labels minimally
                label = f"{label} ({Path(relative).stem})"
            lines.append(f"- [{_escape_label(label)}]({repo_url}/wiki/{page_names[relative]})")
    body = "\n".join(lines)
    return notice_for("docs/README.md") + "\n\n" + body + "\n"


def build_tree(root: Path, repo: str) -> dict[str, str]:
    """Build the wiki tree in memory: output file name -> page content.

    Every page is validated before the tree is returned, so a failure raises
    and nothing is written. The result is a pure function of the ``docs/``
    tree and the script.
    """
    root = Path(root).resolve()
    repo_parts = repo.split("/")
    if (
        len(repo_parts) != 2
        or not all(repo_parts)
        or any(c.isspace() for c in repo)
    ):
        raise WikiPublishError(f"{repo!r}: --repo must look like owner/name")
    repo_url = f"https://github.com/{repo}"
    pages = discover_pages(root)
    if "docs/README.md" not in {
        path.relative_to(root).as_posix() for path in pages.values()
    }:
        raise WikiPublishError(
            "docs/README.md:1: the docs index is required (Home and the sidebar "
            "are generated from it)"
        )
    page_names = {
        path.relative_to(root).as_posix(): name for name, path in pages.items()
    }
    page_texts = {
        path.relative_to(root).as_posix(): _read_page(path, path.relative_to(root).as_posix())
        for path in sorted(pages.values())
    }
    tree: dict[str, str] = {}
    for name, path in sorted(pages.items()):
        source = path.relative_to(root).as_posix()
        tree[f"{name}.md"] = _transform_page(
            page_texts[source], path, root, repo_url, page_names, page_texts
        )
    tree["_Sidebar.md"] = _sidebar(root, page_names, page_texts, repo_url)
    return tree


def check_out_dir(root: Path, out_dir: Path) -> None:
    """Refuse an output directory that overlaps the source tree.

    The write replaces top-level ``*.md`` files in `out_dir`, so an output path that is the repository root, equal to
    or inside ``docs/``, or any ancestor of ``docs/`` would overwrite or delete the canonical documentation. Both
    paths are resolved first, so relative paths, ``..`` segments and symlinks cannot hide an overlap.
    """
    docs = (root / "docs").resolve()
    out = out_dir.resolve()
    if out == docs or docs in out.parents or out in docs.parents:
        raise WikiPublishError(
            f"{out_dir}: the output directory must not be the repository root, docs/, a directory inside docs/ "
            "or an ancestor of docs/ (it would overwrite the source pages)"
        )


def write_tree(out_dir: Path, tree: dict[str, str]) -> None:
    """Write the generated tree into `out_dir`, replacing any stale wiki pages.

    The wiki tree is flat Markdown files, so the write is a full replacement:
    every generated page is written (UTF-8, LF) and any other top-level
    ``*.md`` file already in the directory is removed, so the output directory
    never becomes a second documentation source. Files that are not ``*.md``
    are left alone.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(tree):
        with open(out_dir / name, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(tree[name])
    for stale in sorted(out_dir.glob("*.md")):
        if stale.is_file() and stale.name not in tree:
            stale.unlink()


GIT_TIMEOUT_SECONDS = 300

# The wiki commit's identity: the standard non-person GitHub Actions bot, so
# the commit never depends on a developer's or a runner's global git config.
COMMIT_AUTHOR_NAME = "github-actions[bot]"
COMMIT_AUTHOR_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def _git_environment() -> dict[str, str]:
    """A git environment with no inherited git configuration, token or prompting.

    ``GIT_*`` variables (a hook or an outer git process can set them) are
    dropped, as is ``GITHUB_TOKEN`` (this script reads it once and hands it to git
    only as an HTTP header, so no git child process, hook or helper inherits it),
    credential prompts are disabled and the empty ``credential.helper``
    below keeps any configured helper from injecting a different credential.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_") and key != "GITHUB_TOKEN"}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(
    args: list[str], cwd: Path | None = None, token: str | None = None, text: bool = True
) -> subprocess.CompletedProcess:
    """Run one git command as a fixed argument list (no shell), capturing output.

    With a token, authentication travels as an HTTP extra header via ``git -c
    http.extraheader=...``: the token never appears in a URL, in the
    repository's configuration or in any file, and git's own output is
    captured here, never passed through to ours. The command line is never
    printed; failure messages name the operation, not the command. ``text=False``
    gives binary output, for the one call that must round-trip filenames exactly
    (see publish()).
    """
    argv = ["git", "-c", "commit.gpgsign=false", "-c", "credential.helper="]
    if token is not None:
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
        argv += ["-c", f"http.extraheader=Authorization: Basic {encoded}"]
    argv += list(args)
    # A fixed `git` argument list, no shell, no untrusted executable name (S603/S607).
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=text,  # noqa: S603, S607
                          env=_git_environment(), timeout=GIT_TIMEOUT_SECONDS, check=False)


def _fresh_work_dir(root: Path) -> Path:
    """A fresh temporary directory outside the repository.

    The paths are resolved first, so a ``TMPDIR`` inside the repository cannot
    put the clone (a whole git repository) into the source tree; that is an
    error, not a silent write into the checkout.
    """
    work = Path(tempfile.mkdtemp(prefix="wiki-publish-")).resolve()
    if work == root or root in work.parents:
        shutil.rmtree(work, ignore_errors=True)  # the directory mkdtemp just made must not be left behind
        raise WikiPublishError(
            f"{work}: the temporary working directory must not be inside the repository "
            "(is TMPDIR set to a path inside it?)"
        )
    return work


def _push_failure_message(stderr: str) -> str:
    """One sanitized error line for a failed push, classified from git's captured
    stderr (which is read for the classification and never echoed): an authorisation
    rejection is the documented maintainer decision; a transport problem is retried;
    anything else stays inconclusive rather than speculating about the token."""
    detail = stderr or ""
    if re.search(r"(?i)authentication failed|http 40[13]|\b40[13]\b|could not read username", detail):
        return (
            "pushing to the wiki failed with an authorisation rejection: the repository token "
            "cannot write to the wiki (a maintainer decision; never a fallback token)"
        )
    if re.search(r"(?i)could not resolve|connection|ssl|timed out|network", detail):
        return (
            "pushing to the wiki failed with a transport error "
            "(re-run the workflow; publishing is idempotent)"
        )
    return "pushing to the wiki failed (cause not identified; inconclusive, not evidence about the token)"


def publish(root: Path, repo: str, source_sha: str, remote: str | None = None) -> None:
    """Generate the wiki tree and publish it to the repository's wiki.

    Generation and validation run first: a broken corpus raises before anything
    is cloned or written. The wiki repository (``https://github.com/<repo>.wiki.git``,
    or ``remote`` -- tests only, pointing at a local bare repository so no
    token is needed) is cloned on its default branch, whatever that branch is
    named; the clone's content is fully replaced with the generated set
    (tracked files not in the set are removed, ``.git`` is untouched); if the
    result is identical to what is already published, nothing is committed and
    nothing is pushed ("no change"). Otherwise one commit -- message
    ``Publish docs from main <sha>`` with the ``docs-source: <sha>`` trailer --
    is pushed back to the same default branch. With a real remote the token
    is read from ``GITHUB_TOKEN`` and reaches git only as an HTTP header.
    """
    root = Path(root).resolve()
    tree = build_tree(root, repo)
    token = None if remote is not None else os.environ.get("GITHUB_TOKEN")
    clone_url = remote if remote is not None else f"https://github.com/{repo}.wiki.git"
    work = _fresh_work_dir(root)
    try:
        clone = work / "wiki"
        if _run_git(["clone", "--quiet", clone_url, str(clone)], token=token).returncode != 0:
            raise WikiPublishError(
                "cloning the wiki repository failed (is the wiki initialised and reachable?)"
            )
        branch_result = _run_git(["symbolic-ref", "--short", "HEAD"], cwd=clone)
        if branch_result.returncode != 0:
            raise WikiPublishError("the wiki repository has no branch to publish to")
        branch = branch_result.stdout.strip()
        # -z, binary, decoded with surrogateescape: filenames reach git rm as the exact
        # bytes git reported, on every platform and locale -- a text-mode decode (cp1252
        # on Windows) can turn a valid UTF-8 name into a different path that fails removal.
        tracked = _run_git(["ls-files", "-z"], cwd=clone, text=False)
        if tracked.returncode != 0:
            raise WikiPublishError("listing the wiki repository's files failed")
        for raw in tracked.stdout.split(b"\0"):
            if not raw:
                continue
            name = raw.decode("utf-8", "surrogateescape")
            if name not in tree:
                # :\u0028literal\u0029 disables pathspec magic, so a name that IS magic (a leading colon)
                # is removed as the literal path it is; -- only stops option parsing.
                if _run_git(["rm", "-q", "--", f":(literal){name}"], cwd=clone).returncode != 0:
                    raise WikiPublishError(f"removing the stale wiki file {name} failed")
        # A tracked (or stray) entry at a generated name can be a symlink, and a plain
        # open() follows it: unlink first, so every generated page is written as a fresh
        # regular file inside the clone and nothing outside it can be written through.
        for name in sorted(tree):
            target = clone / name
            if target.exists() or target.is_symlink():
                target.unlink()
        write_tree(clone, tree)
        for name in sorted(tree):
            # :\u0028literal\u0029 here too: a generated name that IS pathspec magic (a docs page
            # whose filename starts with a colon -- legal on POSIX) must stage literally.
            if _run_git(["add", "--", f":(literal){name}"], cwd=clone).returncode != 0:
                raise WikiPublishError(f"staging the generated page {name} failed")
        has_head = _run_git(["rev-parse", "--verify", "-q", "HEAD"], cwd=clone).returncode == 0
        if has_head and _run_git(["diff", "--cached", "--quiet", "HEAD"], cwd=clone).returncode == 0:
            print(f"no change: the wiki already matches docs/ ({len(tree)} pages)")
            return
        commit = _run_git(
            ["-c", f"user.name={COMMIT_AUTHOR_NAME}", "-c", f"user.email={COMMIT_AUTHOR_EMAIL}",
             "commit", "-q",
             "-m", f"Publish docs from main {source_sha}",
             "-m", f"docs-source: {source_sha}"],
            cwd=clone,
        )
        if commit.returncode != 0:
            raise WikiPublishError("committing the generated wiki pages failed")
        push = _run_git(["push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], cwd=clone, token=token)
        if push.returncode != 0:
            raise WikiPublishError(_push_failure_message(push.stderr))
        print(f"published {len(tree)} pages to {repo} wiki (branch {branch})")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """The command line: build (and optionally write or publish) the wiki tree."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="wiki_publish.py",
        description="Generate the GitHub Wiki page tree from the docs/ corpus; optionally publish it.",
    )
    parser.add_argument("--repo", required=True, help="owner/name of the repository the URLs point at")
    parser.add_argument("--root", default=".", help="the repository root (default: the current directory)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", help="write the generated wiki tree into this directory")
    mode.add_argument("--check", action="store_true", help="build and validate in memory, write nothing")
    mode.add_argument("--publish", action="store_true",
                      help="clone the repository's wiki, replace its content with the generated tree and push one commit")
    parser.add_argument("--source-sha",
                        help="the source commit a publish ran from (required with --publish; carried in the commit message)")
    parser.add_argument("--remote", help=argparse.SUPPRESS)  # tests only: a local bare repository instead of GitHub
    args = parser.parse_args(argv)
    if args.publish and not args.source_sha:
        parser.error("--publish requires --source-sha")
    if args.remote is not None and not args.publish:
        parser.error("--remote is only valid with --publish")
    try:
        if args.publish:
            if args.remote is None and not os.environ.get("GITHUB_TOKEN"):
                raise WikiPublishError(
                    "GITHUB_TOKEN is not set; the wiki cannot be published without it"
                )
            publish(Path(args.root), args.repo, args.source_sha, remote=args.remote)
            return 0
        if args.out is not None:
            check_out_dir(Path(args.root), Path(args.out))
        tree = build_tree(Path(args.root), args.repo)
        if args.check:
            print(f"OK: {len(tree)} pages built from docs/ (nothing written)")
            return 0
        write_tree(Path(args.out), tree)
        print(f"wrote {len(tree)} pages to {args.out}")
        return 0
    except subprocess.TimeoutExpired:
        # str() of a TimeoutExpired echoes the command line, which carries the
        # authentication header: the message must stay sanitized.
        print("error: a git operation timed out", file=sys.stderr)
        return 1
    except (WikiPublishError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    sys.exit(main())
