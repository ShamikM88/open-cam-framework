"""Tests for `scripts/wiki_publish.py` (issue #192: the generator and the publisher).

The generator turns the allowlisted `docs/` Markdown pages into a GitHub Wiki
page tree in an output directory: page names mapped, links rewritten to wiki
and repository URLs, a generated-source notice on every page, Home from the
docs index and `_Sidebar.md` from its reading-order table. The `--publish`
mode then clones a wiki repository, fully replaces its content with the
generated set and pushes one commit -- or none, when nothing changed.

The publish tests run entirely offline: a local bare git repository in
`tmp_path` (created on the default branch `master`, like the real wiki) stands
in for GitHub, so clone, commit and push run for real without any network,
any real token or anything outside the test's own temporary directory. The
generator tests build synthetic corpora in `tmp_path` and also run against
the real `docs/` tree of this repository.
"""
import base64
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

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

def _live_links(content: str) -> list[str]:
    """Every real link destination in a generated page: fenced code blocks are
    skipped (example links inside them are prose, not links), with the same
    fence rule the generator uses (tilde fences, longer fences)."""
    dests = []
    for _, line, in_fence in wiki_publish._lines_with_fences(content.split("\n")):
        if in_fence:
            continue
        code_spans = wiki_publish._code_span_ranges(line)
        for match in re.finditer(r"(!?)\[[^\]\n]*\]\(([^)\s]+)\)", line):
            if any(s <= match.start() < e for s, e in code_spans):
                continue  # a code span hides the link
            dests.append(match.group(2))
    return dests

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
    """Write a docs/ corpus and return the generated wiki tree (stub repo).

    Every top-level page (including the caller's README.md content, if any) is
    listed in the index table in sorted order, matching the repository's own
    index contract.
    """
    docs = tmp_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    top = sorted(name for name in pages if "/" not in name)
    read_me = pages.get("README.md", "# Index\n")
    table = "\n".join(f"| [{name[:-3]}.md]({name}) | x |" for name in top if name != "README.md")
    read_me += (
        "\n## What is in this folder\n\n| Document | Contents |\n| :--- | :--- |\n"
        + table + "\n"
    )
    for name, content in pages.items():
        target = docs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, so a \r\n fixture is written identically on every platform
        # (a text-mode write would translate newlines on Windows).
        target.write_bytes(content.encode("utf-8"))
    (docs / "README.md").write_bytes(read_me.encode("utf-8"))
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
        # README.md publishes as "Home", so docs/home.md collides with it
        # case-insensitively. Two files differing only by case cannot even
        # coexist on case-insensitive filesystems, so the collision is
        # created across these two names instead.
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=())
        (tmp_path / "docs" / "home.md").write_text("# x\n", encoding="utf-8")
        with pytest.raises(WikiPublishError, match="(?i)collide"):
            discover_pages(tmp_path)

    def test_a_page_named_like_the_generated_sidebar_is_an_error(self, tmp_path):
        # The generator writes _Sidebar.md itself; a docs page with that name
        # would be silently overwritten, so it is a reserved name.
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("_Sidebar",))
        (tmp_path / "docs" / "_Sidebar.md").write_text("# side\n", encoding="utf-8")
        with pytest.raises(WikiPublishError, match="reserved"):
            discover_pages(tmp_path)

    def test_a_symlinked_page_is_rejected(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("real",))
        (tmp_path / "docs" / "real.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs" / "linked.md").symlink_to(tmp_path / "docs" / "real.md")
        with pytest.raises(WikiPublishError, match="linked.md"):
            discover_pages(tmp_path)

    def test_a_symlinked_ancestor_directory_is_rejected(self, tmp_path):
        # The allowlist reaches docs/examples/synthetic_co/, so a symlinked
        # examples directory would read pages from outside the tracked tree.
        (tmp_path / "docs").mkdir()
        (tmp_path / "elsewhere" / "synthetic_co").mkdir(parents=True)
        (tmp_path / "elsewhere" / "synthetic_co" / "brief.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs" / "examples").symlink_to(tmp_path / "elsewhere")
        with pytest.raises(WikiPublishError, match="symlink"):
            discover_pages(tmp_path)

    def test_a_symlinked_docs_directory_is_rejected(self, tmp_path):
        # Defense in depth: the index-table walk already catches this, but the
        # discovery boundary must reject it on its own.
        (tmp_path / "real").mkdir()
        (tmp_path / "real" / "README.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs").symlink_to(tmp_path / "real")
        with pytest.raises(WikiPublishError, match="symlink"):
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

    def test_an_external_link_with_an_uppercase_scheme_is_unchanged(self, tmp_path):
        # URI schemes are case-insensitive in Markdown; the corpus happens to
        # use lowercase, but an upper-case scheme is still external, not a file.
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[docs](HTTPS://example.com/x) [mail](MAILTO:a@b.c)\n",
        })
        assert "[docs](HTTPS://example.com/x) [mail](MAILTO:a@b.c)" in tree["source.md"]

    def test_links_inside_fenced_code_are_left_alone(self, tmp_path):
        fenced = "```\n[broken](gone.md) and ![img](missing.png)\n```"
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": f"# S\n\n{fenced}\n\n[ok](README.md)\n",
        })
        assert "[broken](gone.md)" in tree["source.md"]
        assert "![img](missing.png)" in tree["source.md"]

    def test_code_spans_inside_link_text_are_still_rewritten(self, tmp_path):
        # docs/README.md links CLAUDE.md as [`CLAUDE.md`](../CLAUDE.md); the
        # backticks are link text, not a code span hiding the link.
        (tmp_path / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
        tree = make_corpus(tmp_path, {
            "README.md": "See [`CLAUDE.md`](../CLAUDE.md).\n",
            "source.md": "# x\n",
        })
        assert f"[`CLAUDE.md`]({U}/blob/main/CLAUDE.md)" in tree["Home.md"]

    def test_links_inside_inline_code_spans_are_left_alone(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "Use `[x](gone.md)` in prose.\n",
        })
        assert "`[x](gone.md)`" in tree["source.md"]

    def test_double_backtick_code_spans_hide_links(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "Use ``[gone](gone.md)`` in prose.\n",
        })
        assert "``[gone](gone.md)``" in tree["source.md"]

    def test_a_shorter_backtick_run_inside_a_longer_span_is_content(self, tmp_path):
        # ```a `` [gone](gone.md)``` is ONE code span: the inner double run is
        # content, not a closer, so the link inside stays code.
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "Use ```a `` [gone](gone.md)``` in prose.\n",
        })
        assert "```a `` [gone](gone.md)```" in tree["source.md"]

    def test_a_longer_closing_run_does_not_close_a_span(self, tmp_path):
        # ``x``` has no equal-length closer, so it is literal text and the
        # following link is live and rewritten.
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "``x``` [ok](README.md)\n",
        })
        assert f"[ok]({U}/wiki/Home)" in tree["source.md"]

    def test_tilde_fences_hide_links(self, tmp_path):
        fenced = "~~~\n[gone](gone.md)\n~~~"
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": f"# S\n\n{fenced}\n",
        })
        assert "[gone](gone.md)" in tree["source.md"]

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self, tmp_path):
        fenced = "````\n```\n[gone](gone.md)\n```\n````"
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": f"# S\n\n{fenced}\n",
        })
        assert "[gone](gone.md)" in tree["source.md"]

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


