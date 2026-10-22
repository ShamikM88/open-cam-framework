"""The maintainer procedure `cam-change-verification` (issue #196, Phase 3a).

Unlike an analyst aid it runs in the maintainer's own session (not forked), runs the test suite and so writes coverage
files, and is for the person who maintains the repository. What it may never do is the same kind of boundary: it is typed
by a person, it removes the write tools while it runs, it pre-approves only exact, non-writing commands (so a commit, a
push, a merge, `gh`, `--write` and `--update-snapshots` are not pre-approved), and it never becomes input to the Maker or
the Checker. A command that is not pre-approved is left to the user's permission settings, which ask by default but can be
set to allow or to skip prompts, so the skill states the limits as its own procedure and does not rely on a prompt.

These are CONTRACT tests: they read the skill file and fail if a property the design depends on is removed. They do not
run the skill, and a pre-approval is Claude Code's behaviour, not something this suite proves. Each check is a function
over text and is also run on deliberately wrong input so it is shown to fail.
"""
import re
from pathlib import Path

import pytest
from test_command_flags import extract_invocations
from test_docs_contracts import cited_paths, missing_paths
from test_skill_inventory import REGISTRY, frontmatter, inventory_rows, invocation_of, read

REPO = Path(__file__).resolve().parents[1]
NAME = "cam-change-verification"
SKILL = f".claude/skills/{NAME}/SKILL.md"

READ_TOOLS = {"Read", "Glob", "Grep"}
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
ALLOWED_KEYS = {"name", "description", "argument-hint", "disable-model-invocation", "allowed-tools", "disallowed-tools"}
TOOL_TOKEN = re.compile(r"Bash\([^)]*\)|\S+")

# The only commands the procedure may pre-approve. All but the last are exact strings (no wildcard); `pii_scan.py`
# reads one file and prints findings.
APPROVED_BASH = {
    "git status --short",
    "git branch --show-current",
    "git diff --name-only origin/main...HEAD",
    "git diff --stat origin/main...HEAD",
    "ruff check .",
    "python -m ruff check .",
    "bandit -r scripts/ -ll",
    "python -m bandit -r scripts/ -ll",
    "python -m pytest tests/ --cov --cov-report=term --cov-report=xml --cov-report=json",
    "tee pytest_output.txt",
    "python scripts/check_test_count.py pytest_output.txt",
    "python scripts/check_coverage.py coverage.json",
    "diff-cover coverage.xml --config-file pyproject.toml",
    "diff-cover coverage.xml --config-file diff_cover_critical.toml",
    "python scripts/pii_scan.py *",
}
# Anything in a pre-approval that changes a repository, a branch, a remote, an environment or a snapshot.
FORBIDDEN_IN_APPROVAL = re.compile(
    r"\b(commit|push|pull|fetch|merge|rebase|reset|stash|clean|checkout|switch|restore|add|rm|mv|cp|gh|curl|wget|pip|install|"
    r"sed|awk|chmod)\b|--write|--update-snapshots|--output|--fix|--force|-delete|>")


def skill_text():
    return read(SKILL)


def flat(text):
    return " ".join(text.replace("*", "").replace("`", "").split())


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
        problems.append(f"unexpected frontmatter key(s) {unknown} (a hook, model or agent override changes what runs)")
    if missing := sorted(WRITE_TOOLS - set(tools(fields, "disallowed-tools"))):
        problems.append(f"disallowed-tools does not remove {missing}")
    for token in tools(fields, "allowed-tools"):
        if token in READ_TOOLS:
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


def test_the_maintainer_skills_of_phase_3_are_the_implemented_ones():
    rows = [r for r in inventory_rows(read(REGISTRY)) if r["kind"] == "skill" and r["audience"] == "maintainer"]
    implemented = sorted(r["name"] for r in rows if r["status"].startswith("implemented"))
    assert implemented == [NAME, "docs-maintenance", "mutation-triage", "prompt-change-gate"]  # Phase 4
    assert [r["name"] for r in rows if r["status"].startswith("planned")] == []


