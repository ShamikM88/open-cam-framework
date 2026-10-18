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


def notice_for(source: str) -> str:
    """The generated-source notice every generated page starts with."""
    return NOTICE_TEMPLATE.format(source=source)


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