class TestValidationFailures:

    def test_a_missing_target_is_an_error(self, tmp_path):
        with pytest.raises(WikiPublishError, match=r"docs/source.md:5.*gone.md.*does not exist"):
            make_corpus(tmp_path, {
                "README.md": "# Index\n",
                "source.md": "# S\n\n## A\n\n[gone](gone.md)\n",
            })

    def test_a_bad_anchor_on_a_target_page_is_an_error(self, tmp_path):
        with pytest.raises(WikiPublishError, match=r"docs/source.md:1.*no heading anchor #nope"):
            make_corpus(tmp_path, {
                "README.md": "# Index\n\n## Real\n",
                "source.md": "[x](README.md#nope)\n",
            })

    def test_a_bad_same_page_anchor_is_an_error(self, tmp_path):
        with pytest.raises(WikiPublishError, match=r"docs/source.md:3.*no heading anchor"):
            make_corpus(tmp_path, {
                "README.md": "# Index\n",
                "source.md": "# S\n\n[x](#nope)\n",
            })

    def test_a_default_relative_root_still_contains_targets(self, tmp_path, monkeypatch):
        # The CLI defaults --root to '.', so containment must hold for a
        # relative root too: a link escaping the repository is an error even
        # when the escaped-to file exists.
        repo = tmp_path / "repo"
        (repo / "docs").mkdir(parents=True)
        write_index(repo, rows=("source",))
        (repo / "docs" / "source.md").write_text("[x](../../outside.md)\n", encoding="utf-8")
        (tmp_path / "outside.md").write_text("# o\n", encoding="utf-8")
        monkeypatch.chdir(repo)
        with pytest.raises(WikiPublishError, match="outside the repository"):
            build_tree(Path("."), "example/repo")

    def test_a_target_outside_the_repository_is_an_error(self, tmp_path):
        root = tmp_path / "repo"
        (root / "docs").mkdir(parents=True)
        (tmp_path / "outside.md").write_text("# x\n", encoding="utf-8")
        write_index(root, rows=("source",))
        (root / "docs" / "source.md").write_text("[x](../../outside.md)\n", encoding="utf-8")
        with pytest.raises(WikiPublishError, match="outside the repository"):
            build_tree(root, "example/repo")

    def test_traversal_beyond_the_root_is_an_error(self, tmp_path):
        root = tmp_path / "repo"
        (root / "docs").mkdir(parents=True)
        write_index(root, rows=("source",))
        (root / "docs" / "source.md").write_text("[x](../../../etc/passwd)\n", encoding="utf-8")
        with pytest.raises(WikiPublishError, match="outside the repository"):
            build_tree(root, "example/repo")

    def test_an_absolute_link_target_is_an_error(self, tmp_path):
        with pytest.raises(WikiPublishError, match="absolute link target"):
            make_corpus(tmp_path, {
                "README.md": "# Index\n",
                "source.md": "[x](/etc/passwd)\n",
            })

    def test_a_symlinked_link_target_is_an_error(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("source", "page"))
        (tmp_path / "real.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs" / "source.md").write_text("# S\n", encoding="utf-8")
        (tmp_path / "docs" / "linked.md").symlink_to(tmp_path / "real.md")
        (tmp_path / "docs" / "page.md").write_text("[x](linked.md)\n", encoding="utf-8")
        with pytest.raises(WikiPublishError, match="symlink"):
            build_tree(tmp_path, "example/repo")

    def test_a_page_that_is_not_utf8_is_an_error(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("source",))
        (tmp_path / "docs" / "source.md").write_bytes(b"# \xff\xfe bad\n")
        with pytest.raises(WikiPublishError, match="not valid UTF-8"):
            build_tree(tmp_path, "example/repo")

    def test_a_page_that_is_not_utf8_names_the_line(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("source",))
        (tmp_path / "docs" / "source.md").write_bytes(b"# ok\n\n\xff\xfe bad\n")
        with pytest.raises(WikiPublishError, match=r"docs/source.md:3.*not valid UTF-8"):
            build_tree(tmp_path, "example/repo")

    def test_a_missing_docs_index_is_an_error(self, tmp_path):
        # Home and the sidebar both come from docs/README.md; a corpus without
        # it must fail loudly, not crash. File-level errors name line 1.
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.md").write_text("# x\n", encoding="utf-8")
        with pytest.raises(WikiPublishError, match=r"docs/README.md:1:"):
            build_tree(tmp_path, "example/repo")

    def test_a_bad_repo_argument_is_an_error(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("source",))
        with pytest.raises(WikiPublishError, match="owner/name"):
            build_tree(tmp_path, "not a repo")

    def test_a_repo_with_an_empty_owner_or_name_is_an_error(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("source",))
        for repo in ("/repo", "owner/", "/", "a/b/c"):
            with pytest.raises(WikiPublishError, match="owner/name"):
                build_tree(tmp_path, repo)