def test_the_procedure_is_typed_unforked_cannot_write_and_pre_approves_only_exact_non_writing_commands():
    text = skill_text()
    assert frontmatter_problems(text) == []
    assert len(text.splitlines()) <= 500, "Claude Code's guidance for a SKILL.md"


def with_tools(text, extra):
    return text.replace("  - Bash(git status --short)\n", f"  - Bash(git status --short)\n  - {extra}\n", 1)


FRONTMATTER_MUTATIONS = [
    ("model-invocable", lambda t: t.replace("disable-model-invocation: true\n", ""), "can be started by the model"),
    ("forked", lambda t: t.replace("disable-model-invocation: true\n", "disable-model-invocation: true\ncontext: fork\n"),
     "must run in the maintainer's own session"),
    ("write tools not removed", lambda t: t.replace("disallowed-tools: Write Edit NotebookEdit", "disallowed-tools: Write"),
     "does not remove ['Edit', 'NotebookEdit']"),
    ("a hook", lambda t: t.replace("disable-model-invocation: true\n", "disable-model-invocation: true\nhooks: x\n"),
     "unexpected frontmatter key"),
    ("a commit", lambda t: with_tools(t, "Bash(git commit *)"), "not on the approved list"),
    ("a push", lambda t: with_tools(t, "Bash(git push)"), "not on the approved list"),
    ("a merge", lambda t: with_tools(t, "Bash(gh pr merge *)"), "not on the approved list"),
    ("the badge write", lambda t: with_tools(t, "Bash(python scripts/check_test_count.py pytest_output.txt --write)"),
     "not on the approved list"),
    ("a golden snapshot update", lambda t: with_tools(t, "Bash(python -m pytest tests/ --update-snapshots)"),
     "not on the approved list"),
    ("an open pytest wildcard", lambda t: with_tools(t, "Bash(python -m pytest *)"), "not on the approved list"),
    ("a bare Bash", lambda t: with_tools(t, "Bash"), "neither a read tool nor an exact command"),
    ("a write tool pre-approved", lambda t: with_tools(t, "Write"), "neither a read tool nor an exact command"),
    ("a fetch", lambda t: with_tools(t, "Bash(git fetch)"), "not on the approved list"),
    ("an unquoted colon", lambda t: t.replace("argument-hint: \"[what the change is about, optional]\"",
                                             "argument-hint: [a: b]"), "not valid YAML"),
    ("no argument hint", lambda t: t.replace("argument-hint: \"[what the change is about, optional]\"\n", ""),
     "argument-hint is missing"),
]


@pytest.mark.parametrize(("label", "change", "expected"), FRONTMATTER_MUTATIONS, ids=[m[0] for m in FRONTMATTER_MUTATIONS])
def test_the_frontmatter_check_reports_each_way_the_procedure_could_stop_being_safe(label, change, expected):
    changed = change(skill_text())
    assert changed != skill_text(), "the mutation changed nothing"
    assert any(expected in p for p in frontmatter_problems(changed)), frontmatter_problems(changed)


def test_the_approval_check_treats_a_forbidden_word_in_an_approved_looking_command_as_a_problem(monkeypatch):
    import test_maintainer_skills as module
    monkeypatch.setattr(module, "APPROVED_BASH", {*APPROVED_BASH, "git commit -m x"})
    changed = with_tools(skill_text(), "Bash(git commit -m x)")
    assert any("changes the repository" in p for p in module.frontmatter_problems(changed))


# ---------------------------------------------------------------------------
# The procedure's body: the order, the limits it states, and what it never does
# ---------------------------------------------------------------------------

