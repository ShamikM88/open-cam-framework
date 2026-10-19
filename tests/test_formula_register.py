"""Drift guard for the formula register (issue #366).

`docs/formula-register.md` is the one reviewable page of every computed
metric: the code is the source of truth and the register describes it. This
test fails when the two drift apart, in either direction:

1. a formula row in ``spreading_builder.SECTIONS`` has no register entry;
2. a key produced by ``evaluate_financial_model()`` on the synthetic example
   in ``docs/examples/synthetic_co`` has no register entry;
3. a rule of the policy engine -- a key of ``evaluate_deal_policy()``'s
   result, the code-derived definition of "rule" used here -- has no register
   entry;
4. an entry names a file or function that does not exist in the repository.

Deleting an entry from the register therefore fails the coverage checks
above (its state keys, workbook rows or policy outputs become uncovered);
renaming a function or moving a file fails the existence check. The guard is
deliberately one-directional on coverage: the register may explain more than
the code computes (the "Proposed, not implemented" collateral entries), but
never less.
"""
import ast
import json
import re
from pathlib import Path

import policy_engine
import spreading_builder

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER_PATH = REPO_ROOT / "docs" / "formula-register.md"
SYNTHETIC_INPUT = REPO_ROOT / "docs" / "examples" / "synthetic_co" / "financials_input.json"

ENTRY_HEADING_RE = re.compile(r"^### (F-\d{3}) — (.+)$", re.MULTILINE)

REQUIRED_FIELDS = (
    "Name",
    "Formula",
    "Unit and storage",
    "Edge rules",
    "State keys",
    "Workbook rows",
    "Policy outputs",
    "Implemented in",
    "Basis",
    "Status",
)

# The collateral metrics agreed on issue #266 (comment 6259256219): the register
# carries them as "Proposed, not implemented" entries, so deleting any of them
# is a drift the guard must catch.
REQUIRED_PROPOSED_COLLATERAL = (
    "Gross Exposure (Exc. RV)",
    "RV Exposure",
    "Total Exposure (Inc. RV)",
    "Net Exposure",
    "Collateral Value",
    "Coverage %",
    "Uncovered Risk",
    "Max LGD",
    "Avg LGD",
)


def _parse_register():
    """Return [{id, name, fields...}] parsed from the register's entry blocks.

    A field's value may wrap onto indented continuation lines; those are
    joined onto the field so a file or function reference cannot escape
    validation by sitting on a wrapped line.
    """
    assert REGISTER_PATH.is_file(), (
        f"{REGISTER_PATH} does not exist; the formula register page is missing"
    )
    text = REGISTER_PATH.read_text(encoding="utf-8")
    matches = list(ENTRY_HEADING_RE.finditer(text))
    assert matches, "no entries found in the formula register (no '### F-NNN — Name' headings)"
    entries = []
    for index, match in enumerate(matches):
        block = text[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        entry = {"id": match.group(1), "name": match.group(2).strip()}
        current_field = None
        for line in block.splitlines():
            field_match = re.match(r"^- \*\*(.+?):\*\* (.+)$", line)
            if field_match:
                field, value = field_match.group(1), field_match.group(2)
                assert field not in entry, f"{entry['id']}: duplicate field {field!r}"
                entry[field] = value
                current_field = field
            elif current_field and line.startswith("  ") and line.strip():
                entry[current_field] += " " + line.strip()
            else:
                current_field = None
        entries.append(entry)
    return entries


def _backticked(text):
    return re.findall(r"`([^`]+)`", text)


def _state_keys(entry):
    return _backticked(entry.get("State keys", ""))


def _workbook_rows(entry):
    value = entry.get("Workbook rows", "")
    if value == "—":
        return []
    return [label.strip() for label in value.split(" · ") if label.strip()]


def _policy_outputs(entry):
    return _backticked(entry.get("Policy outputs", ""))


def _sections_formula_rows():
    for _section_name, rows in spreading_builder.SECTIONS:
        for label, template in rows:
            if template is not None:
                yield label


def _synthetic_example_keys():
    with SYNTHETIC_INPUT.open(encoding="utf-8") as handle:
        data = json.load(handle)
    result = spreading_builder.evaluate_financial_model(data)
    financials = result["financials"]["FY-Current"]
    ratios = result["ratios"]["FY-Current"]
    return sorted(k for k in financials if k != "raw"), sorted(ratios)


def test_entries_parse_with_unique_sequential_ids():
    entries = _parse_register()
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids)), "duplicate entry ids in the formula register"
    expected = [f"F-{number:03d}" for number in range(1, len(ids) + 1)]
    assert ids == expected, (
        "entry ids must be unique and sequential from F-001 without gaps: "
        f"found {ids}"
    )