class TestHomeAndSidebar:
    def test_home_is_the_transformed_index_with_blob_links(self, tmp_path):
        (tmp_path / "README.md").write_text("# Repo\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
        tree = make_corpus(tmp_path, {
            "README.md": "See [README](../README.md) and [CLAUDE](../CLAUDE.md).\n",
            "source.md": "# x\n",
        })
        assert f"[README]({U}/blob/main/README.md)" in tree["Home.md"]
        assert f"[CLAUDE]({U}/blob/main/CLAUDE.md)" in tree["Home.md"]
        assert tree["Home.md"].startswith(notice_for("docs/README.md"))

    def test_the_sidebar_lists_home_first_then_the_table_order(self, tmp_path):
        # The index table lists zeta BEFORE alpha on purpose: the sidebar must
        # follow the table's stated order, not the page-name alphabet.
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text(
            "# Index\n\n## What is in this folder\n\n"
            "| Document | Contents |\n| :--- | :--- |\n"
            "| [zeta.md](zeta.md) | x |\n| [alpha.md](alpha.md) | x |\n",
            encoding="utf-8",
        )
        (tmp_path / "docs" / "zeta.md").write_text("# z\n", encoding="utf-8")
        (tmp_path / "docs" / "alpha.md").write_text("# a\n", encoding="utf-8")
        tree = build_tree(tmp_path, "example/repo")
        lines = tree["_Sidebar.md"].split("\n")
        assert lines[0] == notice_for("docs/README.md")
        entries = [line for line in lines if line.startswith("- [")]
        assert entries == [
            f"- [Home]({U}/wiki/Home)",
            f"- [zeta]({U}/wiki/zeta)",
            f"- [alpha]({U}/wiki/alpha)",
        ]

    def test_a_missing_index_table_is_an_error(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text("# Index with no table\n", encoding="utf-8")
        (tmp_path / "docs" / "source.md").write_text("# x\n", encoding="utf-8")
        with pytest.raises(WikiPublishError, match="What is in this folder"):
            build_tree(tmp_path, "example/repo")

    def test_a_top_level_page_missing_from_the_table_is_an_error(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("listed",))
        (tmp_path / "docs" / "listed.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs" / "unlisted.md").write_text("# x\n", encoding="utf-8")
        with pytest.raises(WikiPublishError, match="unlisted"):
            build_tree(tmp_path, "example/repo")

    def test_example_pages_need_not_be_in_the_table(self, tmp_path):
        # The docs index table lists the top-level pages only; the two example
        # pages are published (linked from other pages) but are deliberately
        # not index rows, matching the repository's own index contract.
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[brief](examples/synthetic_co/brief.md)\n",
            "examples/synthetic_co/brief.md": "# Brief\n",
        })
        assert "examples-synthetic_co-brief.md" in tree
        assert "examples-synthetic_co-brief" not in tree["_Sidebar.md"]

    def test_a_table_row_that_is_not_a_page_is_an_error(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("source",))
        (tmp_path / "docs" / "source.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs" / "README.md").write_text(
            "# Index\n\n## What is in this folder\n\n"
            "| Document | Contents |\n| :--- | :--- |\n| [gone](gone.md) | x |\n",
            encoding="utf-8",
        )
        with pytest.raises(WikiPublishError, match="gone.md"):
            build_tree(tmp_path, "example/repo")