REQUIRED = {
    "never commits, pushes, merges, rebases, resets, stashes or cleans":
        r"never commits, pushes, merges, rebases, resets, stashes or cleans",
    "never runs gh": r"never runs gh",
    "never changes a setting, ruleset or credential": r"never changes a repository setting, ruleset or credential",
    "makes no ad hoc edit to a tracked file": r"makes no ad hoc edit to source, tests, documentation, configuration or any other tracked file",
    "says the checks write local artefacts": r"the checks it runs generate local artefacts \(pytest_output\.txt, coverage\.xml, coverage\.json and coverage data, ignored by git\)",
    "says the badge update is a separate gated write": r"the test-count badge update below is a separate, explicitly gated write",
    "never regenerates a golden snapshot": r"never regenerates a golden snapshot \(--update-snapshots\)",
    "stops at an agent prompt edit": r"never edits an agent prompt \(agents/\.md\); if the change does, it stops there",
    "makes no network or model call": r"makes no network or model call",
    "never hand-edits the badge": r"never hand-edits the test-count badge",
    "keeps --write out of the pre-approvals": r"deliberately not pre-approved, so by default your permission settings ask",
    "keeps the go-ahead a rule even without a prompt": r"the go-ahead is still the rule even if they do not",
    "says what holds the limits": r"What holds these limits\. This procedure, and the removal of the write tools",
    "says not pre-approved only means not pre-approved": r"Not pre-approving a command only means it is not pre-approved",
    "names a mode that skips prompts": r"a mode that skips prompts \(such as bypassPermissions\)",
    "does not rely on a prompt": r"never relies on a prompt to stay inside this list",
    "points to deny rules for enforcement": r"add deny rules to your permission settings",
    "offers --write only after a complete green run": r"only if the run was complete and green",
    "waits for the go-ahead": r"wait for the maintainer's go-ahead before running it",
    "does not judge the design": r"does not judge the design",
    "reports a known xfail apart from a failure": r"keep a known strict xfail \(it names an issue\) apart from a new failure",
    "does not call an incomplete run green": r"stop reporting .green.",
    "never runs diff-cover without its config": r"never run it without",
    "never lowers a floor": r"floor is never lowered to pass",
    "does not fetch": r"do not fetch",
    "treats a clean scan as no guarantee": r"A clean result is not a guarantee",
    "reports leftovers without removing them": r"reported, not removed",
    "states a green local run is not a green CI run": r"A green local run is not a green CI run",
    "never reports a step as passed without its output": r"Never describe a step as passed unless its command ran in this session",
    "names the Windows job as CI only": r"checks only CI can show.{0,200}Windows job",
    "names runtime-smoke as CI only": r"clean-environment runtime-smoke job",
    "names pip-audit, zizmor and CodeQL as CI only": r"pip-audit, zizmor and CodeQL",
    "limits its strongest statement": r"No blocker found by these checks",
    "reads no deal": r"reads no state\.json",
    "is never input to the Maker or the Checker": r"never input to the Maker or the Checker",
}
ORDER = ("1. What changed.", "2. Targeted tests first", "3. Lint and security scan.", "4. The full suite with coverage",
         "5. The test-count badge.", "6. Coverage.", "7. Documentation checks.", "8. Added text.", "9. Clean tree.")
SENSITIVE_PATHS = ("agents/", "config/", "scripts/", ".claude/", ".github/", "pyproject.toml", "requirements",
                   "badges/", "tests/snapshots/")


def body_problems(text):
    body = flat(text.split("---", 2)[-1])
    problems = []
    for label, pattern in REQUIRED.items():
        if not re.search(pattern, body, re.I):
            problems.append(f"the procedure no longer {label}")
    positions = [body.find(step) for step in ORDER]
    if -1 in positions or positions != sorted(positions):
        problems.append("the steps are missing or out of order")
    for command in sorted(APPROVED_BASH - {"python scripts/pii_scan.py *", "tee pytest_output.txt",
                                           "python -m ruff check .", "python -m bandit -r scripts/ -ll"}):
        shown = command if command.startswith(("git", "python -m pytest")) else command.replace("python -m pytest", "pytest")
        if command not in body and shown not in body:
            problems.append(f"the procedure no longer shows the pre-approved command {command!r}")
    for path in SENSITIVE_PATHS:
        if path not in body:
            problems.append(f"the sensitive path {path} is no longer listed")
    # --write is never mentioned without the go-ahead being near it
    for match in re.finditer(r"--write", body):
        near = body[max(0, match.start() - 400):match.end() + 400]
        if "go-ahead" not in near:
            problems.append("--write appears without the maintainer's go-ahead nearby")
            break
    return problems


