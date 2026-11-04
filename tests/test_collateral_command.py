"""Contract for the /collateral command (issue #266).

The approved calculation paragraph is a thin wrapper over the deterministic calculator. The
legacy hand-written state-write block is deliberately pinned until a separate maintainer
decision authorises its replacement; this test does not grant that authorisation.
"""
from pathlib import Path

from test_command_flags import extract_invocations, real_options
from test_skill_inventory import REGISTRY, frontmatter, inventory_rows, read

REPO = Path(__file__).resolve().parents[1]
COMMAND = REPO / ".claude" / "commands" / "collateral.md"

BUREAU_PARAGRAPH = (
    "PD, LGD, and any bureau score are all user-supplied inputs — this framework has no "
    "bureau/rating-agency integration — so use exactly what's given; never invent or adjust "
    "a grade or score yourself. Always ask for the bureau score explicitly before this deal's "
    "CAM is finalized (the CAM template's Credit Bureau / Legal Charges section — its section "
    "number varies by deal type/template) rather than defaulting it to \"N/A\"/"
    "\"Not provided\" unasked — if the user confirms none is available or applicable, "
    "that's a valid, citable answer in its own right."
)


def command_text():
    return COMMAND.read_text(encoding="utf-8")


def test_frontmatter_pins_the_person_invoked_command_and_approved_tools():
    fields = frontmatter(command_text())

    assert fields["disable-model-invocation"] == "true"
    assert set(fields) == {"description", "argument-hint", "disable-model-invocation", "allowed-tools"}
    assert fields["allowed-tools"] == (
        "Bash(python scripts/collateral_check.py *), "
        "Bash(python scripts/state_manager.py *), "
        "Bash(python scripts/source_manifest.py *)"
    )


def test_the_deterministic_calculation_paragraph_pins_its_load_bearing_rules():
    text = command_text()

    for phrase in (
        "**Deterministic collateral calculation (mandatory)**",
        "Collect the required per-asset inputs and every non-asset mitigant, including an explicit",
        "`eligible_for_netting` value for each mitigant",
        "Each asset must have a stable, unique",
        "`asset_id` matching `[A-Za-z0-9-]{1,32}`",
        "Do not use `max` or `avg`; they are reserved identifiers.",
        "Record `lgd_pct` as the risk team's per-asset input; do not derive or estimate it.",
        "Do not calculate, estimate, round, adjust or restate any collateral metric in prose.",
        "Write all collected asset inputs and mitigants as one UTF-8 JSON payload outside the repository.",
        "passing that complete payload either from a file or",
        "from standard input:",
        "The calculator validates the complete payload before writing",
        "records the per-asset inputs and results in `collateral_analysis`",
        "refreshes the backward-compatible `collateral` projection with `exposure = total_exposure`",
        "marks the `collateral` step complete only after success",
        "the per-asset result echoes the recorded risk-team `lgd_pct` input",
        "deal-level total exposure, total collateral value, coverage, gross and net uncovered risk",
        "`collateral_max_lgd_pct` and `collateral_avg_lgd_pct`",
        "Treat the calculator's JSON output as the sole numeric source of truth.",
        "Use its unrounded values in the response; never calculate a substitute in prose.",
        "On a non-zero exit, stop, report the single `error:` line",
        "do not mark the step complete or write partial results.",
    ):
        assert phrase in text, phrase

    assert "per-asset explanations" not in text


def test_the_existing_bureau_score_paragraph_is_retained_verbatim():
    assert BUREAU_PARAGRAPH in command_text()


def test_both_calculator_invocations_match_the_real_cli_help():
    text = command_text()
    expected = [
        'python scripts/collateral_check.py --company "<company>" --proposal "<proposal>" '
        '--input-file "<payload.json>"',
        'python scripts/collateral_check.py --company "<company>" --proposal "<proposal>" '
        '--input-file - < "<payload.json>"',
    ]
    actual = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("python scripts/collateral_check.py")
    ]
    assert actual == expected

    options = real_options("collateral_check")
    assert options is not None
    invocations = [
        (script, set(flags))
        for _line, script, flags in extract_invocations(text)
        if script == "collateral_check"
    ]
    assert invocations == [
        ("collateral_check", {"--company", "--proposal", "--input-file"}),
        ("collateral_check", {"--company", "--proposal", "--input-file"}),
    ]
    assert all(flags <= options for _script, flags in invocations)
    assert {"--company", "--proposal", "--input-file"} <= options


def test_the_unapproved_legacy_state_write_text_stays_out_of_this_change():
    """The separate design comment asks for sign-off; this PR must not silently make that edit."""
    text = command_text()

    assert "Assign each asset a stable `asset_id`" in text
    assert "`collateral` as a **flat list** (one entry per" in text
    assert 'Append "collateral" to `steps_completed`' in text
    assert "Write `collateral_analysis` by hand" not in text


def test_the_command_does_not_load_or_allow_a_skill():
    text = command_text()

    for forbidden in (".claude/skills", "SKILL.md", "Skill tool", "Skill("):
        assert forbidden not in text, forbidden


def test_the_registry_row_matches_the_existing_command_identity():
    rows = {row["name"]: row for row in inventory_rows(read(REGISTRY))}
    row = rows["/collateral"]
    assert (
        row["kind"], row["audience"], row["status"], row["invocation"], row["role"], row["headless"]
    ) == ("command", "analyst", "implemented", "user", "Maker", "yes")