class TestCli:
    def run(self, argv):
        return wiki_publish.main(argv)

    def test_check_builds_in_memory_and_writes_nothing(self, tmp_path, capsys):
        make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# S\n",
        })
        (tmp_path / "out").mkdir()
        code = self.run(["--repo", "example/repo", "--root", str(tmp_path), "--check"])
        assert code == 0
        assert not list((tmp_path / "out").iterdir())
        summary = capsys.readouterr().out.strip()
        assert "\n" not in summary
        assert "3 pages" in summary  # Home, source, _Sidebar

    def test_out_writes_the_tree(self, tmp_path):
        make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# S\n",
        })
        code = self.run(["--repo", "example/repo", "--root", str(tmp_path),
                         "--out", str(tmp_path / "out")])
        assert code == 0
        written = sorted(p.name for p in (tmp_path / "out").iterdir())
        assert written == ["Home.md", "_Sidebar.md", "source.md"]

    def test_out_replaces_stale_wiki_files(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "stale.md").write_text("old\n", encoding="utf-8")
        (out / "notes.txt").write_text("kept\n", encoding="utf-8")
        make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# S\n",
        })
        code = self.run(["--repo", "example/repo", "--root", str(tmp_path), "--out", str(out)])
        assert code == 0
        assert not (out / "stale.md").exists()
        assert (out / "notes.txt").exists()

    def test_a_file_system_failure_is_one_error_line(self, tmp_path, capsys):
        # --out pointing at a regular file cannot be written to; the failure
        # must be one error line and exit 1, not a traceback.
        make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# S\n",
        })
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, not a directory\n", encoding="utf-8")
        code = self.run(["--repo", "example/repo", "--root", str(tmp_path),
                         "--out", str(blocker)])
        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert captured.err.count("error:") == 1
        assert "traceback" not in captured.err.lower()

    def test_a_validation_failure_writes_nothing_and_prints_one_error_line(self, tmp_path, capsys):
        out = tmp_path / "out"
        out.mkdir()
        (out / "existing.md").write_text("keep\n", encoding="utf-8")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text("# Index\n", encoding="utf-8")
        (tmp_path / "docs" / "source.md").write_text("[gone](gone.md)\n", encoding="utf-8")
        code = self.run(["--repo", "example/repo", "--root", str(tmp_path), "--out", str(out)])
        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert captured.err.count("error:") == 1
        assert "gone.md" in captured.err
        assert "traceback" not in captured.err.lower()
        assert (out / "existing.md").read_text(encoding="utf-8") == "keep\n"

    def test_a_bad_repo_format_is_one_error_line(self, tmp_path, capsys):
        code = self.run(["--repo", "not-a-repo", "--root", str(tmp_path), "--check"])
        assert code == 1
        assert "owner/name" in capsys.readouterr().err

    def test_out_and_check_are_mutually_exclusive(self, tmp_path):
        with pytest.raises(SystemExit) as raised:
            self.run(["--repo", "example/repo", "--root", str(tmp_path),
                      "--check", "--out", str(tmp_path / "out")])
        assert raised.value.code == 2

    def test_publish_requires_source_sha(self, tmp_path):
        with pytest.raises(SystemExit) as raised:
            self.run(["--repo", "example/repo", "--root", str(tmp_path), "--publish"])
        assert raised.value.code == 2

    def test_remote_requires_publish(self, tmp_path):
        with pytest.raises(SystemExit) as raised:
            self.run(["--repo", "example/repo", "--root", str(tmp_path),
                      "--check", "--remote", str(tmp_path / "r.git")])
        assert raised.value.code == 2

    def test_repo_is_required(self, tmp_path):
        with pytest.raises(SystemExit) as raised:
            self.run(["--root", str(tmp_path), "--check"])
        assert raised.value.code == 2


