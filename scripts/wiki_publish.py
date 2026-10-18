"""Generate the GitHub Wiki page tree from the ``docs/`` corpus (issue #192).

PR1 is the generator only: it reads the allowlisted Markdown pages under
``docs/``, maps them to wiki page names, rewrites their links to wiki and
repository URLs, and writes the generated tree into an output directory (or
validates it in memory with ``--check``). It publishes nothing: no wiki, no
token, no network, no git, no workflow. The output is a pure function of the
``docs/`` tree and this script -- no timestamps, run ids or environment data.

Only regular (non-symlink) files matching ``docs/*.md`` and
``docs/examples/synthetic_co/*.md`` are read into the output; linked
repository files are checked for existence and rewritten to URLs, never
copied. No ``anthropic`` dependency.
"""
import os
import re
from pathlib import Path


class WikiPublishError(Exception):
    """A validation failure. The message always names the file (and line)."""


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
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
EXTERNAL_RE = re.compile(r"^(https?:|mailto:)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")


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
    in_fence = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            base = slug(match.group(1))
            count = seen.get(base, 0)
            seen[base] = count + 1
            slugs.add(base if count == 0 else f"{base}-{count}")
    return slugs


def _read_page(path: Path) -> str:
    """Read a page strictly as UTF-8; a decode failure names the file."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except UnicodeDecodeError as error:
        raise WikiPublishError(
            f"{path}: is not valid UTF-8 ({error.reason} at byte {error.start})"
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
        candidates.extend(sorted(docs.glob(pattern)))
    for path in sorted(candidates):
        if path.is_symlink():
            raise WikiPublishError(
                f"{path.relative_to(root).as_posix()}: symlinked pages are not publishable"
            )
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        name = _page_name(relative)
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


def _resolve_target(
    file_part: str, page_path: Path, root: Path, line_no: int
) -> tuple[Path, str]:
    """Resolve a relative link destination to (repository path, relative posix
    path), refusing targets that escape the repository or pass through a
    symlink, and requiring the target to exist."""
    source = page_path.relative_to(root).as_posix()
    where = f"{source}:{line_no}"
    if Path(file_part).is_absolute():
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


def _rewrite_line(
    line: str, line_no: int, page_path: Path, root: Path, repo_url: str,
    page_names: dict[str, str], page_texts: dict[str, str],
) -> str:
    """Rewrite every real link on one line, leaving code spans untouched."""
    code_spans = [match.span() for match in CODE_SPAN_RE.finditer(line)]

    def in_code(start: int, end: int) -> bool:
        return any(start < span_end and end > span_start for span_start, span_end in code_spans)

    out: list[str] = []
    cursor = 0
    for match in LINK_RE.finditer(line):
        if in_code(*match.span()):
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
    if not file_part:
        raise WikiPublishError(f"{where}: unparseable link destination {dest!r}")
    target, relative = _resolve_target(file_part, page_path, root, line_no)
    if anchor and target.suffix == ".md":
        target_text = page_texts.get(relative)
        if target_text is None:
            target_text = _read_page(target)
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
    out: list[str] = []
    in_fence = False
    for line_no, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        out.append(
            _rewrite_line(line, line_no, page_path, root, repo_url, page_names, page_texts)
        )
    return notice_for(source) + "\n\n" + "\n".join(out)


def build_tree(root: Path, repo: str) -> dict[str, str]:
    """Build the wiki tree in memory: output file name -> page content.

    Every page is validated before the tree is returned, so a failure raises
    and nothing is written. The result is a pure function of the ``docs/``
    tree and the script.
    """
    root = Path(root)
    if "/" not in repo or repo.count("/") != 1 or any(c.isspace() for c in repo):
        raise WikiPublishError(f"{repo!r}: --repo must look like owner/name")
    repo_url = f"https://github.com/{repo}"
    pages = discover_pages(root)
    page_names = {
        path.relative_to(root).as_posix(): name for name, path in pages.items()
    }
    page_texts = {
        path.relative_to(root).as_posix(): _read_page(path) for path in sorted(pages.values())
    }
    tree: dict[str, str] = {}
    for name, path in sorted(pages.items()):
        source = path.relative_to(root).as_posix()
        tree[f"{name}.md"] = _transform_page(
            page_texts[source], path, root, repo_url, page_names, page_texts
        )
    return tree