def test_every_entry_has_the_required_fields():
    for entry in _parse_register():
        missing = [field for field in REQUIRED_FIELDS if field not in entry]
        assert not missing, f"{entry['id']} ({entry['name']}): missing fields {missing}"


def test_status_uses_the_agreed_forms():
    for entry in _parse_register():
        status = entry["Status"]
        assert status.startswith((
            "Implemented, not yet reviewed by an analyst",
            "Reviewed (",
            "Change requested (issue #",
            "Proposed, not implemented",
        )), f"{entry['id']}: unexpected status {status!r}"


def test_every_sections_formula_row_has_an_entry():
    entries = _parse_register()
    covered = {row for entry in entries for row in _workbook_rows(entry)}
    for label in _sections_formula_rows():
        assert label in covered, (
            f"workbook formula row {label!r} in spreading_builder.SECTIONS has no "
            "formula-register entry"
        )


def test_every_synthetic_example_key_has_an_entry():
    entries = _parse_register()
    covered = {key for entry in entries for key in _state_keys(entry)}
    financial_keys, ratio_keys = _synthetic_example_keys()
    for key in financial_keys:
        assert key in covered, (
            f"financials key {key!r} produced by evaluate_financial_model() on the "
            "synthetic example has no formula-register entry"
        )
    for key in ratio_keys:
        assert key in covered, (
            f"ratio key {key!r} produced by evaluate_financial_model() on the "
            "synthetic example has no formula-register entry"
        )


def _policy_engine_rule_functions():
    """The policy engine's rule functions, derived from source: every module-level
    function that ``evaluate_deal_policy()`` calls directly. A new rule is a new
    call inside ``evaluate_deal_policy()`` (or a call moved into a helper it
    calls), so this inventory grows with the rules themselves rather than with
    the result containers they share — a new security check inside
    ``_evaluate_security()`` still needs its own register entry to be named
    there, and a new top-level rule fails this inventory's check below.
    """
    source = (REPO_ROOT / "scripts" / "policy_engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    rules = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate_deal_policy":
            for child in ast.walk(node):
                if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                        and child.func.id in defined
                        and child.func.id != "evaluate_deal_policy"):
                    rules.add(child.func.id)
    return sorted(rules)


def test_every_policy_engine_rule_has_an_entry():
    entries = _parse_register()
    covered = {key for entry in entries for key in _policy_outputs(entry)}
    for key in sorted(policy_engine.evaluate_deal_policy({}).keys()):
        assert key in covered, (
            f"policy-engine rule {key!r} (a key of evaluate_deal_policy()'s result) "
            "has no formula-register entry"
        )
    named_functions = {
        token[:-2]
        for entry in entries
        for token in _backticked(entry["Implemented in"])
        if token.endswith("()")
    }
    for rule in _policy_engine_rule_functions():
        assert rule in named_functions, (
            f"policy-engine rule function {rule!r} (called by evaluate_deal_policy()) "
            "is not named in any register entry's 'Implemented in'"
        )


def test_implemented_entries_name_real_files_and_functions():
    for entry in _parse_register():
        if entry["Status"].startswith("Proposed"):
            continue  # nothing to point at until issue #266's design lands
        implemented_in = entry["Implemented in"]
        tokens = _backticked(implemented_in)
        file_tokens = [t for t in tokens if "/" in t or t.endswith((".py", ".md", ".json"))]
        assert file_tokens, (
            f"{entry['id']} ({entry['name']}): 'Implemented in' names no repository file"
        )
        for token in file_tokens:
            assert (REPO_ROOT / token).is_file(), (
                f"{entry['id']} ({entry['name']}): {token} named in 'Implemented in' "
                "does not exist in the repository"
            )
        for token in tokens:
            if not token.endswith("()"):
                continue
            function = token[:-2]
            assert any(
                f"def {function}(" in (REPO_ROOT / file_token).read_text(encoding="utf-8")
                for file_token in file_tokens
                if file_token.endswith(".py")
            ), (
                f"{entry['id']} ({entry['name']}): function {token} named in "
                "'Implemented in' is not defined in the named Python files"
            )


def test_collateral_metrics_stay_proposed_entries():
    entries = _parse_register()
    proposed = {
        entry["name"]: entry for entry in entries
        if entry["Status"] == "Proposed, not implemented"
    }
    for name in REQUIRED_PROPOSED_COLLATERAL:
        assert name in proposed, (
            f"the collateral metric {name!r} agreed on issue #266 has no "
            "'Proposed, not implemented' entry in the formula register"
        )
