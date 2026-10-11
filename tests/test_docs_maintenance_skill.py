"""The maintainer procedure `docs-maintenance` (issue #196, Phase 3b).

Unlike `cam-change-verification` it EDITS files (documentation), so it removes no write tool and pre-approves none: each
edit goes through the maintainer's permission settings, which ask by default but can be set to allow or to skip prompts.
The limits on what it edits are therefore stated as the skill's own rule, and these tests check the file says them; they
cannot enforce them, and neither can a frontmatter field (a deny rule in the user's settings would).

What is pinned: typed by a person; unforked; pre-approvals are exact, non-writing commands (never `Edit` or `Write`, a
commit, a push, a merge, `gh`, `--update-snapshots`); the edit boundary (`docs/` and `README.md`; command text only when
asked; never `agents/*.md`, `CLAUDE.md`, source, tests, configuration, workflows, the move ledger or a snapshot); the
runbook's five documentation test files as one exact command; what the checks cannot confirm; and that what it cites exists.

These are CONTRACT tests of the file, not a run of the skill. Each check is also run on deliberately wrong input.
"""
import re
from pathlib import Path

import pytest
from test_command_flags import extract_invocations
from test_docs_contracts import cited_paths, missing_paths
from test_maintainer_skills import FORBIDDEN_IN_APPROVAL, TOOL_TOKEN, flat
from test_skill_inventory import REGISTRY, frontmatter, inventory_rows, invocation_of, read

REPO = Path(__file__).resolve().parents[1]
NAME = "docs-maintenance"
SKILL = f".claude/skills/{NAME}/SKILL.md"

READ_TOOLS = {"Read", "Glob", "Grep"}
ALLOWED_KEYS = {"name", "description", "argument-hint", "disable-model-invocation", "allowed-tools", "disallowed-tools"}
DOC_TESTS = ("tests/test_docs.py tests/test_docs_examples.py tests/test_docs_contracts.py "
             "tests/test_command_flags.py tests/test_prompt_consistency.py")
APPROVED_BASH = {
    "git status --short",
    "git branch --show-current",
    "git diff --name-only origin/main...HEAD",
    "git diff origin/main...HEAD",
    f"python -m pytest {DOC_TESTS}",
    "python scripts/pii_scan.py *",
}
# A tool that writes must never be pre-approved here: its prompt, not a pre-approval, is what the maintainer sees.
WRITING_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


def mutate(text, pattern, replacement):
    """Replace the first match of a whitespace-tolerant pattern; the match must exist, or the test proves nothing."""
    result, n = re.subn(pattern.replace(" ", r"\s+"), replacement, text, count=1, flags=re.S)
    assert n, f"the mutation matched nothing: {pattern}"
    return result


def skill_text():
    return read(SKILL)


def tools(fields, key):
    return TOOL_TOKEN.findall(fields.get(key, ""))


def frontmatter_problems(text, name=NAME):
    fields = frontmatter(text)
    problems = []
    if fields.get("name") != name:
        problems.append(f"name is {fields.get('name')!r}, not {name!r}")
    if invocation_of(fields) != "user":
        problems.append("it can be started by the model (disable-model-invocation must be true)")
    if "context" in fields:
        problems.append("it sets context: it must run in the maintainer's own session, not forked")
    if unknown := sorted(set(fields) - ALLOWED_KEYS):
        problems.append(f"unexpected frontmatter key(s) {unknown}")
    if "NotebookEdit" not in tools(fields, "disallowed-tools"):
        problems.append("disallowed-tools does not remove NotebookEdit")
    for token in tools(fields, "allowed-tools"):
        if token in READ_TOOLS:
            continue
        if token in WRITING_TOOLS:
            problems.append(f"allowed-tools pre-approves {token}, so an edit would not ask under the default settings")
            continue
        call = re.fullmatch(r"Bash\((.*)\)", token)
        if not call:
            problems.append(f"allowed-tools pre-approves {token}, which is neither a read tool nor an exact command")
        elif call[1] not in APPROVED_BASH:
            problems.append(f"allowed-tools pre-approves {call[1]!r}, which is not on the approved list")
        elif FORBIDDEN_IN_APPROVAL.search(call[1]):
            problems.append(f"the approved command {call[1]!r} contains something that changes the repository")
        elif "*" in call[1] and call[1] != "python scripts/pii_scan.py *":
            problems.append(f"{call[1]!r} is a wildcard pre-approval")
    description = fields.get("description", "")
    if not description or len(description) + len(fields.get("when_to_use", "")) > 1536:
        problems.append("description is missing or longer than the 1,536 characters Claude Code lists")
    if not fields.get("argument-hint"):
        problems.append("argument-hint is missing")
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        value = value.strip()
        if sep and not line.startswith(" ") and not value.startswith(("'", '"')) and (": " in value or " #" in value):
            problems.append(f"{key} holds ': ' or ' #' in an unquoted value, which is not valid YAML")
    return problems