def test_the_procedure_runs_the_runbooks_checks_in_order_and_states_what_it_will_not_do():
    assert body_problems(skill_text()) == []


def drop(pattern, replacement=""):
    return lambda t: re.sub(pattern.replace(" ", r"\s+"), replacement, t, count=1, flags=re.S)


BODY_MUTATIONS = [
    ("commits", drop(r"never commits, pushes, merges, rebases, resets, stashes or cleans", "may commit"),
     "never commits, pushes, merges"),
    ("runs gh", drop(r"never runs `gh`", "runs gh"), "never runs gh"),
    ("edits files", drop(r"\*\*makes no ad hoc edit\*\* to source, tests, documentation, configuration or any other tracked file",
                         "edits files"), "makes no ad hoc edit to a tracked file"),
    ("claims it writes nothing", drop(r"the checks it runs generate local artefacts", "nothing is written"),
     "says the checks write local artefacts"),
    ("hides the badge write", drop(r"is a separate, explicitly gated write", "is part of the run"),
     "says the badge update is a separate gated write"),
    ("updates snapshots", drop(r"and never regenerates a golden snapshot", "and regenerates snapshots"),
     "never regenerates a golden snapshot"),
    ("edits a prompt", drop(r"It never edits an agent prompt", "It edits an agent prompt"), "stops at an agent prompt edit"),
    ("hand-edits the badge", drop(r"never hand-edits the test-count badge", "edits the badge"), "never hand-edits the badge"),
    ("pre-approves --write", drop(r"deliberately not pre-approved, so by default your permission settings ask", "pre-approved"),
     "keeps --write out of the pre-approvals"),
    ("relies on a prompt", drop(r"So it never relies on a prompt to stay inside this list", "So the prompt keeps it inside this list"),
     "does not rely on a prompt"),
    ("drops the bypass caveat", drop(r"a mode that skips prompts \(such as `bypassPermissions`\)", "nothing"),
     "names a mode that skips prompts"),
    ("claims the absence of pre-approval is a restriction",
     drop(r"Not pre-approving a command only\s+means it is not pre-approved", "Anything not pre-approved is blocked"),
     "says not pre-approved only means not pre-approved"),
    ("drops the deny-rule pointer", drop(r"add deny rules to your permission settings", "nothing more is needed"),
     "points to deny rules for enforcement"),
    ("makes the go-ahead depend on a prompt", drop(r"the go-ahead is still the rule even if they do not", "the prompt is the go-ahead"),
     "keeps the go-ahead a rule even without a prompt"),
    ("writes the badge on a red run", drop(r"\*\*only\*\* if the run was complete and green", "whenever"),
     "offers --write only after a complete green run"),
    ("writes without the go-ahead", drop(r"wait for the maintainer's go-ahead before running it", "run it"),
     "waits for the go-ahead"),
    ("calls an incomplete run green", drop(r"stop reporting \"green\"", "keep going"), "does not call an incomplete run green"),
    ("diff-cover without its config", drop(r"so never run it without", ""), "never runs diff-cover without its config"),
    ("fetches", drop(r"do not fetch", "fetch it"), "does not fetch"),
    ("claims CI parity", drop(r"A green local run is not a green CI run", "A green local run is enough"),
     "states a green local run is not a green CI run"),
    ("drops the CI-only list", drop(r"`pip-audit`, `zizmor` and CodeQL", "nothing"), "names pip-audit, zizmor and CodeQL"),
    ("approves the change", drop(r"\"No blocker found by these checks\"", "\"Approved\""), "limits its strongest statement"),
    ("reorders the steps", lambda t: t.replace("3. **Lint and security scan.**", "8. **Lint and security scan.**", 1),
     "out of order"),
    ("loses the coverage step", lambda t: t.replace("6. **Coverage.**", "6. **Numbers.**", 1), "out of order"),
    ("a sensitive path dropped", lambda t: t.replace("`.github/`, ", "", 1), "the sensitive path .github/"),
    ("the full-suite command dropped",
     lambda t: t[:t.rfind("python -m pytest tests/ --cov")] + "python -m pytest tests/" + t[t.rfind("python -m pytest tests/ --cov") + 29:],
     "no longer shows the pre-approved command"),
    ("the write flag without a go-ahead nearby",
     lambda t: t + "\n\n" + ("filler sentence. " * 80) + "\n\nRun `python scripts/check_test_count.py pytest_output.txt --write`.\n",
     "--write appears without"),
]