class TestPublish:
    """The `--publish` mode against a local bare repository standing in for the wiki.

    The remote is a bare git repository created on `master` (the real wiki's
    default branch) with one placeholder commit, exactly the situation the
    maintainer's one-time setup left behind. `--remote` points the publisher
    at it, so no token and no network are involved; the git operations run
    for real.
    """

    SHA1 = "1" * 40
    SHA2 = "2" * 40
    SENTINEL = "ghs_leak_canary_9f1e"

    def _git(self, cwd, *args):
        """Run git with a fixed identity and no inherited git configuration."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(
            ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
             "-c", "commit.gpgsign=false", *args],
            cwd=cwd, check=True, capture_output=True, text=True, env=env, timeout=60)

    def bare_remote(self, tmp_path):
        """A bare repository standing in for the wiki: default branch `master`, one placeholder commit."""
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "Home.md").write_text("placeholder\n", encoding="utf-8")
        self._git(seed, "-c", "init.defaultBranch=master", "init", "-q")
        self._git(seed, "add", "Home.md")
        self._git(seed, "commit", "-q", "-m", "Initial Home page")
        remote = tmp_path / "wiki.git"
        self._git(tmp_path, "clone", "--quiet", "--bare", str(seed), str(remote))
        shutil.rmtree(seed)
        return remote

    def remote_files(self, remote):
        return self._git(None, "--git-dir", str(remote),
                         "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()

    def remote_head(self, remote):
        return self._git(None, "--git-dir", str(remote), "rev-parse", "HEAD").stdout.strip()

    def remote_commit_count(self, remote):
        return int(self._git(None, "--git-dir", str(remote),
                             "rev-list", "--count", "HEAD").stdout.strip())

    def publish(self, tmp_path, remote, sha=SHA1):
        return wiki_publish.main([
            "--repo", "example/repo", "--root", str(tmp_path), "--publish",
            "--source-sha", sha, "--remote", str(remote),
        ])

    def test_the_first_publish_creates_every_page(self, tmp_path):
        remote = self.bare_remote(tmp_path)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        assert self.publish(tmp_path, remote) == 0
        files = self.remote_files(remote)
        assert "Home.md" in files and "source.md" in files and "_Sidebar.md" in files
        # the placeholder is replaced, not kept: the published Home is the generated one
        home = self._git(None, "--git-dir", str(remote), "show", "HEAD:Home.md").stdout
        assert home.startswith(notice_for("docs/README.md"))
        assert "placeholder" not in home

    def test_an_identical_republish_makes_no_new_commit(self, tmp_path, capsys):
        remote = self.bare_remote(tmp_path)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        assert self.publish(tmp_path, remote, sha=self.SHA1) == 0
        head, count = self.remote_head(remote), self.remote_commit_count(remote)
        capsys.readouterr()

        for sha in (self.SHA1, self.SHA2):  # same sha, then a different one: identical docs, no commit
            assert self.publish(tmp_path, remote, sha=sha) == 0
            captured = capsys.readouterr()
            assert "no change" in captured.out
            assert self.remote_head(remote) == head
            assert self.remote_commit_count(remote) == count

    def test_a_changed_page_changes_only_its_page(self, tmp_path):
        remote = self.bare_remote(tmp_path)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n", "other.md": "# O\n"})
        assert self.publish(tmp_path, remote) == 0
        (tmp_path / "docs" / "source.md").write_text("# S\n\nChanged.\n", encoding="utf-8")
        assert self.publish(tmp_path, remote, sha=self.SHA2) == 0
        changed = self._git(None, "--git-dir", str(remote),
                            "diff", "--name-only", "HEAD~1", "HEAD").stdout.split()
        assert changed == ["source.md"]

    def test_a_page_removed_from_docs_is_removed_from_the_wiki(self, tmp_path):
        remote = self.bare_remote(tmp_path)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        assert self.publish(tmp_path, remote) == 0
        assert "source.md" in self.remote_files(remote)
        (tmp_path / "docs" / "source.md").unlink()
        (tmp_path / "docs" / "README.md").write_text(
            "# Index\n\n## What is in this folder\n\n| Document | Contents |\n| :--- | :--- |\n",
            encoding="utf-8")
        assert self.publish(tmp_path, remote, sha=self.SHA2) == 0
        assert "source.md" not in self.remote_files(remote)
        assert "Home.md" in self.remote_files(remote)  # the rest is untouched

    def test_the_commit_message_and_trailer_carry_the_source_sha(self, tmp_path):
        remote = self.bare_remote(tmp_path)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        assert self.publish(tmp_path, remote, sha=self.SHA1) == 0
        message = self._git(None, "--git-dir", str(remote), "log", "-1", "--format=%B").stdout
        assert message.rstrip("\n") == f"Publish docs from main {self.SHA1}\n\ndocs-source: {self.SHA1}"

    def test_the_remote_default_branch_name_is_preserved(self, tmp_path):
        remote = self.bare_remote(tmp_path)  # created on master, like the real wiki
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        assert self.publish(tmp_path, remote) == 0
        assert self._git(None, "--git-dir", str(remote),
                         "symbolic-ref", "--short", "HEAD").stdout.strip() == "master"
        # the publish landed on master, not on an assumed main
        assert self._git(None, "--git-dir", str(remote),
                         "rev-parse", "master").stdout.strip() == self.remote_head(remote)
        assert self.remote_commit_count(remote) == 2  # placeholder + publish

    def test_a_sentinel_token_never_appears_in_output_or_any_file(self, tmp_path, tmp_path_factory,
                                                                    monkeypatch, capsys):
        remote = self.bare_remote(tmp_path)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        monkeypatch.setenv("GITHUB_TOKEN", self.SENTINEL)
        # a work directory OUTSIDE the repository root (the containment guard
        # refuses one inside it), kept so every file can be scanned
        work_dir = tmp_path_factory.mktemp("publish-work")
        monkeypatch.setattr(wiki_publish.tempfile, "mkdtemp", lambda *a, **k: str(work_dir))
        monkeypatch.setattr(wiki_publish.shutil, "rmtree", lambda path, **k: None)

        assert self.publish(tmp_path, remote) == 0

        captured = capsys.readouterr()
        assert self.SENTINEL not in captured.out and self.SENTINEL not in captured.err
        encoded = base64.b64encode(self.SENTINEL.encode("utf-8"))
        for path in list(tmp_path.rglob("*")) + list(work_dir.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                assert self.SENTINEL.encode("utf-8") not in data, path
                assert encoded not in data, path

    def test_a_generator_error_pushes_nothing(self, tmp_path, capsys):
        remote = self.bare_remote(tmp_path)
        head, count = self.remote_head(remote), self.remote_commit_count(remote)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text("# Index\n", encoding="utf-8")
        (tmp_path / "docs" / "source.md").write_text("[gone](gone.md)\n", encoding="utf-8")

        assert self.publish(tmp_path, remote) == 1

        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert captured.err.count("error:") == 1
        assert "gone.md" in captured.err
        assert self.remote_head(remote) == head  # nothing was pushed
        assert self.remote_commit_count(remote) == count

    def test_a_failed_push_exits_1_with_one_error_line(self, tmp_path, capsys):
        # A non-bare remote with its branch checked out refuses pushes: the clone
        # succeeds and the push is rejected, which is the failure being asserted.
        remote = self.bare_remote(tmp_path)
        work = tmp_path / "worktree"
        self._git(tmp_path, "clone", "--quiet", str(remote), str(work))
        # a non-bare repository with its branch checked out refuses pushes
        self._git(work, "config", "receive.denyCurrentBranch", "refuse")
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})

        code = self.publish(tmp_path, work)

        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert captured.err.count("error:") == 1
        assert captured.err.startswith("error:")
        assert "push" in captured.err
        assert "Traceback" not in captured.err

    def test_an_unreachable_remote_exits_1_with_one_error_line(self, tmp_path, capsys):
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        code = self.publish(tmp_path, tmp_path / "no" / "such" / "repo.git")
        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert captured.err.count("error:") == 1
        assert "cloning" in captured.err
        assert "Traceback" not in captured.err

    def test_a_missing_token_with_a_real_remote_is_an_error_before_any_git_runs(self, tmp_path, capsys, monkeypatch):
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        calls = []

        def fail(argv, **kwargs):
            calls.append(argv)
            raise AssertionError("git must not run when the token is missing")

        monkeypatch.setattr(wiki_publish.subprocess, "run", fail)
        code = wiki_publish.main([
            "--repo", "example/repo", "--root", str(tmp_path), "--publish", "--source-sha", self.SHA1])
        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert "GITHUB_TOKEN" in captured.err
        assert not calls

    def test_the_token_reaches_git_only_as_an_http_header(self, tmp_path, monkeypatch):
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_header_test_token")
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 128, "", "fatal: simulated failure")

        monkeypatch.setattr(wiki_publish.subprocess, "run", fake_run)
        code = wiki_publish.main([
            "--repo", "example/repo", "--root", str(tmp_path), "--publish", "--source-sha", self.SHA1])
        assert code == 1  # the simulated clone failure
        argv, kwargs = calls[0]
        header = base64.b64encode(b"x-access-token:ghs_header_test_token").decode("ascii")
        assert f"http.extraheader=Authorization: Basic {header}" in argv
        assert "https://github.com/example/repo.wiki.git" in argv
        assert not any("ghs_header_test_token" in str(part) for part in argv)  # never the raw token
        assert kwargs["capture_output"] is True  # git's own output is captured, never passed through
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"

    def test_a_git_timeout_is_one_sanitized_error_line(self, tmp_path, capsys, monkeypatch):
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})

        def slow(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 300)

        monkeypatch.setattr(wiki_publish.subprocess, "run", slow)
        code = wiki_publish.main([
            "--repo", "example/repo", "--root", str(tmp_path), "--publish",
            "--source-sha", self.SHA1, "--remote", str(tmp_path / "r.git")])
        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert captured.err.startswith("error:")
        assert "timed out" in captured.err
        assert "ghs" not in captured.err and "extraheader" not in captured.err

    def test_a_work_dir_inside_the_repository_is_refused(self, tmp_path, monkeypatch, capsys):
        remote = self.bare_remote(tmp_path)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        # TMPDIR pointing inside the repository: the clone must not land there
        monkeypatch.setattr(wiki_publish.tempfile, "mkdtemp",
                            lambda *a, **k: str(tmp_path / "docs" / "tmp-work"))
        code = self.publish(tmp_path, remote)
        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert captured.err.count("error:") == 1
        assert "temporary" in captured.err

    def test_an_empty_wiki_repository_is_published_too(self, tmp_path):
        remote = tmp_path / "empty.git"
        self._git(tmp_path, "-c", "init.defaultBranch=master", "init", "--bare", "-q", str(remote))
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        assert self.publish(tmp_path, remote) == 0
        files = self.remote_files(remote)
        assert "Home.md" in files and "source.md" in files and "_Sidebar.md" in files

    @pytest.mark.parametrize("failing,expected", [
        (("symbolic-ref",), "no branch to publish to"),
        (("ls-files",), "listing the wiki repository's files failed"),
        (("rm",), "removing the stale wiki file"),
        (("add",), "staging the generated page"),
        (("commit",), "committing the generated wiki pages failed"),
    ])
    def test_git_operation_failures_are_sanitized_error_lines(self, tmp_path, monkeypatch, capsys,
                                                             failing, expected):
        """Every git operation can fail; each failure is one error line naming the operation,
        never the command line, the token or git's raw output."""
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})

        def fake_run_git(args, cwd=None, token=None):
            index = 0
            while args[index] == "-c":  # skip the -c <value> pairs before the subcommand
                index += 2
            head = args[index]
            if head == "clone":
                return subprocess.CompletedProcess(args, 0, "", "")
            if head == failing[0]:
                return subprocess.CompletedProcess(args, 1, "", "simulated failure")
            if head == "symbolic-ref":
                return subprocess.CompletedProcess(args, 0, "master\n", "")
            if head == "ls-files":
                return subprocess.CompletedProcess(args, 0, "stale.md\n", "")
            if head == "diff":
                return subprocess.CompletedProcess(args, 1, "", "")  # differences exist
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(wiki_publish, "_run_git", fake_run_git)
        code = self.publish(tmp_path, "unused-remote")

        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert captured.err.count("error:") == 1
        assert expected in captured.err
        assert "simulated failure" not in captured.err  # git's raw output stays out


