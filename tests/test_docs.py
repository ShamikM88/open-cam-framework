"""Documentation contracts (issue #117): links resolve, headings that code cites exist, evergreen pages stay evergreen.

Deliberately small. The documentation set is README.md (landing page), docs/ (reference) and CLAUDE.md (operating
rules); these tests guard the few facts whose breakage is silent and costly:

- every relative Markdown link, and every `#heading` anchor, resolves (GitHub's slug rules);
- every page in docs/ is linked from the docs index, and the README links the index;
- the CLAUDE.md headings that scripts, commands and the registry cite by name still exist;
- the evaluation page stays evergreen (no volatile "has / has not been run" status), and the only dated
  statement of project status is the one in the docs index.

Flags are checked by tests/test_command_flags.py and `Guideline N` references by tests/test_prompt_consistency.py,
both of which scan docs/ as well.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC_FILES = ("README.md", "CLAUDE.md", "evals/README.md", "templates/README.md", "config/skills_registry.md")

# Headings (and bold labels) that code comments, commands or the registry cite by name. Renaming one means updating
# those citations too; this list is the reminder, so it is deliberately written out literally.
CITED_HEADINGS = (
    "Project overview", "Maker-Checker agents", "Context Window & State Management Protocol",
    "Source material persistence", "Persisted conventions", "Slash commands (primary interface)",
    "Execution scripts", "What `config/settings.json` actually controls", "Testing and static analysis",
    "Confidentiality rule for new features", "Git workflow",
)
CITED_LABELS = ("**Promoting a local override upstream:**", "**Mutation testing**")


def doc_files():
    return [REPO / name for name in DOC_FILES] + sorted((REPO / "docs").glob("*.md"))


def strip_fences(text):
    """Text without fenced code blocks."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def strip_code(text):
    """Text without fenced code blocks and inline code spans (links inside code are examples, not links)."""
    return "\n".join(re.sub(r"`[^`\n]*`", "", line) for line in strip_fences(text).split("\n"))


def slug(heading_text):
    """GitHub's heading anchor: formatting removed, lower-cased, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading_text)
    text = re.sub(r"[`*_~]", lambda m: "_" if m.group(0) == "_" else "", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def heading_slugs(text):
    """The set of anchors a Markdown file defines, with GitHub's -1, -2 suffixes for repeated headings."""
    seen, slugs = {}, set()
    for line in strip_fences(text).split("\n"):
        match = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if match:
            base = slug(match.group(1))
            count = seen.get(base, 0)
            seen[base] = count + 1
            slugs.add(base if count == 0 else f"{base}-{count}")
    return slugs


LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def links(text):
    return LINK_RE.findall(strip_code(text))


def broken_links(path, root=REPO):
    """[(link, why)] for every relative link or anchor in `path` that does not resolve."""
    text = path.read_text(encoding="utf-8")
    problems = []
    for target in links(text):
        if re.match(r"^(https?:|mailto:)", target):
            continue
        file_part, _, anchor = target.partition("#")
        dest = path if not file_part else (path.parent / file_part)
        if not dest.exists():
            problems.append((target, "no such file or directory"))
            continue
        if anchor and dest.is_file() and dest.suffix == ".md":
            if anchor not in heading_slugs(dest.read_text(encoding="utf-8")):
                problems.append((target, f"no heading anchor #{anchor} in {dest.relative_to(root)}"))
    return problems


def test_every_relative_link_and_heading_anchor_resolves():
    problems = {path.relative_to(REPO).as_posix(): broken_links(path) for path in doc_files()}
    problems = {name: found for name, found in problems.items() if found}
    assert problems == {}


def test_every_docs_page_is_linked_from_the_index_and_the_readme_links_the_index():
    index = (REPO / "docs" / "README.md").read_text(encoding="utf-8")
    linked = {Path(t.partition("#")[0]).name for t in links(index) if not t.startswith("http")}
    pages = {p.name for p in (REPO / "docs").glob("*.md") if p.name != "README.md"}
    assert pages - linked == set(), "pages not linked from docs/README.md"
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "docs/README.md" in links(readme)
    assert "move-ledger.md" in pages and "decisions.md" in pages and "architecture.md" in pages


