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
        "Do not use `max` or `avg`; those asset IDs are reserved and refused because they collide",
        "Record `lgd_pct` as the risk team's per-asset input; do not derive or estimate it.",
        "Do not calculate, estimate, round, adjust or restate any collateral metric in prose.",
        "Write all collected asset inputs and mitigants as one UTF-8 JSON payload outside the repository.",
        "passing that complete payload either from a file or",
        "from standard input:",
        "The calculator validates the complete payload before writing",
        "records the per-asset inputs and results in `collateral_analysis`",
        "refreshes the backward-compatible `collateral` projection with `exposure = total_exposure`",
        "marks the `collateral` step complete only after success",
        "The per-asset result echoes the recorded risk-team `lgd_pct` input",
        "deal-level total exposure, total collateral value, coverage, gross and net uncovered risk",
        "`collateral_max_lgd_pct` and `collateral_avg_lgd_pct`",
        "Treat the calculator's JSON output as the sole numeric source of truth.",
        "Use its unrounded values in the response; never calculate a substitute in prose.",
        "On a non-zero exit, stop, report the single `error:` line",
        "do not mark the step complete or write partial results.",
    ):
        assert phrase in " ".join(text.split()), phrase

    assert "per-asset explanations" not in text


def test_the_existing_bureau_score_paragraph_is_retained_verbatim():
    assert " ".join(BUREAU_PARAGRAPH.split()) in " ".join(command_text().split())


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
        if script == "collateral_check" and flags
    ]
    assert invocations == [
        ("collateral_check", {"--company", "--proposal", "--input-file"}),
        ("collateral_check", {"--company", "--proposal", "--input-file"}),
    ]
    assert all(flags <= options for _script, flags in invocations)
    assert {"--company", "--proposal", "--input-file"} <= options


def test_the_calculator_is_the_sole_writer_of_the_collateral_state():
    """Maintainer-approved State: write replacement (#266 comment 6642820954, approved 2026-11-05)."""
    text = " ".join(command_text().split())

    for phrase in (
        "Assign each asset a stable `asset_id`",
        "Include the stable ID in the calculator payload; do not write collateral results into `state.json` yourself.",
        "Do not set or write `collateral` or `collateral_analysis`, and do not append `collateral` to "
        "`steps_completed` yourself.",
        "The deterministic calculator writes `collateral_analysis`, refreshes the backward-compatible "
        "`collateral` projection, and appends the `collateral` step atomically only after successful validation.",
        "On a non-zero exit, leave those state values unchanged.",
    ):
        assert phrase in text, phrase


def test_the_hand_written_collateral_state_instructions_are_gone():
    text = " ".join(command_text().split())

    assert "`collateral` as a **flat list**" not in text
    assert '"cap_value": 0' not in text
    assert 'Append `"collateral"` to `steps_completed`' not in text
    assert "`exposure`/`collateral_value` are the Gross Exposure and Collateral Value you calculated" not in text


def test_the_other_state_write_instructions_are_kept():
    text = " ".join(command_text().split())

    for phrase in (
        "`company`, `proposal`, `date`, `deal_type` (if known), `inputs.pd`, `inputs.lgd`",
        "Also set/update `security_package` as a **flat list**",
        '"secures_asset_id": "AST-001"',
    ):
        assert phrase in text, phrase


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


def test_the_state_write_never_creates_a_state_file():
    """CG560-3: the read section says no matching record is a stop, so the write must not create one."""
    text = " ".join(command_text().split())
    assert "If no state file existed, create" not in text
    assert ("Update the existing `state.json` found above and write back to that same file "
            "(never create one: no matching record is a stop).") in text


def test_the_security_package_bullet_does_not_point_at_a_field_the_command_no_longer_lists():
    """The command no longer lists `collateral.perfection_status`; the contrast is with the calculator payload."""
    text = " ".join(command_text().split())
    assert "Unlike `collateral.perfection_status` above" not in text
    assert ("Unlike the asset's free-text `perfection_status` in the calculator payload, which is a narrative field,"
            in text)
