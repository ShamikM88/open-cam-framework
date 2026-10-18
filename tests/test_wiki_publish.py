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

import wiki_publish

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from wiki_publish import (  # noqa: E402
    WikiPublishError,
    build_tree,
    discover_pages,
    notice_for,
)

U = "https://github.com/example/repo"

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


def make_corpus(tmp_path: Path, pages: dict[str, str]) -> dict[str, str]:
    """Write a docs/ corpus and return the generated wiki tree (stub repo)."""
    docs = tmp_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    top = [name for name in pages if "/" not in name]
    write_index(tmp_path, rows=tuple(name[:-3] for name in top if name != "README.md"))
    for name, content in pages.items():
        target = docs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return build_tree(tmp_path, "example/repo")


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


class TestLinkRewriting:
    def test_a_page_to_page_link_becomes_a_wiki_url(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "See [workflows](workflows.md).\n",
            "workflows.md": "# Workflows\n",
        })
        assert tree["source.md"] == notice_for("docs/source.md") + "\n\nSee [workflows]({U}/wiki/workflows).\n".replace("{U}", U)

    def test_a_page_link_with_an_anchor_targets_the_wiki_page(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n\n## Project status\n",
            "source.md": "[status](README.md#project-status)\n",
            "page.md": "# x\n",
        })
        assert f"({U}/wiki/Home#project-status)" in tree["source.md"]

    def test_a_same_page_anchor_is_left_alone(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# Source\n\n## Section\n\n[jump](#section)\n",
            "page.md": "# x\n",
        })
        assert "[jump](#section)" in tree["source.md"]

    def test_a_repository_file_link_becomes_a_blob_url(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "state_manager.py").write_text("", encoding="utf-8")
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[state_manager](../scripts/state_manager.py)\n",
        })
        assert f"({U}/blob/main/scripts/state_manager.py)" in tree["source.md"]

    def test_a_repository_directory_link_becomes_a_tree_url(self, tmp_path):
        (tmp_path / "templates").mkdir()
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[templates](../templates/)\n",
        })
        assert f"({U}/tree/main/templates)" in tree["source.md"]

    def test_an_example_asset_becomes_a_blob_url_under_docs(self, tmp_path):
        (tmp_path / "docs" / "examples" / "synthetic_co").mkdir(parents=True)
        (tmp_path / "docs" / "examples" / "synthetic_co" / "financials_input.json").write_text(
            "{}\n", encoding="utf-8"
        )
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[input](examples/synthetic_co/financials_input.json)\n",
        })
        assert f"({U}/blob/main/docs/examples/synthetic_co/financials_input.json)" in tree["source.md"]

    def test_an_example_page_becomes_a_flattened_wiki_url(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[brief](examples/synthetic_co/brief.md)\n",
            "examples/synthetic_co/brief.md": "# Brief\n",
        })
        assert f"({U}/wiki/examples-synthetic_co-brief)" in tree["source.md"]

    def test_a_relative_image_becomes_a_raw_blob_url(self, tmp_path):
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs" / "chart.png").write_bytes(b"\x89PNG\r\n")
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "![chart](chart.png)\n",
        })
        assert f"![chart]({U}/blob/main/docs/chart.png?raw=true)" in tree["source.md"]

    def test_an_external_link_is_unchanged(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[google](https://google.com) [mail](mailto:a@b.c)\n",
        })
        assert "[google](https://google.com) [mail](mailto:a@b.c)" in tree["source.md"]

    def test_links_inside_fenced_code_are_left_alone(self, tmp_path):
        fenced = "```\n[broken](gone.md) and ![img](missing.png)\n```"
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": f"# S\n\n{fenced}\n\n[ok](README.md)\n",
        })
        assert "[broken](gone.md)" in tree["source.md"]
        assert "![img](missing.png)" in tree["source.md"]

    def test_links_inside_inline_code_spans_are_left_alone(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "Use `[x](gone.md)` in prose.\n",
        })
        assert "`[x](gone.md)`" in tree["source.md"]

    def test_mermaid_blocks_and_tables_are_preserved_byte_for_byte(self, tmp_path):
        mermaid = '```mermaid\nflowchart LR\n  A[Start] --> B{"end<br/>x"}\n```'
        table = "| a | b |\n| :--- | :--- |\n| [x](README.md) | plain |"
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": f"# S\n\n{mermaid}\n\n{table}\n",
        })
        assert mermaid in tree["source.md"]
        assert f"| [x]({U}/wiki/Home) | plain |" in tree["source.md"]
        assert "| a | b |\n| :--- | :--- |" in tree["source.md"]

    def test_output_is_lf_only(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\r\n\r\n## S\r\n",
            "source.md": "# x\n",
        })
        assert "\r" not in tree["Home.md"]

    def test_the_same_tree_builds_identical_output(self, tmp_path):
        pages = {
            "README.md": "# Index\n\n## S\n\n## What is in this folder\n\n| Document | Contents |\n| :--- | :--- |\n| [source.md](source.md) | x |\n",
            "source.md": "[a](README.md#s) [b](source.md) [c](../README.md) [d](https://x.y)\n",
        }
        first = tmp_path / "one"
        second = tmp_path / "two"
        for root in (first, second):
            (root / "docs").mkdir(parents=True)
            (root / "README.md").write_text("# R\n", encoding="utf-8")
            for name, content in pages.items():
                (root / "docs" / name).write_text(content, encoding="utf-8")
        assert build_tree(first, "example/repo") == build_tree(second, "example/repo")

    def test_repeated_builds_are_identical(self, tmp_path):
        make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[x](README.md)\n",
        })
        first = wiki_publish.build_tree(tmp_path, "example/repo")
        second = wiki_publish.build_tree(tmp_path, "example/repo")
        assert first == second
