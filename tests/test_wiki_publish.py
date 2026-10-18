"""Tests for `scripts/wiki_publish.py` (issue #192, PR1: the generator only).

The generator turns the allowlisted `docs/` Markdown pages into a GitHub Wiki
page tree in an output directory: page names mapped, links rewritten to wiki
and repository URLs, a generated-source notice on every page, Home from the
docs index and `_Sidebar.md` from its reading-order table. It publishes
nothing -- no wiki, no token, no network, no git -- and is deterministic.

These tests build synthetic corpora in `tmp_path` and also run against the
real `docs/` tree of this repository. Nothing here touches anything outside
the repository or the test's own temporary directory.
"""
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from wiki_publish import (  # noqa: E402
    WikiPublishError,
    discover_pages,
    notice_for,
)


def write_index(root: Path, rows=("",)) -> Path:
    """Write a docs/README.md whose 'What is in this folder' table lists `rows`."""
    table = "\n".join(f"| [{name}.md]({name}.md) | x |" for name in rows)
    path = root / "docs" / "README.md"
    path.write_text(
        "# Documentation\n\n## What is in this folder\n\n"
        f"| Document | Contents |\n| :--- | :--- |\n{table}\n",
        encoding="utf-8",
    )
    return path


class TestPageDiscovery:
    def test_top_level_pages_are_named_after_their_files(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("architecture", "workflows"))
        for name in ("architecture", "workflows"):
            (tmp_path / "docs" / f"{name}.md").write_text("# x\n", encoding="utf-8")
        pages = discover_pages(tmp_path)
        assert sorted(pages) == ["Home", "architecture", "workflows"]

    def test_the_docs_index_becomes_home(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path)
        (tmp_path / "docs" / "a.md").write_text("# x\n", encoding="utf-8")
        assert "Home" in discover_pages(tmp_path)

    def test_example_pages_are_flattened_with_a_prefix(self, tmp_path):
        example = tmp_path / "docs" / "examples" / "synthetic_co"
        example.mkdir(parents=True)
        write_index(tmp_path)
        (example / "brief.md").write_text("# x\n", encoding="utf-8")
        pages = discover_pages(tmp_path)
        assert "examples-synthetic_co-brief" in pages

    def test_a_case_insensitive_name_collision_is_an_error(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("page",))
        (tmp_path / "docs" / "page.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs" / "PAGE.md").write_text("# x\n", encoding="utf-8")
        with pytest.raises(WikiPublishError, match="(?i)collide"):
            discover_pages(tmp_path)

    def test_a_symlinked_page_is_rejected(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("real",))
        (tmp_path / "docs" / "real.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs" / "linked.md").symlink_to(tmp_path / "docs" / "real.md")
        with pytest.raises(WikiPublishError, match="linked.md"):
            discover_pages(tmp_path)

    def test_only_the_allowlisted_patterns_are_discovered(self, tmp_path):
        docs = tmp_path / "docs"
        (docs / "examples" / "synthetic_co").mkdir(parents=True)
        write_index(tmp_path, rows=("kept",))
        (docs / "kept.md").write_text("# x\n", encoding="utf-8")
        (docs / "examples" / "synthetic_co" / "brief.md").write_text("# x\n", encoding="utf-8")
        # Everything below is outside the allowlist and must not become a page.
        (docs / "notes.txt").write_text("x\n", encoding="utf-8")
        (docs / "examples" / "other.md").write_text("# x\n", encoding="utf-8")
        (docs / "examples" / "synthetic_co" / "input.json").write_text("{}\n", encoding="utf-8")
        pages = discover_pages(tmp_path)
        assert sorted(pages) == ["Home", "examples-synthetic_co-brief", "kept"]


class TestNotice:
    def test_every_page_notice_states_source_canonical_overwrite_and_pr(self):
        text = notice_for("docs/architecture.md")
        assert text.startswith("> ")
        assert "docs/architecture.md" in text
        assert "canonical" in text
        assert "overwritten" in text
        assert "pull request" in text

    def test_the_notice_wording_is_pinned(self):
        assert notice_for("docs/workflows.md") == (
            "> This page is generated from `docs/workflows.md` in the repository -- `docs/` is "
            "the canonical documentation. Direct wiki edits here are overwritten on the next "
            "publish; make changes in `docs/` by pull request."
        )
