"""Template-drift guard: the checked-in spreading template equals the blank builder output (issue #374).

`templates/spreading/default_spreading_template.xlsx` is a generated artifact, not a hand-maintained
file (templates/README.md): it is the blank workbook `scripts/spreading_builder.py` produces. Nothing
tied the checked-in copy to the builder, so it silently drifted until PR #372 regenerated it. This test
builds the blank workbook the way the `spreading_blank` golden case (tests/test_golden_outputs.py) and
templates/README.md document, and compares it with the checked-in file through the golden-output
normalizer, so the next builder change that forgets to regenerate the template fails here with the
regeneration command instead of drifting silently.

This file is deliberately not on the mutation selection list: the mutation workflow runs a fixed list
of test files in a working copy that contains only `src/`, `tests/`, `.github` and `docs/examples`
(issue #376), so a test that reads `templates/` must stay out of `[tool.mutmut]`'s
`pytest_add_cli_args_test_selection` (unverified by this test; pinned by review).
"""
from pathlib import Path

import snapshot_utils
import spreading_builder

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "spreading" / "default_spreading_template.xlsx"

# The regeneration command, exactly as templates/README.md documents it.
REGENERATION_COMMAND = (
    "cd scripts\n"
    "python -c \"from spreading_builder import export_to_xlsx; export_to_xlsx('[Company Name]', "
    "'../templates/spreading/default_spreading_template.xlsx')\""
)


def test_checked_in_spreading_template_matches_the_blank_builder_output(tmp_path):
    built = tmp_path / "blank.xlsx"
    spreading_builder.export_to_xlsx("Synthetic Legacy Co", str(built))
    assert snapshot_utils.normalize_xlsx(built) == snapshot_utils.normalize_xlsx(TEMPLATE), (
        "templates/spreading/default_spreading_template.xlsx no longer matches the blank workbook "
        "scripts/spreading_builder.py produces. Regenerate it with the command from "
        "templates/README.md:\n" + REGENERATION_COMMAND
    )