class TestRealCorpus:
    def test_the_real_docs_tree_builds_and_every_link_is_allowed(self):
        tree = build_tree(REPO_ROOT, "example/repo")
        # 22 top-level pages (README becomes Home) + 2 example pages + sidebar.
        assert len(tree) == 25
        allowed = re.compile(r"^(https://github\.com/example/repo/(wiki|blob|tree)/|https?://|#|mailto:)")
        for name, content in tree.items():
            for dest in _live_links(content):
                assert allowed.match(dest), f"{name}: unexpected link destination {dest!r}"

    def test_the_real_home_links_the_repository_readme_and_claude(self):
        tree = build_tree(REPO_ROOT, "example/repo")
        assert "(https://github.com/example/repo/blob/main/README.md)" in tree["Home.md"]
        assert "(https://github.com/example/repo/blob/main/CLAUDE.md)" in tree["Home.md"]


class TestSlugRuleMatchesTestDocs:
    def test_the_anchor_rule_is_the_one_test_docs_pins(self):
        import test_docs

        headings = [
            "What `config/settings.json` actually controls",
            "Context Window & State Management Protocol",
            "Mutation testing",
            "D1: The Maker and the Checker are independent prompts",
            "A heading with an [inline link](x.md) inside",
            "Ünïcödé and  spaces  ",
        ]
        for heading in headings:
            assert wiki_publish.slug(heading) == test_docs.slug(heading)
        sample = "# A\n\ntext\n\n## Same\n\n## Same\n\n```md\n### Fenced, ignored\n```\n\n# A\n"
        assert wiki_publish.heading_slugs(sample) == test_docs.heading_slugs(sample)


