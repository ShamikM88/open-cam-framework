"""Gate for the rules register (issue #499).

`docs/rules-register.md` gives every governance rule a stable id
`R-<AREA>-NNN` and records its status; a test cites a rule with a literal
`Rule: R-...` line in its docstring. This test fails when the register and
the citations drift apart, in either direction:

1. a Covered rule has no citing test docstring;
2. a citation names an id that is not in the register;
3. an id is duplicated, an issued id has no retained entry (a renumbered or
   deleted id), or an entry's id is missing from the "IDs ever issued" list;
4. a Not covered rule has no issue link in Tracking;
5. a citation names a Withdrawn rule;
6. an "Implemented in" pointer does not resolve to an existing file and, when
   a function or method is named, an existing definition in that file.

A citation declares which rule a test is responsible for; it does not prove
the test is strong, that its assertions cover the Claim, or that it executes:
a skipped or deselected test is still present in source, and tests generated
dynamically in a loop are not reliably visible to an AST scan (the register
page states these limits). The gate checks the structural link only.
"""
import ast
import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER_PATH = REPO_ROOT / "docs" / "rules-register.md"
TESTS_DIR = REPO_ROOT / "tests"

RULE_ID = r"R-[A-Z]+-\d{3}"
ENTRY_HEADING_RE = re.compile(rf"^### ({RULE_ID}) — (.+)$", re.MULTILINE)
ISSUED_HEADING_RE = re.compile(r"^## IDs ever issued$", re.MULTILINE)
FIELD_RE = re.compile(r"^- \*\*(.+?):\*\* (.+)$")
BACKTICKED_RE = re.compile(r"`([^`]+)`")
ISSUE_LINK_RE = re.compile(r"\(https?://[^)]*/issues/\d+\)|#\d+")
CITATION_LINE_RE = re.compile(r"^Rule:\s*(.+)$")

STATUS_FORMS = ("Covered", "Not covered", "Withdrawn")


