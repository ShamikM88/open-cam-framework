"""The shipped CAM templates keep drafter guidance in HTML comments and leave the human signature blank (issue #439).

This lives apart from tests/test_docx_builder.py on purpose: the mutation run copies only the paths named in
`also_copy` (pyproject.toml) next to the tests it selects, and `test_docx_builder.py` is selected, so a test there that
reads `templates/` fails the run's clean pass before a single mutant is tested.
"""
from pathlib import Path


def test_shipped_cam_guidance_uses_html_comments_and_blank_human_signature():
    root = Path(__file__).resolve().parents[1]
    expected_guidance_counts = {
        "asset_finance_cam.md": 9,
        "corporate_credit_cam.md": 5,
    }
    signature = "**Credit Analyst:** ________ **Date:** ________"
    signature_guidance = "<!-- Guidance: The drafter never fills this line. -->"

    for name, expected_count in expected_guidance_counts.items():
        lines = (root / "templates" / "cam" / name).read_text(encoding="utf-8").splitlines()
        guidance = [line for line in lines if line.startswith("<!-- Guidance: ") and line.endswith(" -->")]
        assert len(guidance) == expected_count + 1, name
        assert signature in lines, name
        signature_index = lines.index(signature)
        assert lines[signature_index + 1] == signature_guidance, name