class TestEdgeBranches:
    def test_the_page_name_guard_rejects_non_allowlisted_paths(self):
        with pytest.raises(WikiPublishError, match="not an allowlisted"):
            wiki_publish._page_name("scripts/tool.py")

    def test_a_missing_docs_directory_is_an_error(self, tmp_path):
        with pytest.raises(WikiPublishError, match="no docs/ directory"):
            build_tree(tmp_path, "example/repo")

    def test_an_empty_docs_directory_is_an_error(self, tmp_path):
        (tmp_path / "docs").mkdir()
        with pytest.raises(WikiPublishError, match="no Markdown pages"):
            build_tree(tmp_path, "example/repo")

    def test_a_directory_named_like_a_page_is_ignored(self, tmp_path):
        (tmp_path / "docs" / "weird.md").mkdir(parents=True)
        write_index(tmp_path, rows=())
        tree = build_tree(tmp_path, "example/repo")
        assert sorted(tree) == ["Home.md", "_Sidebar.md"]

    def test_a_symlinked_repository_file_target_is_an_error(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "real_tool.py").write_text("", encoding="utf-8")
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("source",))
        (tmp_path / "docs" / "source.md").write_text("[x](../scripts/tool.py)\n", encoding="utf-8")
        (tmp_path / "scripts" / "tool.py").symlink_to(tmp_path / "real_tool.py")
        with pytest.raises(WikiPublishError, match="symlink"):
            build_tree(tmp_path, "example/repo")

    def test_a_table_row_without_a_link_is_an_error(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write_index(tmp_path, rows=("source",))
        (tmp_path / "docs" / "source.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs" / "README.md").write_text(
            "# Index\n\n## What is in this folder\n\n"
            "| Document | Contents |\n| :--- | :--- |\n| [source.md](source.md) | x |\n| plain row | x |\n",
            encoding="utf-8",
        )
        with pytest.raises(WikiPublishError, match="no page link"):
            build_tree(tmp_path, "example/repo")

    def test_a_table_row_pointing_at_a_non_page_is_an_error(self, tmp_path):
        (tmp_path / "README.md").write_text("# Repo\n", encoding="utf-8")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "source.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs" / "README.md").write_text(
            "# Index\n\n## What is in this folder\n\n"
            "| Document | Contents |\n| :--- | :--- |\n| [repo README](../README.md) | x |\n",
            encoding="utf-8",
        )
        with pytest.raises(WikiPublishError, match="not a published page"):
            build_tree(tmp_path, "example/repo")

    def test_prose_after_the_table_ends_it_and_a_new_heading_aborts_the_search(self, tmp_path):
        # A table followed by prose stops the row scan; a heading before any
        # table aborts it with the missing-table error.
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "source.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "docs" / "README.md").write_text(
            "# Index\n\n## What is in this folder\n\n"
            "| Document | Contents |\n| :--- | :--- |\n| [source.md](source.md) | x |\n\n"
            "Trailing prose.\n\n## Where each kind of fact is authoritative\n\n| a | b |\n",
            encoding="utf-8",
        )
        assert "_Sidebar.md" in build_tree(tmp_path, "example/repo")
        (tmp_path / "docs" / "README.md").write_text(
            "# Index\n\n## What is in this folder\n\nProse, then a new section.\n\n"
            "## Where each kind of fact is authoritative\n\n| a | b |\n",
            encoding="utf-8",
        )
        with pytest.raises(WikiPublishError, match="no 'What is in this folder' index table"):
            build_tree(tmp_path, "example/repo")