def test_the_claude_md_headings_that_code_and_commands_cite_still_exist():
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    headings = {m.group(1).strip() for m in re.finditer(r"^#{1,6}\s+(.*?)\s*$", strip_fences(text), re.M)}
    assert [h for h in CITED_HEADINGS if h not in headings] == []
    assert [label for label in CITED_LABELS if label not in text] == []


def test_those_citations_are_still_made_so_the_list_above_is_not_stale():
    """Each cited heading is actually cited somewhere outside CLAUDE.md (a citation list nobody uses rots)."""
    sources = [*REPO.glob("scripts/*.py"), *REPO.glob(".claude/commands/*.md"), REPO / "config" / "skills_registry.md"]
    corpus = "\n".join(re.sub(r"\s+", " ", p.read_text(encoding="utf-8")) for p in sources)
    for cited in ("Context Window & State Management Protocol", "What `config/settings.json` actually controls",
                  "Execution scripts", "Promoting a local override upstream", "Mutation testing"):
        assert cited in corpus or cited.replace("`", "") in corpus, cited


def test_the_evaluation_page_is_evergreen_and_only_the_index_carries_a_dated_status():
    evaluation = (REPO / "docs" / "evaluation.md").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", evaluation).lower()
    for volatile in ("has been run yet", "not yet been run", "no live evaluation has been run", "as of 20"):
        assert volatile not in flat, volatile
    dated = {p.name for p in (REPO / "docs").glob("*.md")
             if re.search(r"As of \d{4}-\d{2}-\d{2}", p.read_text(encoding="utf-8"))}
    assert dated == {"README.md"}
    index = (REPO / "docs" / "README.md").read_text(encoding="utf-8")
    assert "## Project status" in index and "#151" in index


# ---------------------------------------------------------------------------
# The checks themselves can fail
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("heading, expected", [
    ("Context Window & State Management Protocol", "context-window--state-management-protocol"),
    ("What `config/settings.json` actually controls", "what-configsettingsjson-actually-controls"),
    ("Confidentiality — what never belongs in this repo", "confidentiality--what-never-belongs-in-this-repo"),
    ("State manager and state.json", "state-manager-and-statejson"),
    ("D6. Forward-year covenant results are disclosure only", "d6-forward-year-covenant-results-are-disclosure-only"),
    ("Slash commands (primary interface)", "slash-commands-primary-interface"),
    ("[Link](x.md) text", "link-text"),
])
def test_the_anchor_rule_matches_githubs(heading, expected):
    assert slug(heading) == expected


def test_repeated_headings_get_numbered_anchors_and_code_is_ignored():
    text = "# A\n\n## Same\n\n## Same\n\n```\n# not a heading\n```\n"
    assert heading_slugs(text) == {"a", "same", "same-1"}


def test_links_inside_code_are_not_checked_but_real_ones_are():
    text = "See `[x](nope.md)` and [y](real.md#top).\n\n```\n[z](nope2.md)\n```\n"
    assert links(text) == ["real.md#top"]


def test_the_link_check_reports_a_missing_file_and_a_missing_anchor(tmp_path):
    (tmp_path / "target.md").write_text("# Title\n\n## Real section\n", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text("[ok](target.md#real-section) [file](gone.md) [anchor](target.md#nope) [self](#title-here)\n"
                    "# Title here\n[web](https://example.invalid/x#y)\n", encoding="utf-8")
    found = broken_links(page, root=tmp_path)
    assert [t for t, _ in found] == ["gone.md", "target.md#nope"]
    assert "no such file" in found[0][1] and "#nope" in found[1][1]


def test_a_heading_with_inline_code_keeps_its_code_text_in_the_anchor():
    """GitHub keeps the text of inline code in a heading's anchor (it only drops the backticks)."""
    text = "## `orchestrator.py` ends in a traceback\n\n### What `config/settings.json` controls\n"
    assert heading_slugs(text) == {"orchestratorpy-ends-in-a-traceback", "what-configsettingsjson-controls"}
