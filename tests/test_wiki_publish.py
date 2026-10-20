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
    fence rule the generator uses (tilde fences, longer fences) and the same
    block-wide code-span pairing (`_code_span_intervals`)."""
    lines = content.split("\n")
    code_spans = wiki_publish._code_span_intervals(lines)
    dests = []
    for line_no, line, in_fence in wiki_publish._lines_with_fences(lines):
        if in_fence:
            continue
        spans = code_spans.get(line_no - 1, [])
        for match in re.finditer(r"(!?)\[[^\]\n]*\]\(([^)\s]+)\)", line):
            if any(s <= match.start() < e for s, e in spans):
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

    def test_a_link_inside_a_multiline_code_span_is_left_alone(self, tmp_path):
        # PX192-1 / PX358-3: a code span may open and close on different lines
        # (CommonMark pairs a backtick string with the next backtick string of
        # equal length in the same block, across soft line breaks); a link
        # inside it is code, not a link. The per-line span detection the
        # generator used left such a link live.
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": (
                "# S\n\nRun `python scripts/check.py --flag\n"
                "value [flag](README.md)` as a step.\n\n[ok](README.md)\n"
            ),
        })
        assert "value [flag](README.md)`" in tree["source.md"]

    def test_a_link_after_a_multiline_code_span_is_still_rewritten(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# S\n\n`opens here\nand closes` [ok](README.md)\n",
        })
        assert f"[ok]({U}/wiki/Home)" in tree["source.md"]

    def test_a_multiline_code_span_in_a_list_item_hides_a_link(self, tmp_path):
        # The corpus's real cases (commands.md, cli-reference.md): the span is
        # a continuation of a bullet item's text, and the link sits before the
        # closing backtick on the span's closing line.
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": (
                "# S\n\n- run `python scripts/spread.py --company <name>\n"
                "  --proposal <name> [opt](README.md)` as a Bash step\n"
            ),
        })
        assert "[opt](README.md)`" in tree["source.md"]

    def test_a_multiline_code_span_across_a_table_row_boundary_is_not_paired(
        self, tmp_path
    ):
        # Table cells are separate inline contexts: a dangling opener in one
        # row must not pair with a closer in the next and hide the next row's
        # live link.
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": (
                "# S\n\n| `dangling\n| [x](README.md) closer`\n"
            ),
        })
        assert f"[x]({U}/wiki/Home)" in tree["source.md"]

    def test_a_heading_is_its_own_block_not_paired_with_the_next_paragraph(
        self, tmp_path
    ):
        # A heading's dangling backtick must not pair with a run in the
        # paragraph below it and hide that paragraph's live link.
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": (
                "# Heading with `tick\nA paragraph [link](README.md) and `tock\n"
            ),
        })
        assert f"[link]({U}/wiki/Home)" in tree["source.md"]

    def test_a_table_row_is_its_own_block_not_paired_with_the_next_line(
        self, tmp_path
    ):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": (
                "# S\n\n| row with `tick\nThen [link](README.md) and `tock\n"
            ),
        })
        assert f"[link]({U}/wiki/Home)" in tree["source.md"]

    def test_a_quote_after_a_paragraph_is_its_own_block(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": (
                "# S\n\nA paragraph with `tick\n> a quote with [link](README.md) and `tock\n"
            ),
        })
        assert f"[link]({U}/wiki/Home)" in tree["source.md"]

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

    def test_a_backtick_run_inside_a_closed_span_does_not_open_another_span(self, tmp_path):
        # CommonMark resumes scanning after a span's closing run, so a shorter
        # run inside the span is content and never an opener: the lone backtick
        # later in the paragraph is literal, and the link between is live.
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# S\n\n`` a ` b `` then [ok](README.md) and a lone ` tick\n",
        })
        assert f"[ok]({U}/wiki/Home)" in tree["source.md"]
        spans = wiki_publish._code_span_intervals(["`` a ` b `` then [ok](x) and a lone ` tick"])
        assert spans == {0: [(0, 11)]}

    def test_a_fence_indented_four_or_more_spaces_fails_the_build(self, tmp_path):
        # PX358-3: a fence indented four or more spaces is an indented code
        # block in CommonMark, not a fence. The generator supports only the
        # Markdown subset the corpus uses and fails closed on this construct:
        # misdetecting it would toggle the fence state and wrongly expose or
        # hide links around it.
        with pytest.raises(WikiPublishError, match="indented 4 or more spaces"):
            make_corpus(tmp_path, {
                "README.md": "# Index\n",
                "source.md": "# S\n\n    ```\n    [code](README.md)\n    ```\n",
            })

    def test_a_fence_indented_three_spaces_is_still_a_fence(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# S\n\n   ```\n   [code](README.md)\n   ```\n",
        })
        assert "[code](README.md)" in tree["source.md"]

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
        # follow the table's stated order, not the page-name alphabet. Each
        # label is the page's own first heading (decision comment 6278039251):
        # zeta's is "The zeta guide", alpha's "The alpha guide".
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text(
            "# Index\n\n## What is in this folder\n\n"
            "| Document | Contents |\n| :--- | :--- |\n"
            "| [zeta.md](zeta.md) | x |\n| [alpha.md](alpha.md) | x |\n",
            encoding="utf-8",
        )
        (tmp_path / "docs" / "zeta.md").write_text("# The zeta guide\n", encoding="utf-8")
        (tmp_path / "docs" / "alpha.md").write_text("# The alpha guide\n", encoding="utf-8")
        tree = build_tree(tmp_path, "example/repo")
        lines = tree["_Sidebar.md"].split("\n")
        assert lines[0] == notice_for("docs/README.md")
        entries = [line for line in lines if line.startswith("- [")]
        assert entries == [
            f"- [Home]({U}/wiki/Home)",
            f"- [The zeta guide]({U}/wiki/zeta)",
            f"- [The alpha guide]({U}/wiki/alpha)",
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
        # pages are published (linked from other pages) and now close the
        # sidebar as an Examples group, but are deliberately still not index
        # rows in docs/README.md, matching the repository's own index contract.
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[brief](examples/synthetic_co/brief.md)\n",
            "examples/synthetic_co/brief.md": "# Research Brief - Synthetic Co\n",
        })
        assert "examples-synthetic_co-brief.md" in tree
        # the Examples group lists the page; the index table never did
        assert f"[Research Brief - Synthetic Co]({U}/wiki/examples-synthetic_co-brief)" \
            in tree["_Sidebar.md"]
        table_rows = [line for line in tree["Home.md"].split("\n") if line.startswith("|")]
        assert not any("brief" in row for row in table_rows)

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


class TestSidebarLabels:
    """PX192-2 (decision comment 6278039251): every sidebar label is the page's
    OWN first heading -- the first ATX level-1 heading line outside any fenced
    code block in the source docs/*.md text, trailing # and surrounding
    whitespace removed, inline markup reduced to plain text, whitespace
    collapsed, Markdown-significant characters escaped -- never a hard-coded
    name. A page with no first heading falls back to its file name; Home keeps
    its label; link targets and order are unchanged."""

    def sidebar_entries(self, tree):
        return [line for line in tree["_Sidebar.md"].split("\n") if line.startswith("- [")]

    def test_labels_come_from_each_pages_own_first_heading(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# A slash command reference\n",
            "other.md": "# Outputs and templates\n",
        })
        assert self.sidebar_entries(tree) == [
            f"- [Home]({U}/wiki/Home)",
            f"- [Outputs and templates]({U}/wiki/other)",
            f"- [A slash command reference]({U}/wiki/source)",
        ]

    def test_a_heading_inside_a_fenced_code_block_is_not_a_first_heading(self, tmp_path):
        # the only level-1 heading line sits inside a fenced code block, so the
        # page has no first heading and falls back to its file name
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "## Level two only\n\n```\n# Not a heading\n```\n",
        })
        assert self.sidebar_entries(tree) == [
            f"- [Home]({U}/wiki/Home)",
            f"- [source]({U}/wiki/source)",
        ]

    def test_a_page_with_no_first_heading_falls_back_to_its_file_name(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "## Only a level-two heading\n",
        })
        assert self.sidebar_entries(tree) == [
            f"- [Home]({U}/wiki/Home)",
            f"- [source]({U}/wiki/source)",
        ]

    def test_inline_markup_and_closing_hashes_reduce_to_plain_text(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# The `spreading` **workbook** ##\n",
            "other.md": "# See [the docs](README.md)\n",
        })
        assert self.sidebar_entries(tree) == [
            f"- [Home]({U}/wiki/Home)",
            f"- [See the docs]({U}/wiki/other)",
            f"- [The spreading workbook]({U}/wiki/source)",
        ]

    def test_markdown_significant_characters_are_escaped(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "# A [bracketed] label\n",
        })
        assert self.sidebar_entries(tree) == [
            f"- [Home]({U}/wiki/Home)",
            f"- [A \\[bracketed\\] label]({U}/wiki/source)",
        ]

    def test_home_keeps_its_label_whatever_the_index_heading(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# The full documentation index\n",
            "source.md": "# S\n",
        })
        assert self.sidebar_entries(tree)[0] == f"- [Home]({U}/wiki/Home)"

    def test_link_targets_and_order_are_unchanged(self, tmp_path):
        # the table lists source BEFORE other on purpose: whatever the labels,
        # the entries' targets stay the page names in the table's order
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text(
            "# Index\n\n## What is in this folder\n\n"
            "| Document | Contents |\n| :--- | :--- |\n"
            "| [source.md](source.md) | x |\n| [other.md](other.md) | x |\n",
            encoding="utf-8",
        )
        (tmp_path / "docs" / "source.md").write_text("# Second label\n", encoding="utf-8")
        (tmp_path / "docs" / "other.md").write_text("# First label\n", encoding="utf-8")
        tree = build_tree(tmp_path, "example/repo")
        assert [entry.rsplit("(", 1)[1] for entry in self.sidebar_entries(tree)] == [
            f"{U}/wiki/Home)",
            f"{U}/wiki/source)",
            f"{U}/wiki/other)",
        ]


class TestSidebarExamplesGroup:
    """PX192-3 (decision comment 6278039251): the two pages published from
    docs/examples/synthetic_co/*.md close the sidebar as a short Examples
    group, each labelled from its own first heading, with identical labels
    disambiguated minimally. They stay published pages and are still not
    index rows in docs/README.md."""

    def test_the_examples_group_closes_the_sidebar(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[brief](examples/synthetic_co/brief.md)\n",
            "examples/synthetic_co/brief.md": "# Research Brief - Synthetic Co\n",
            "examples/synthetic_co/draft.md": "# Credit Assessment Memorandum - Synthetic Co\n",
        })
        assert tree["_Sidebar.md"].endswith(
            f"\n\n**Examples**\n\n"
            f"- [Research Brief - Synthetic Co]({U}/wiki/examples-synthetic_co-brief)\n"
            f"- [Credit Assessment Memorandum - Synthetic Co]({U}/wiki/examples-synthetic_co-draft)\n"
        )

    def test_identical_example_labels_are_disambiguated_minimally(self, tmp_path):
        tree = make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[brief](examples/synthetic_co/brief.md)\n",
            "examples/synthetic_co/brief.md": "# Synthetic example\n",
            "examples/synthetic_co/draft.md": "# Synthetic example\n",
        })
        assert tree["_Sidebar.md"].endswith(
            f"\n\n**Examples**\n\n"
            f"- [Synthetic example (brief)]({U}/wiki/examples-synthetic_co-brief)\n"
            f"- [Synthetic example (draft)]({U}/wiki/examples-synthetic_co-draft)\n"
        )

    def test_no_examples_group_without_example_pages(self, tmp_path):
        tree = make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        assert "**Examples**" not in tree["_Sidebar.md"]
        assert tree["_Sidebar.md"].endswith(f"- [S]({U}/wiki/source)\n")

    def test_repeated_builds_produce_identical_sidebars(self, tmp_path):
        make_corpus(tmp_path, {
            "README.md": "# Index\n",
            "source.md": "[brief](examples/synthetic_co/brief.md)\n",
            "examples/synthetic_co/brief.md": "# Research Brief - Synthetic Co\n",
            "examples/synthetic_co/draft.md": "# Credit Assessment Memorandum - Synthetic Co\n",
        })
        first = wiki_publish.build_tree(tmp_path, "example/repo")["_Sidebar.md"]
        second = wiki_publish.build_tree(tmp_path, "example/repo")["_Sidebar.md"]
        assert first == second


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

    def test_publish_requires_source_sha(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as raised:
            self.run(["--repo", "example/repo", "--root", str(tmp_path), "--publish"])
        assert raised.value.code == 2
        assert "--publish requires --source-sha" in capsys.readouterr().err

    def test_remote_requires_publish(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as raised:
            self.run(["--repo", "example/repo", "--root", str(tmp_path),
                      "--check", "--remote", str(tmp_path / "r.git")])
        assert raised.value.code == 2
        assert "--remote is only valid with --publish" in capsys.readouterr().err

    def test_repo_is_required(self, tmp_path):
        with pytest.raises(SystemExit) as raised:
            self.run(["--root", str(tmp_path), "--check"])
        assert raised.value.code == 2


def _make_dir(path):
    """Create the directory a patched mkdtemp would have returned; give back its path as text."""
    path.mkdir(parents=True)
    return str(path)


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

    def _git(self, cwd, *args, stdin=None):
        """Run git with a fixed identity and no inherited git configuration."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(
            ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
             "-c", "commit.gpgsign=false", *args],
            cwd=cwd, check=True, capture_output=True, text=True, env=env, timeout=60, input=stdin)

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
        return remote  # the seed directory is left in place: git marks its objects read-only,
        # and deleting them is a Windows permission problem with nothing to test

    def remote_files(self, remote):
        """The remote's tracked filenames, NUL-delimited and decoded exactly as git
        reported them (a text-mode or quoted listing can hide a non-ASCII name)."""
        result = subprocess.run(
            ["git", "--git-dir", str(remote), "ls-tree", "-r", "-z", "--name-only", "HEAD"],
            check=True, capture_output=True, timeout=60,
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
        return [name.decode("utf-8", "surrogateescape")
                for name in result.stdout.split(b"\0") if name]

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
        encoded = base64.b64encode(f"x-access-token:{self.SENTINEL}".encode())  # the real credential form
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

    def test_the_real_credential_is_used_and_persisted_nowhere(self, tmp_path, tmp_path_factory,
                                                                 monkeypatch, capsys):
        """The production token-bearing path, run for real with only the network
        transport substituted: the clone URL is rewritten to a local bare repository
        (no --remote, so the token path runs); the header, the environment, the real
        git subprocesses and the files they write are the production ones. The raw
        token AND the actual encoded credential must appear in neither output stream
        nor any file the run touches. This verifies transport-side handling, not that
        GitHub accepts the credential."""
        remote = self.bare_remote(tmp_path)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        monkeypatch.setenv("GITHUB_TOKEN", self.SENTINEL)
        # a work directory OUTSIDE the repository root, kept so every file can be scanned
        work_dir = tmp_path_factory.mktemp("credential-work")
        monkeypatch.setattr(wiki_publish.tempfile, "mkdtemp", lambda *a, **k: str(work_dir))
        monkeypatch.setattr(wiki_publish.shutil, "rmtree", lambda path, **k: None)
        real_run = subprocess.run

        def local_transport(argv, **kwargs):
            argv = list(argv)
            if "clone" in argv:  # only the transport: the local bare repo, not github.com
                argv[argv.index("clone") + 2] = str(remote)
            return real_run(argv, **kwargs)

        monkeypatch.setattr(wiki_publish.subprocess, "run", local_transport)
        code = wiki_publish.main([
            "--repo", "example/repo", "--root", str(tmp_path), "--publish",
            "--source-sha", self.SHA1])

        assert code == 0
        captured = capsys.readouterr()
        credential = base64.b64encode(f"x-access-token:{self.SENTINEL}".encode()).decode("ascii")
        for stream in (captured.out, captured.err):
            assert self.SENTINEL not in stream and credential not in stream
        for path in list(tmp_path.rglob("*")) + list(work_dir.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                assert self.SENTINEL.encode("utf-8") not in data, path
                assert credential.encode("ascii") not in data, path

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

    def test_the_token_authenticates_both_the_clone_and_the_push(self, tmp_path, monkeypatch, capsys):
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_header_test_token")
        calls = []

        def fake_run(argv, **kwargs):
            index = 1  # skip the executable; then the -c <value> pairs before the subcommand
            while argv[index] == "-c":
                index += 2
            head = argv[index]
            calls.append((head, argv))
            if head == "symbolic-ref":
                return subprocess.CompletedProcess(argv, 0, "master\n", "")
            if head == "diff":
                return subprocess.CompletedProcess(argv, 1, "", "")  # differences exist
            if head == "ls-files":  # the real call is binary (-z)
                return subprocess.CompletedProcess(argv, 0, b"", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(wiki_publish.subprocess, "run", fake_run)
        code = wiki_publish.main([
            "--repo", "example/repo", "--root", str(tmp_path), "--publish", "--source-sha", self.SHA1])
        assert code == 0
        captured = capsys.readouterr()
        assert "published 3 pages" in captured.out  # the success path, not the no-change exit
        assert "ghs_header_test_token" not in captured.out + captured.err
        header = base64.b64encode(b"x-access-token:ghs_header_test_token").decode("ascii")
        heads = [head for head, _ in calls]
        assert heads.count("clone") == 1 and heads.count("push") == 1
        for head, argv in calls:
            if head in ("clone", "push"):  # both remote operations carry the header
                assert f"http.extraheader=Authorization: Basic {header}" in argv
        assert not any("ghs_header_test_token" in str(part) for _, argv in calls for part in argv)

    def test_the_token_is_absent_from_every_git_child_environment(self, tmp_path, monkeypatch):
        # CG361-1: the header is the only route to git; the raw token must not ride along in
        # the environment of any child process (clone, push or a purely local command).
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_env_canary_token")
        monkeypatch.setenv("GIT_DIR", "/nonexistent")  # still dropped, as before
        envs = []

        def fake_run(argv, **kwargs):
            envs.append(kwargs["env"])
            index = 1
            while argv[index] == "-c":
                index += 2
            head = argv[index]
            if head == "symbolic-ref":
                return subprocess.CompletedProcess(argv, 0, "master\n", "")
            if head == "diff":
                return subprocess.CompletedProcess(argv, 1, "", "")
            if head == "ls-files":
                return subprocess.CompletedProcess(argv, 0, b"", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(wiki_publish.subprocess, "run", fake_run)
        code = wiki_publish.main([
            "--repo", "example/repo", "--root", str(tmp_path), "--publish", "--source-sha", self.SHA1])
        assert code == 0
        assert len(envs) >= 5  # clone, branch, listing, staging, commit, push, ...
        for env in envs:
            assert "GITHUB_TOKEN" not in env
            assert "ghs_env_canary_token" not in env.values()
            assert "GIT_DIR" not in env
            assert env["GIT_TERMINAL_PROMPT"] == "0"

    @pytest.mark.parametrize("stderr,expected", [
        ("fatal: Authentication failed for 'https://github.com/x/y.wiki.git/'", "authorisation rejection"),
        ("fatal: could not read Username for 'https://github.com': terminal prompts disabled",
         "authorisation rejection"),
        ("fatal: unable to access 'https://github.com/x/y.wiki.git/': The requested URL returned error: 403",
         "authorisation rejection"),
        ("fatal: unable to access 'https://github.com/x/y.wiki.git/': Could not resolve host: github.com",
         "transport error"),
        ("error: open('index.lock': Permission denied", "cause not identified"),  # a LOCAL permission
        # error is not evidence about the remote token and must not become one
        ("fatal: the remote end hung up unexpectedly", "cause not identified"),
    ])
    def test_push_failures_are_classified_without_echoing_git(self, tmp_path, monkeypatch, capsys,
                                                              stderr, expected):
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})

        def fake_run_git(args, cwd=None, token=None, text=True):
            index = 0
            while args[index] == "-c":
                index += 2
            head = args[index]
            if head == "push":
                return subprocess.CompletedProcess(args, 1, "", stderr)
            if head == "symbolic-ref":
                return subprocess.CompletedProcess(args, 0, "master\n", "")
            if head == "diff":
                return subprocess.CompletedProcess(args, 1, "", "")
            if head == "ls-files":  # the real call is binary (-z)
                return subprocess.CompletedProcess(args, 0, b"", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(wiki_publish, "_run_git", fake_run_git)
        code = self.publish(tmp_path, "unused-remote")

        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert expected in captured.err
        assert stderr not in captured.err  # git's raw output stays out of the message

    def test_a_git_timeout_is_one_sanitized_error_line(self, tmp_path, capsys, monkeypatch):
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_timeout_token")  # the real-remote path: the header is built

        def slow(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 300)

        monkeypatch.setattr(wiki_publish.subprocess, "run", slow)
        code = wiki_publish.main([
            "--repo", "example/repo", "--root", str(tmp_path), "--publish", "--source-sha", self.SHA1])
        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert captured.err.startswith("error:")
        assert "timed out" in captured.err
        # the command line the exception would echo carries the authentication header: neither it
        # nor the raw token may reach the message
        header = base64.b64encode(b"x-access-token:ghs_timeout_token").decode("ascii")
        assert header not in captured.err and "ghs_timeout_token" not in captured.err
        assert "extraheader" not in captured.err

    def test_a_work_dir_inside_the_repository_is_refused(self, tmp_path, monkeypatch, capsys):
        remote = self.bare_remote(tmp_path)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        # TMPDIR pointing inside the repository: the clone must not land there
        monkeypatch.setattr(wiki_publish.tempfile, "mkdtemp",
                            lambda *a, **k: _make_dir(tmp_path / "docs" / "tmp-work"))
        code = self.publish(tmp_path, remote)
        assert code == 1
        captured = capsys.readouterr()
        assert len(captured.err.splitlines()) == 1
        assert captured.err.count("error:") == 1
        assert "temporary" in captured.err
        assert not (tmp_path / "docs" / "tmp-work").exists()  # CG361-4: the refused directory is removed

    def test_a_tracked_symlink_at_a_generated_name_cannot_redirect_the_write(self, tmp_path):
        # A wiki could track Home.md as a symlink; the publish must replace it with a
        # regular generated page and never write through the link. The seed commit is
        # built with plumbing (hash-object/mktree/commit-tree) so no filesystem symlink
        # is needed and the test is meaningful on Windows too.
        victim = tmp_path / "victim.txt"
        victim.write_text("precious\n", encoding="utf-8")
        remote = tmp_path / "wiki.git"
        self._git(tmp_path, "-c", "init.defaultBranch=master", "init", "--bare", "-q", str(remote))
        link_text = tmp_path / "link-text"
        link_text.write_text(str(victim), encoding="utf-8")
        blob = self._git(None, "--git-dir", str(remote), "hash-object", "-w", str(link_text)).stdout.strip()
        tree = self._git(None, "--git-dir", str(remote), "mktree",
                          stdin=f"120000 blob {blob}\tHome.md").stdout.strip()
        commit = self._git(None, "--git-dir", str(remote), "commit-tree", tree,
                           "-m", "Symlink Home page").stdout.strip()
        self._git(None, "--git-dir", str(remote), "update-ref", "refs/heads/master", commit)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})

        assert self.publish(tmp_path, remote) == 0

        assert victim.read_text(encoding="utf-8") == "precious\n"  # never written through the link
        entry = self._git(None, "--git-dir", str(remote), "ls-tree", "HEAD", "Home.md").stdout.split()
        assert entry[0] == "100644"  # a regular file, not a symlink
        home = self._git(None, "--git-dir", str(remote), "show", "HEAD:Home.md").stdout
        assert home.startswith(notice_for("docs/README.md"))

    def test_full_replacement_handles_filenames_git_quotes(self, tmp_path):
        # A stale tracked page whose name git quotes in ls-files output (non-ASCII): the
        # NUL-delimited listing must still remove it, or publication fails instead of it.
        remote = self.bare_remote(tmp_path)
        work = tmp_path / "stale-worktree"
        self._git(tmp_path, "clone", "--quiet", str(remote), str(work))
        (work / "café.md").write_text("stale\n", encoding="utf-8")
        self._git(work, "add", "--", "café.md")
        self._git(work, "commit", "-q", "-m", "add stale page")
        self._git(work, "push", "--quiet", "origin", "master")
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})

        assert self.publish(tmp_path, remote) == 0

        # the complete replacement: exactly the generated set, nothing stale left behind
        assert sorted(self.remote_files(remote)) == ["Home.md", "_Sidebar.md", "source.md"]

    def test_magic_names_reach_git_as_literal_pathspecs_whatever_the_platform(self, tmp_path,
                                                                              monkeypatch):
        """The command construction for names that are themselves pathspec magic (a leading
        colon): removal and staging must both carry the :(literal) prefix. The real
        integration with such filenames runs on POSIX only (the two tests below) -- a colon
        is not a legal Windows filename, so git cannot even check such a tree out there
        (the publisher's clone fails); this mocked-git test is what pins the construction
        on every platform, including Windows."""
        commands = []

        def fake_run_git(args, cwd=None, token=None, text=True):
            commands.append(list(args))
            index = 0
            while args[index] == "-c":  # skip the -c <value> pairs before the subcommand
                index += 2
            head = args[index]
            if head == "ls-files":
                return subprocess.CompletedProcess(args, 0, b":stale.md\0", "")
            if head == "symbolic-ref":
                return subprocess.CompletedProcess(args, 0, "master\n", "")
            if head == "diff":
                return subprocess.CompletedProcess(args, 1, "", "")  # differences exist
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(wiki_publish, "_run_git", fake_run_git)
        # a tree holding a magic-named page, with no files written: the point is the
        # commands publish() builds, not the corpus or the write
        monkeypatch.setattr(wiki_publish, "build_tree",
                            lambda root, repo: {":magic.md": "x", "Home.md": "x"})
        monkeypatch.setattr(wiki_publish, "write_tree", lambda out_dir, tree: None)

        code = self.publish(tmp_path, "unused-remote")

        assert code == 0
        assert ["rm", "-q", "--", ":(literal):stale.md"] in commands
        assert ["add", "--", ":(literal):magic.md"] in commands

    @pytest.mark.skipif(sys.platform.startswith("win32"),
                        reason="git cannot check a tree containing ':stale.md' out on Windows "
                               "(a colon is not a legal filename there), so the publisher's "
                               "clone fails; the command construction this test exercises "
                               "for real is pinned on every platform by the mocked-git test above")
    def test_a_stale_filename_that_greets_pathspec_magic_is_removed_literally(self, tmp_path):
        # A tracked stale file whose name is itself pathspec magic (a leading colon):
        # `git rm -- <name>` reads it as `:(top)<rest>` and matches nothing. The seed tree
        # is built with plumbing (hash-object/mktree/commit-tree), like the symlink test --
        # no filesystem file with that name is needed. POSIX only: git's checkout of the
        # clone cannot write that filename on Windows.
        remote = tmp_path / "wiki.git"
        self._git(tmp_path, "-c", "init.defaultBranch=master", "init", "--bare", "-q", str(remote))
        content = tmp_path / "magic-content"
        content.write_text("stale\n", encoding="utf-8")
        blob = self._git(None, "--git-dir", str(remote),
                        "hash-object", "-w", str(content)).stdout.strip()
        tree = self._git(None, "--git-dir", str(remote), "mktree",
                        stdin=f"100644 blob {blob}\t:stale.md").stdout.strip()
        commit = self._git(None, "--git-dir", str(remote), "commit-tree", tree,
                          "-m", "Add a magic-named file").stdout.strip()
        self._git(None, "--git-dir", str(remote), "update-ref", "refs/heads/master", commit)
        assert ":stale.md" in self.remote_files(remote)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        assert self.publish(tmp_path, remote) == 0
        assert ":stale.md" not in self.remote_files(remote)
        assert sorted(self.remote_files(remote)) == ["Home.md", "_Sidebar.md", "source.md"]

    @pytest.mark.skipif(sys.platform.startswith("win32"),
                        reason="a colon is not a legal Windows filename, so the docs/ corpus "
                               "this test needs cannot exist there; the publish itself is "
                               "covered on Windows by the other tests")
    def test_a_generated_page_greeting_pathspec_magic_is_staged_literally(self, tmp_path):
        # The mirror of the removal case: a docs page whose own generated name is
        # pathspec magic (a leading colon -- a legal POSIX filename). `git add -- <name>`
        # would read it as `:(top)<rest>` and stage nothing, so the publish must stage
        # it literally. Shown red before the fix.
        remote = self.bare_remote(tmp_path)
        make_corpus(tmp_path, {"README.md": "# Index\n", "source.md": "# S\n"})
        (tmp_path / "docs" / ":magic.md").write_text("# M\n", encoding="utf-8")
        (tmp_path / "docs" / "README.md").write_text(
            "# Index\n\n## What is in this folder\n\n| Document | Contents |\n| :--- | :--- |\n"
            "| [source.md](source.md) | x |\n| [:magic.md](:magic.md) | x |\n", encoding="utf-8")
        assert self.publish(tmp_path, remote) == 0
        assert ":magic.md" in self.remote_files(remote)

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

        def fake_run_git(args, cwd=None, token=None, text=True):
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
                return subprocess.CompletedProcess(args, 0, b"stale.md\0", "")
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
        # 23 top-level pages (README becomes Home) + 2 example pages + sidebar.
        assert len(tree) == 26
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