def test_the_documentation_procedure_is_an_implemented_maintainer_skill():
    row = next(r for r in inventory_rows(read(REGISTRY)) if r["name"] == NAME)
    assert (row["kind"], row["audience"], row["status"], row["invocation"], row["role"], row["headless"]) == \
        ("skill", "maintainer", "implemented: phase 3", "user", "None (aid)", "no")


def test_it_is_typed_unforked_and_pre_approves_only_exact_non_writing_commands_never_an_edit():
    text = skill_text()
    assert frontmatter_problems(text) == []
    assert len(text.splitlines()) <= 500, "Claude Code's guidance for a SKILL.md"


def with_tools(text, extra):
    return text.replace("  - Bash(git status --short)\n", f"  - Bash(git status --short)\n  - {extra}\n", 1)


FRONTMATTER_MUTATIONS = [
    ("model-invocable", lambda t: t.replace("disable-model-invocation: true\n", ""), "can be started by the model"),
    ("forked", lambda t: t.replace("disable-model-invocation: true\n", "disable-model-invocation: true\ncontext: fork\n"),
     "must run in the maintainer's own session"),
    ("an edit pre-approved", lambda t: with_tools(t, "Edit"), "pre-approves Edit"),
    ("a write pre-approved", lambda t: with_tools(t, "Write"), "pre-approves Write"),
    ("a scoped edit pre-approved", lambda t: with_tools(t, "Edit(docs/**)"), "neither a read tool nor an exact command"),
    ("a hook", lambda t: t.replace("disable-model-invocation: true\n", "disable-model-invocation: true\nhooks: x\n"),
     "unexpected frontmatter key"),
    ("a commit", lambda t: with_tools(t, "Bash(git commit *)"), "not on the approved list"),
    ("a push", lambda t: with_tools(t, "Bash(git push)"), "not on the approved list"),
    ("a merge", lambda t: with_tools(t, "Bash(gh pr merge *)"), "not on the approved list"),
    ("a snapshot update", lambda t: with_tools(t, "Bash(python -m pytest tests/ --update-snapshots)"),
     "not on the approved list"),
    ("an open pytest wildcard", lambda t: with_tools(t, "Bash(python -m pytest *)"), "not on the approved list"),
    ("a different set of documentation tests",
     lambda t: t.replace("tests/test_command_flags.py tests/test_prompt_consistency.py)",
                         "tests/test_command_flags.py)", 1), "not on the approved list"),
    ("a fetch", lambda t: with_tools(t, "Bash(git fetch)"), "not on the approved list"),
    ("a bare Bash", lambda t: with_tools(t, "Bash"), "neither a read tool nor an exact command"),
    ("NotebookEdit allowed", lambda t: t.replace("disallowed-tools: NotebookEdit", ""), "does not remove NotebookEdit"),
    ("no argument hint", lambda t: t.replace('argument-hint: "[what changed: a file, flag, command, setting or decision, optional]"\n', ""),
     "argument-hint is missing"),
]


@pytest.mark.parametrize(("label", "change", "expected"), FRONTMATTER_MUTATIONS, ids=[m[0] for m in FRONTMATTER_MUTATIONS])
def test_the_frontmatter_check_fails_for_each_way_the_procedure_could_stop_being_safe(label, change, expected):
    changed = change(skill_text())
    assert changed != skill_text(), "the mutation changed nothing"
    assert any(expected in p for p in frontmatter_problems(changed)), frontmatter_problems(changed)


# ---------------------------------------------------------------------------
# The body: the edit boundary, the procedure's order, and what it cannot confirm
# ---------------------------------------------------------------------------