class TestOutputPathSafety:
    """CG358-1 / PX358-1: --out must never overlap the source tree, or the generator would overwrite or delete the
    canonical documentation."""

    def corpus(self, tmp_path):
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        (tmp_path / "docs" / "examples" / "synthetic_co").mkdir(parents=True, exist_ok=True)
        return {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    @pytest.mark.parametrize("relative_out", [
        ".", "docs", "docs/examples", "docs/examples/synthetic_co", "docs/../docs", "..",
    ], ids=["root", "docs", "docs-examples", "docs-examples-synthetic_co", "docs-dotdot", "parent-of-root"])
    def test_an_output_path_that_overlaps_the_source_tree_is_refused_and_writes_nothing(
            self, tmp_path, capsys, relative_out):
        root = tmp_path / "repo"
        root.mkdir()
        before = self.corpus(root)
        everything_before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

        code = wiki_publish.main(["--repo", "example/repo", "--root", str(root),
                                  "--out", str(root / relative_out)])

        assert code == 1
        err = capsys.readouterr().err
        assert len(err.splitlines()) == 1 and err.startswith("error: ") and "output" in err
        assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
        assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == everything_before

    def test_a_relative_output_path_is_resolved_before_the_overlap_check(self, tmp_path, monkeypatch, capsys):
        before = self.corpus(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert wiki_publish.main(["--repo", "example/repo", "--root", ".", "--out", "docs"]) == 1
        assert wiki_publish.main(["--repo", "example/repo", "--root", ".", "--out", "."]) == 1
        capsys.readouterr()
        assert {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before

    def test_a_separate_output_directory_inside_the_repository_still_works(self, tmp_path):
        self.corpus(tmp_path)
        assert wiki_publish.main(["--repo", "example/repo", "--root", str(tmp_path),
                                  "--out", str(tmp_path / "build" / "wiki")]) == 0
        assert (tmp_path / "build" / "wiki" / "Home.md").is_file()


def test_dotfiles_in_docs_are_not_discovered_as_pages(tmp_path):
    """PX358-2: an editor or draft artifact must not demand an index row."""
    make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
    (tmp_path / "docs" / ".draft.md").write_text("# draft\n", encoding="utf-8")
    assert ".draft" not in discover_pages(tmp_path)
    assert build_tree(tmp_path, "example/repo")