def _parse_register(text):
    """Return (entries, issued_ids) parsed from the register page.

    A field's value may wrap onto indented continuation lines; those are
    joined onto the field so a pointer or link cannot escape validation by
    sitting on a wrapped line. An entry whose heading does not match the
    page's `### R-<AREA>-NNN — Name` form is invisible to the parser and is
    caught by the issued-list checks instead.
    """
    matches = list(ENTRY_HEADING_RE.finditer(text))
    entries = []
    for index, match in enumerate(matches):
        block = text[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        entry = {"id": match.group(1), "name": match.group(2).strip()}
        current_field = None
        for line in block.splitlines():
            field_match = FIELD_RE.match(line)
            if field_match:
                current_field = field_match.group(1)
                entry[current_field] = field_match.group(2)
            elif current_field and line.startswith("  ") and line.strip():
                entry[current_field] += " " + line.strip()
            else:
                current_field = None
        entries.append(entry)
    issued = []
    heading = ISSUED_HEADING_RE.search(text)
    if heading:
        end = text.find("\n## ", heading.end())
        section = text[heading.end(): end if end != -1 else len(text)]
        issued = BACKTICKED_RE.findall(section)
    return entries, issued


def _test_docstrings(tree):
    """Yield (test_name, docstring) for every test the AST scan recognises:
    module-level ``test_*`` functions and ``test_*`` methods of ``Test*``
    classes. Helpers, module and class docstrings do not count."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                yield node.name, ast.get_docstring(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for child in node.body:
                if (isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and child.name.startswith("test_")):
                    yield f"{node.name}::{child.name}", ast.get_docstring(child)


def _collect_citations(test_files, root):
    """Return (citations, malformed): citations maps each cited rule id to
    the ``path::test_name`` locations citing it; malformed holds one message
    per `Rule:` line that does not match the full id format."""
    citations = {}
    malformed = []
    for path in sorted(test_files):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, docstring in _test_docstrings(tree):
            location = f"{path.relative_to(root).as_posix()}::{name}"
            for line in (docstring or "").splitlines():
                citation_match = CITATION_LINE_RE.match(line.strip())
                if not citation_match:
                    continue
                token = citation_match.group(1).strip()
                if re.fullmatch(RULE_ID, token):
                    citations.setdefault(token, []).append(location)
                else:
                    malformed.append(
                        f"Malformed citation {line.strip()!r} in {location}; "
                        "expected 'Rule: R-<AREA>-NNN'."
                    )
    return citations, malformed


def _module_level_definitions(path):
    """Top-level function and class/method names in a Python file, by source
    inspection; the module is never imported."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = set()
    classes = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.add(node.name)
        elif isinstance(node, ast.ClassDef):
            classes[node.name] = {
                child.name for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return functions, classes


def _check_implemented_in(entry, repo_root):
    """Fail when an 'Implemented in' pointer does not resolve. `<file>` is
    checked for the file; `<file>::<function>` additionally for a module-level
    function of that name and `<file>::<Class>.<method>` for a method of that
    class; `none` makes no existence claim and is reserved for a Not covered
    rule."""
    rule_id = entry["id"]
    tokens = BACKTICKED_RE.findall(entry.get("Implemented in", ""))
    if not tokens:
        return [f"Rule {rule_id} has no 'Implemented in' pointer."]
    pointer = tokens[0]
    if pointer == "none":
        if entry.get("Status") == "Not covered":
            return []
        return [f"Rule {rule_id} is not Not covered, so 'Implemented in' cannot be none."]
    file_part, separator, name_part = pointer.partition("::")
    file_path = repo_root / file_part
    if not file_path.is_file():
        return [f"Rule {rule_id}: 'Implemented in' names {pointer!r}, which does not exist in the repository."]
    if not separator:
        return []
    if not file_part.endswith(".py"):
        return [f"Rule {rule_id}: 'Implemented in' names {pointer!r}, whose file is not Python source."]
    functions, classes = _module_level_definitions(file_path)
    if "." in name_part:
        class_name, _, method_name = name_part.partition(".")
        if class_name not in classes or method_name not in classes[class_name]:
            return [f"Rule {rule_id}: 'Implemented in' names {pointer!r}, which is not defined in that file."]
    elif name_part not in functions:
        return [f"Rule {rule_id}: 'Implemented in' names {pointer!r}, which is not defined in that file."]
    return []


def _check_register(register_text, citations, malformed, repo_root):
    """The pure checker shared by the gate and its self-tests: return the
    sorted unique failure messages for a register page, its citations and
    the repository the 'Implemented in' pointers resolve against."""
    errors = list(malformed)
    entries, issued = _parse_register(register_text)
    entry_ids = [entry["id"] for entry in entries]
    statuses = {entry["id"]: entry.get("Status", "") for entry in entries}

    for rule_id, count in sorted(Counter(entry_ids).items()):
        if count > 1:
            errors.append(f"Duplicate rule ID {rule_id} in docs/rules-register.md.")
    for rule_id, count in sorted(Counter(issued).items()):
        if count > 1:
            errors.append(f"Duplicate rule ID {rule_id} in docs/rules-register.md.")
    for rule_id in sorted(set(issued) - set(entry_ids)):
        errors.append(f"Issued rule ID {rule_id} has no retained entry; keep it and mark Withdrawn.")
    for rule_id in sorted(set(entry_ids) - set(issued)):
        errors.append(f"Rule ID {rule_id} is not recorded in the issued-ID list.")

    for entry in entries:
        rule_id = entry["id"]
        status = entry.get("Status", "")
        if status not in STATUS_FORMS:
            errors.append(
                f"Rule {rule_id} has Status {status!r}; the page defines "
                "'Covered', 'Not covered' and 'Withdrawn'."
            )
        if status == "Not covered" and not ISSUE_LINK_RE.search(entry.get("Tracking", "")):
            errors.append(f"Not covered rule {rule_id} has no issue link.")
        if status == "Covered" and not citations.get(rule_id):
            errors.append(f"Covered rule {rule_id} has no citing test docstring.")
        errors.extend(_check_implemented_in(entry, repo_root))

    for rule_id, locations in sorted(citations.items()):
        if rule_id not in statuses:
            for location in sorted(set(locations)):
                errors.append(f"Unknown rule ID {rule_id} cited in {location}.")
        elif statuses[rule_id] == "Withdrawn":
            for location in sorted(set(locations)):
                errors.append(f"Withdrawn rule {rule_id} is cited in {location}; update the citation.")
    return sorted(set(errors))


def test_the_register_and_its_citations_agree():
    """The gate: the register on main and the citations in tests/ must be
    structurally linked. Run once on the real register; the failure modes
    themselves are proven on synthetic trees by the tests below."""
    citations, malformed = _collect_citations(TESTS_DIR.rglob("*.py"), REPO_ROOT)
    errors = _check_register(
        REGISTER_PATH.read_text(encoding="utf-8"), citations, malformed, REPO_ROOT
    )
    assert not errors, "the rules register gate failed:\n" + "\n".join(errors)


# --- Self-tests on synthetic trees (tmp_path is outside the scanned tests/ glob) ---

SYNTHETIC_IMPL = "def _evaluate_covenant():\n    return None\n"

CITING_TEST = '''import pytest


@pytest.mark.parametrize("value", [1, 2])
def test_cites(value):
    """One citation covers the whole parameter set.

    Rule: R-POL-001
    """
    assert value
'''


def _entry(rule_id, status, tracking, implemented):
    return f"""### {rule_id} — Synthetic rule {rule_id}

- **Claim:** A synthetic claim for {rule_id}.
- **Source:** [Synthetic](financial-model.md#synthetic).
- **Implemented in:** `{implemented}`
- **Status:** {status}
- **Tracking:** {tracking}
"""


def _register_tree(tmp_path, register, test_sources, impl_sources=None):
    """Write a synthetic register, test tree and implementation files under
    tmp_path and run the same pure checker the real gate uses."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for name, source in test_sources.items():
        (tests_dir / name).write_text(source, encoding="utf-8")
    for name, source in (impl_sources or {"scripts/synthetic_impl.py": SYNTHETIC_IMPL}).items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    citations, malformed = _collect_citations(sorted(tests_dir.glob("*.py")), tmp_path)
    return _check_register(register, citations, malformed, tmp_path)


def _valid_register():
    """A register that passes: a Covered rule with a citation, an uncited
    Withdrawn rule, and a Not covered rule with an issue link (which does not
    fail the build)."""
    return (
        "## IDs ever issued\n\n"
        "- `R-POL-001`\n- `R-POL-002`\n- `R-POL-003`\n\n"
        "## Synthetic area (POL)\n\n"
        + _entry("R-POL-001", "Covered", "—", "scripts/synthetic_impl.py::_evaluate_covenant")
        + "\n"
        + _entry("R-POL-002", "Withdrawn", "Withdrawn for the self-test.", "scripts/synthetic_impl.py")
        + "\n"
        + _entry(
            "R-POL-003",
            "Not covered",
            "[Issue #900](https://github.com/ShamikM88/open-cam-framework/issues/900)",
            "none",
        )
    )


def test_a_valid_register_with_citations_passes(tmp_path):
    assert _register_tree(tmp_path, _valid_register(), {"test_cites.py": CITING_TEST}) == []


def test_a_covered_rule_without_a_citing_test_fails(tmp_path):
    register = _valid_register()
    errors = _register_tree(tmp_path, register, {"test_empty.py": "def test_empty():\n    assert True\n"})
    assert errors == ["Covered rule R-POL-001 has no citing test docstring."]


def test_a_citation_to_an_unknown_id_fails(tmp_path):
    unknown = CITING_TEST.replace("R-POL-001", "R-POL-999")
    errors = _register_tree(tmp_path, _valid_register(), {"test_covered.py": CITING_TEST, "test_cites.py": unknown})
    assert errors == ["Unknown rule ID R-POL-999 cited in tests/test_cites.py::test_cites."]


def test_a_duplicate_rule_id_fails(tmp_path):
    register = _valid_register() + "\n" + _entry(
        "R-POL-001", "Not covered",
        "[Issue #900](https://github.com/ShamikM88/open-cam-framework/issues/900)",
        "scripts/synthetic_impl.py",
    )
    errors = _register_tree(tmp_path, register, {"test_cites.py": CITING_TEST})
    assert errors == ["Duplicate rule ID R-POL-001 in docs/rules-register.md."]


def test_an_issued_id_with_no_retained_entry_fails(tmp_path):
    register = _valid_register().replace("- `R-POL-003`\n", "- `R-POL-003`\n- `R-POL-004`\n")
    errors = _register_tree(tmp_path, register, {"test_cites.py": CITING_TEST})
    assert errors == ["Issued rule ID R-POL-004 has no retained entry; keep it and mark Withdrawn."]


def test_an_entry_id_missing_from_the_issued_list_fails(tmp_path):
    register = _valid_register().replace("- `R-POL-003`\n", "")
    errors = _register_tree(tmp_path, register, {"test_cites.py": CITING_TEST})
    assert errors == ["Rule ID R-POL-003 is not recorded in the issued-ID list."]


def test_a_not_covered_rule_without_an_issue_link_fails(tmp_path):
    register = _valid_register().replace(
        "[Issue #900](https://github.com/ShamikM88/open-cam-framework/issues/900)",
        "To be verified later.",
    )
    errors = _register_tree(tmp_path, register, {"test_cites.py": CITING_TEST})
    assert errors == ["Not covered rule R-POL-003 has no issue link."]


def test_a_citation_to_a_withdrawn_rule_fails(tmp_path):
    withdrawn = CITING_TEST.replace("R-POL-001", "R-POL-002")
    errors = _register_tree(tmp_path, _valid_register(), {"test_covered.py": CITING_TEST, "test_cites.py": withdrawn})
    assert errors == ["Withdrawn rule R-POL-002 is cited in tests/test_cites.py::test_cites; update the citation."]


def test_an_implemented_in_pointer_to_a_missing_file_fails(tmp_path):
    register = _valid_register().replace("scripts/synthetic_impl.py::_evaluate_covenant", "scripts/gone.py::_evaluate_covenant")
    errors = _register_tree(tmp_path, register, {"test_cites.py": CITING_TEST})
    assert errors == [
        "Rule R-POL-001: 'Implemented in' names 'scripts/gone.py::_evaluate_covenant', "
        "which does not exist in the repository."
    ]


def test_an_implemented_in_pointer_to_a_missing_function_fails(tmp_path):
    register = _valid_register().replace("_evaluate_covenant", "_missing_function")
    errors = _register_tree(tmp_path, register, {"test_cites.py": CITING_TEST})
    assert errors == [
        "Rule R-POL-001: 'Implemented in' names 'scripts/synthetic_impl.py::_missing_function', "
        "which is not defined in that file."
    ]


def test_an_implemented_in_method_pointer_resolves(tmp_path):
    impl = "class Handler:\n    def apply(self):\n        return None\n"
    register = _valid_register().replace(
        "scripts/synthetic_impl.py::_evaluate_covenant", "scripts/synthetic_impl.py::Handler.apply"
    )
    errors = _register_tree(
        tmp_path, register, {"test_cites.py": CITING_TEST},
        {"scripts/synthetic_impl.py": impl},
    )
    assert errors == []


def test_an_implemented_in_method_pointer_to_a_missing_method_fails(tmp_path):
    impl = "class Handler:\n    def other(self):\n        return None\n"
    register = _valid_register().replace(
        "scripts/synthetic_impl.py::_evaluate_covenant", "scripts/synthetic_impl.py::Handler.apply"
    )
    errors = _register_tree(
        tmp_path, register, {"test_cites.py": CITING_TEST},
        {"scripts/synthetic_impl.py": impl},
    )
    assert errors == [
        "Rule R-POL-001: 'Implemented in' names 'scripts/synthetic_impl.py::Handler.apply', "
        "which is not defined in that file."
    ]


def test_a_none_pointer_is_reserved_for_not_covered_rules(tmp_path):
    register = _valid_register().replace(
        "scripts/synthetic_impl.py::_evaluate_covenant", "none"
    )
    errors = _register_tree(tmp_path, register, {"test_cites.py": CITING_TEST})
    assert errors == ["Rule R-POL-001 is not Not covered, so 'Implemented in' cannot be none."]


def test_an_unknown_status_fails(tmp_path):
    register = _valid_register().replace("- **Status:** Covered", "- **Status:** Coverred")
    errors = _register_tree(tmp_path, register, {"test_cites.py": CITING_TEST})
    assert errors == [
        "Rule R-POL-001 has Status 'Coverred'; the page defines "
        "'Covered', 'Not covered' and 'Withdrawn'."
    ]


def test_a_malformed_citation_line_fails(tmp_path):
    malformed = CITING_TEST.replace("Rule: R-POL-001", "Rule: R-POL-1")
    errors = _register_tree(tmp_path, _valid_register(), {"test_covered.py": CITING_TEST, "test_cites.py": malformed})
    assert errors == [
        "Malformed citation 'Rule: R-POL-1' in tests/test_cites.py::test_cites; "
        "expected 'Rule: R-<AREA>-NNN'."
    ]


def test_a_citation_in_a_helper_or_module_docstring_does_not_count(tmp_path):
    stray = '''"""Module docstring.

Rule: R-POL-001
"""


def helper():
    """A helper is not a test.

    Rule: R-POL-001
    """
    return None


def test_empty():
    assert True
'''
    errors = _register_tree(tmp_path, _valid_register(), {"test_stray.py": stray})
    assert errors == ["Covered rule R-POL-001 has no citing test docstring."]


def test_a_citation_in_a_class_method_test_counts(tmp_path):
    in_class = '''class TestCovenants:
    def test_cites(self):
        """Cites the rule.

        Rule: R-POL-001
        """
        assert True
'''
    errors = _register_tree(tmp_path, _valid_register(), {"test_class.py": in_class})
    assert errors == []