REQUIRED = {
    "edits only docs/ and README.md": r"It edits files under docs/ and README\.md, and only to make them describe the source accurately",
    "does not pre-approve Edit or Write": r"Edit and Write are not pre-approved here, so by default your permission settings ask",
    "names a mode that skips prompts": r"a mode that skips prompts \(such as bypassPermissions\)",
    "does not rely on a prompt for its boundary": r"It does not rely on a prompt: the list below is its own rule in every mode",
    "says the test run writes only git-ignored caches": r"Running the documentation tests writes only git-ignored caches",
    "edits command text only when asked": r"command text \(\.claude/commands/\) only when the maintainer asks",
    "never edits an agent prompt": r"It never edits an agent prompt \(agents/\.md\)",
    "stops and points to the prompt-change process": r"it stops there, says so, and points to \[the prompt-change process\]",
    "does not edit CLAUDE.md": r"It does not edit CLAUDE\.md",
    "never edits source, tests, configuration or a workflow": r"It never edits source, tests, configuration or a workflow to make a page true",
    "never regenerates a golden snapshot": r"never regenerates a golden snapshot \(--update-snapshots\)",
    "never hand-edits a quoted figure": r"never hand-edits a figure that a worked example quotes",
    "never touches the move ledger": r"never touches the move ledger \(docs/move-ledger\.md",
    "never commits, pushes, merges": r"never commits, pushes, merges, rebases, resets, stashes or cleans",
    "never runs gh": r"never runs gh",
    "makes no network or model call": r"makes no network or model call",
    "leaves Wiki publication separate": r"Wiki publication is a separate workstream \(#192\)",
    "treats the source as defining the system": r"The source defines the system and the page describes it",
    "does not judge the design": r"does not judge the design",
    "limits its strongest statement": r"No stale page found by these checks and this reading",
    "does not fetch": r"do not fetch",
    "keeps evergreen pages evergreen": r"keep evergreen pages evergreen \(no dates, .not yet. or current counts",
    "reads each page against the diff": r"Read each page against the diff, not only the names",
    "knows the checks miss changed behaviour": r"they do not catch a behaviour that changed under an unchanged name",
    "reports a wrong-looking source instead of changing it": r"do not change the source: report the disagreement",
    "fixes the page, never the check": r"fix the page \(never the check\)",
    "runs the five documentation tests": r"python -m pytest " + re.escape(DOC_TESTS),
    "treats a clean scan as no guarantee": r"A clean result is not a guarantee",
    "states what the checks cannot confirm": r"what the checks cannot confirm",
    "never claims a page confirmed without reading it": r"Never describe a page as confirmed unless you read it against the source in this session",
    "never claims a check passed without its output": r"never describe a check as passed unless its command ran in this session",
    "reads no deal": r"reads no state\.json",
    "is never input to the Maker or the Checker": r"never input to the Maker or the Checker",
}
ORDER = ("1. What changed.", "2. Find what describes it.", "3. Read each page against the diff,",
         "4. Where the page and the source disagree", "5. Run the documentation checks:", "6. Added text.")


def body_problems(text):
    body = flat(text.split("---", 2)[-1])
    problems = []
    for label, pattern in REQUIRED.items():
        if not re.search(pattern, body, re.I):
            problems.append(f"the procedure no longer {label}")
    positions = [body.find(step) for step in ORDER]
    if -1 in positions or positions != sorted(positions):
        problems.append("the steps are missing or out of order")
    for command in ("git status --short", "git branch --show-current", "git diff --name-only origin/main...HEAD",
                    "git diff origin/main...HEAD"):
        if command not in body:
            problems.append(f"the procedure no longer shows the pre-approved command {command!r}")
    for place in ("docs/", "README.md", "CLAUDE.md", "evals/README.md", ".claude/commands/"):
        if place not in body:
            problems.append(f"the search no longer covers {place}")
    return problems


def test_the_procedure_states_its_edit_boundary_runs_the_runbooks_checks_in_order_and_says_what_they_cannot_confirm():
    assert body_problems(skill_text()) == []


def drop(pattern, replacement=""):
    return lambda t: mutate(t, pattern, replacement)