@pytest.mark.parametrize(("label", "change", "expected"), BODY_MUTATIONS, ids=[m[0] for m in BODY_MUTATIONS])
def test_the_body_check_fails_for_each_regression_it_exists_to_catch(label, change, expected):
    changed = change(skill_text())
    assert changed != skill_text(), "the mutation changed nothing"
    assert any(expected in p for p in body_problems(changed)), body_problems(changed)


# ---------------------------------------------------------------------------
# What it cites and runs is real; it names nothing it must not; the pages say what it is
# ---------------------------------------------------------------------------

def test_every_repository_path_the_procedure_cites_exists_and_every_script_it_runs_is_a_checker_not_a_writer():
    text = skill_text()
    cited = cited_paths(text)
    assert cited and missing_paths(cited) == []
    scripts = {script for _, script, _ in extract_invocations(text)}
    assert scripts <= {"check_test_count", "check_coverage", "pii_scan"}, scripts
    flags = {flag for _, _, found in extract_invocations(text) for flag in found}
    assert flags <= {"--write"}, "the only flag it passes to a script is the gated --write"


def test_the_runbook_anchors_the_procedure_links_to_exist():
    text = skill_text()
    for page, anchor in re.findall(r"\(\.\./\.\./\.\./docs/([a-z-]+\.md)#([a-z0-9-]+)\)", text):
        headings = {re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-")
                    for h in re.findall(r"^#+\s+(.+)$", read(f"docs/{page}"), re.M)}
        assert anchor in headings, (page, anchor)


def test_the_pages_describe_the_procedure_as_implemented_and_name_its_limits():
    skills, design = flat(read("docs/skills.md")), flat(read("docs/skill-design.md"))
    for needle in ("The maintainer procedure", "cam-change-verification", "it is not forked",
                   "Everything else, including a targeted pytest run and check_test_count.py --write, is not pre-approved",
                   "by default they ask you", "What actually holds the limits", "a mode that skips prompts (such as bypassPermissions)",
                   "No test here, and no frontmatter field, can enforce it",
                   "so it makes no ad hoc edit to any tracked file",
                   "the checks generate local artefacts, and the badge update is a separate, explicitly gated write",
                   "a green local run is not a green CI run", "no blocker found by these checks"):
        assert needle in skills, needle
    for needle in ("Maintainer procedures (done)", "A procedure skill differs in two ways", "deliberately not pre-approved",
                   "ask by default but can be set to allow or to skip prompts", "a deny rule in the settings is what would enforce it"):
        assert needle in design, needle
    row = next(r for r in inventory_rows(read(REGISTRY)) if r["name"] == NAME)
    assert row["status"] == "implemented: phase 3" and row["invocation"] == "user" and row["role"] == "None (aid)"


def test_the_procedure_carries_no_real_name_figure_or_contact():
    from pii_scan import scan_for_likely_real_data
    assert scan_for_likely_real_data(skill_text()) == []
