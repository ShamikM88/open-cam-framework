"""CG593-1 (PR #593): HTML-commented text must not satisfy the Sources check.

The Word export strips HTML comments before it renders, so a commented-out Sources heading or list never reaches the
exported document; the shared finder must ignore it too, or the CAM warning (#583) and the research brief's mandatory
Sources section (#318) are silently suppressed. Synthetic text only.
"""
import pytest

import research_export
from deal_export import export_deal

COMMENTED_SECTION = "# Synthetic CAM\n\nBody text.\n\n<!--\n## Sources\n\n- **Fake:** https://example.invalid/fake\n-->\n"


def _populated(markdown):
    return research_export._has_populated_sources_section(markdown)


def test_a_fully_commented_out_sources_section_is_not_populated():
    assert _populated(COMMENTED_SECTION) is False


def test_a_comment_wrapping_only_the_entries_leaves_the_section_empty():
    markdown = "# Synthetic CAM\n\n## Sources\n\n<!--\n- **Fake:** https://example.invalid/fake\n-->\n"
    assert _populated(markdown) is False


def test_an_inline_comment_hiding_the_only_url_leaves_the_section_empty():
    markdown = "# Synthetic CAM\n\n## Sources\n\nSee <!-- https://example.invalid/hidden --> below.\n"
    assert _populated(markdown) is False


def test_a_comment_beside_real_entries_does_not_hide_them():
    markdown = (
        "# Synthetic CAM\n\n## Sources\n\n<!-- guidance for the drafter -->\n"
        "- **FY25:** [accounts](https://example.invalid/a)\n"
    )
    assert _populated(markdown) is True


def test_a_commented_heading_does_not_end_a_real_section_early():
    markdown = (
        "# Synthetic CAM\n\n## Sources\n\n<!--\n## Not a heading\n-->\n"
        "- **FY25:** [accounts](https://example.invalid/a)\n"
    )
    assert _populated(markdown) is True


def test_an_unterminated_comment_does_not_crash_the_finder():
    # The Word export refuses an unterminated comment on its own; the finder only has to stay quiet.
    assert _populated("# Synthetic CAM\n\n## Sources\n\n- **FY25:** [accounts](https://example.invalid/a)\n<!-- open") is True


def test_a_commented_out_sources_section_still_fails_the_research_brief_check(tmp_path):
    brief = COMMENTED_SECTION.replace("CAM", "brief")
    with pytest.raises(ValueError, match="populated Sources or References section"):
        research_export.export_research_brief(
            "Acme Co", "Synthetic Lease", brief, date_str="2026-01-15", base_dir=str(tmp_path),
        )
    assert not (tmp_path / "deals").exists()


def test_export_deal_warns_when_the_only_sources_section_is_commented_out(tmp_path, capsys):
    export_deal("Acme Corp", "Fleet Loan", "asset_finance", COMMENTED_SECTION, date_str="2026-01-15",
                base_dir=str(tmp_path))
    warnings = [line for line in capsys.readouterr().err.splitlines() if "Sources section warning" in line]
    assert len(warnings) == 1