BODY_MUTATIONS = [
    ("edits anywhere", drop(r"and only to make them describe the source accurately", "and anything else"),
     "edits only docs/ and README.md"),
    ("claims a prompt keeps the boundary", drop(r"It does not\s+rely on a prompt: the list below is its own rule in every mode", "A prompt keeps it inside."),
     "does not rely on a prompt for its boundary"),
    ("hides the test run's caches", drop(r"Running the documentation tests writes only git-ignored\s+caches", "Nothing else is written"),
     "says the test run writes only git-ignored caches"),
    ("drops the bypass caveat", drop(r"a mode that skips prompts \(such as `bypassPermissions`\)", "nothing"),
     "names a mode that skips prompts"),
    ("edits a prompt", drop(r"It never edits an agent prompt", "It edits an agent prompt"), "never edits an agent prompt"),
    ("edits CLAUDE.md", drop(r"It does not edit `CLAUDE.md`", "It edits `CLAUDE.md`"), "does not edit CLAUDE.md"),
    ("edits source", drop(r"It never edits source, tests, configuration or a workflow", "It edits source"),
     "never edits source, tests, configuration or a workflow"),
    ("updates snapshots", drop(r"never regenerates a golden snapshot", "regenerates snapshots"), "never regenerates a golden snapshot"),
    ("hand-edits figures", drop(r"never hand-edits a figure that a worked example quotes", "edits figures"),
     "never hand-edits a quoted figure"),
    ("touches the move ledger", drop(r"\*\*never touches the move ledger\*\*", "updates the move ledger"),
     "never touches the move ledger"),
    ("edits command text freely", drop(r"\*\*only when the maintainer asks\*\*", "when it likes"),
     "edits command text only when asked"),
    ("commits", drop(r"never commits, pushes, merges, rebases, resets, stashes or cleans", "may commit"),
     "never commits, pushes, merges"),
    ("publishes the Wiki", drop(r"Wiki publication is a separate workstream", "It also publishes the Wiki"),
     "leaves Wiki publication separate"),
    ("changes the source to match the page", drop(r"do not change the source: report the disagreement", "change the source"),
     "reports a wrong-looking source instead of changing it"),
    ("weakens the check", drop(r"fix the\s+page \(never the check\)", "fix the check"), "fixes the page, never the check"),
    ("drops the unchanged-name caveat", drop(r"they do not catch a\s+behaviour that changed under an unchanged name", "they catch everything"),
     "knows the checks miss changed behaviour"),
    ("approves", drop(r"\"No stale page found by these checks and this reading\"", "\"All docs are correct\""),
     "limits its strongest statement"),
    ("fetches", drop(r"do not fetch", "fetch it"), "does not fetch"),
    ("a different test set", lambda t: t[:t.rfind("tests/test_command_flags.py tests/test_prompt_consistency.py")]
     + "tests/test_command_flags.py" + t[t.rfind("tests/test_command_flags.py tests/test_prompt_consistency.py")
                                          + len("tests/test_command_flags.py tests/test_prompt_consistency.py"):],
     "runs the five documentation tests"),
    ("reorders the steps", lambda t: t.replace("5. **Run the documentation checks:**", "2. **Run the documentation checks:**", 1),
     "out of order"),
    ("drops a search location", lambda t: t.replace("`evals/README.md` and `.claude/commands/`", "`.claude/commands/`", 1),
     "the search no longer covers evals/README.md"),
    ("claims a page confirmed", drop(r"Never describe a page as confirmed unless you read it against the source in this session",
                                    "Describe each page as confirmed"), "never claims a page confirmed without reading it"),
]


@pytest.mark.parametrize(("label", "change", "expected"), BODY_MUTATIONS, ids=[m[0] for m in BODY_MUTATIONS])
def test_the_body_check_fails_for_each_regression_it_exists_to_catch(label, change, expected):
    changed = change(skill_text())
    assert changed != skill_text(), "the mutation changed nothing"
    assert any(expected in p for p in body_problems(changed)), body_problems(changed)


# ---------------------------------------------------------------------------
# What it cites and runs is real; the pages say what it is
# ---------------------------------------------------------------------------

def test_every_path_and_anchor_the_procedure_cites_exists_and_the_only_script_it_runs_is_the_text_scan():
    text = skill_text()
    cited = cited_paths(text)
    assert cited and missing_paths(cited) == []
    assert {script for _, script, _ in extract_invocations(text)} <= {"pii_scan"}
    assert not [flag for _, _, found in extract_invocations(text) for flag in found], "it passes no flag to a script"
    for page, anchor in re.findall(r"\(\.\./\.\./\.\./docs/([a-z-]+\.md)#([a-z0-9-]+)\)", text):
        headings = {re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-")
                    for h in re.findall(r"^#+\s+(.+)$", read(f"docs/{page}"), re.M)}
        assert anchor in headings, (page, anchor)
    for test_file in DOC_TESTS.split():
        assert (REPO / test_file).is_file(), test_file


def test_the_runbook_names_the_same_five_documentation_test_files():
    """The skill's one pre-approved pytest command is the runbook's, so a change to one must change the other."""
    runbook = read("docs/operations.md")
    assert "pytest " + DOC_TESTS in runbook


def test_the_pages_describe_the_procedure_as_implemented_and_name_its_limits():
    skills, design = flat(read("docs/skills.md")), flat(read("docs/skill-design.md"))
    for needle in ("docs-maintenance", "It does not pre-approve Edit or Write, so by default each edit asks you",
                   "the edit boundary above is therefore the skill's own rule in every mode",
                   "not that every sentence is true", "Wiki publication (#192) is separate"):
        assert needle in skills, needle
    for needle in ("Maintainer procedures (done)", "it edits files, so it does not pre-approve Edit or Write",
                   "as its own rule"):
        assert needle in design, needle
    assert "test_docs_maintenance_skill" in flat(read("docs/testing.md"))


def test_the_procedure_carries_no_real_name_figure_or_contact():
    from pii_scan import scan_for_likely_real_data
    assert scan_for_likely_real_data(skill_text()) == []
