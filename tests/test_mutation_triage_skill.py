"""The maintainer report `mutation-triage` (issue #196, Phase 4, from the Step 5 recommendation).

It is a *report* in the skill taxonomy: forked, typed by a person, read-only, and for a maintainer rather than an analyst
(its input is a downloaded mutation report, not a deal). It pre-approves only `Read`, `Glob` and `Grep`, and removes
`Write`, `Edit`, `NotebookEdit` and `Bash` while it runs, so it can run no `mutmut`, no test and no script, and edits and
files nothing. A tool that is not removed stays under the user's permission settings, which ask by default but can be set to
allow or to skip prompts, so the limits are also stated as the skill's own rule; these tests check that the file says them and
cannot enforce them, and neither can a frontmatter field (a deny rule in the user's settings would).

What is pinned: typed by a person; forked; the read tools and nothing else pre-approved; the write tools and `Bash` removed;
the runbook's steps in order; that it never changes the mutation scope, target, configuration or workflow; that every class is
a candidate until the mutated line has been seen; the shape of the report it reads (checked against the script that writes
it) and the files the workflow uploads; and that what it cites exists.

These are CONTRACT tests of the file, not a run of the skill. Each check is also run on deliberately wrong input.
"""
import re
from pathlib import Path

import pytest
from test_command_flags import extract_invocations
from test_docs_contracts import cited_paths, missing_paths
from test_maintainer_skills import TOOL_TOKEN, flat
from test_skill_inventory import REGISTRY, frontmatter, inventory_rows, invocation_of, read

REPO = Path(__file__).resolve().parents[1]
NAME = "mutation-triage"
SKILL = f".claude/skills/{NAME}/SKILL.md"

READ_TOOLS = {"Read", "Glob", "Grep"}
REMOVED_TOOLS = {"Write", "Edit", "NotebookEdit", "Bash", "WebFetch", "WebSearch"}
ALLOWED_KEYS = {"name", "description", "argument-hint", "disable-model-invocation", "context", "background",
                "allowed-tools", "disallowed-tools"}


def mutate(text, pattern, replacement):
    """Replace the first match of a whitespace-tolerant pattern; the match must exist, or the test proves nothing."""
    result, n = re.subn(pattern.replace(" ", r"\s+"), replacement, text, count=1, flags=re.S)
    assert n, f"the mutation matched nothing: {pattern}"
    return result


def skill_text():
    return read(SKILL)


def tools(fields, key):
    return TOOL_TOKEN.findall(fields.get(key, ""))


def remove_network_tool(text, tool):
    """Start from valid frontmatter then remove one enforced network-tool deny."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("disallowed-tools:"):
            tokens = line.partition(":")[2].split()
            if not {"WebFetch", "WebSearch"} <= set(tokens):
                tokens.extend(tool_name for tool_name in ("WebFetch", "WebSearch") if tool_name not in tokens)
            tokens.remove(tool)
            lines[index] = "disallowed-tools: " + " ".join(tokens)
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    raise AssertionError("frontmatter has no disallowed-tools line")


# ---------------------------------------------------------------------------
# The inventory row and the frontmatter
# ---------------------------------------------------------------------------

def test_mutation_triage_is_an_implemented_maintainer_skill_of_phase_4():
    row = next(r for r in inventory_rows(read(REGISTRY)) if r["name"] == NAME)
    assert (row["kind"], row["audience"], row["status"], row["invocation"], row["role"], row["headless"]) == \
        ("skill", "maintainer", "implemented: phase 4", "user", "None (aid)", "no")


def skill_text_with_network_denies():
    """Return a valid baseline for isolated mutation cases, even before the skill file is updated."""
    text = skill_text()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("disallowed-tools:"):
            tokens = line.partition(":")[2].split()
            tokens.extend(tool for tool in ("WebFetch", "WebSearch") if tool not in tokens)
            lines[index] = "disallowed-tools: " + " ".join(tokens)
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    raise AssertionError("frontmatter has no disallowed-tools line")


def frontmatter_problems(text, name=NAME):
    fields = frontmatter(text)
    problems = []
    if fields.get("name") != name:
        problems.append(f"name is {fields.get('name')!r}, not {name!r}")
    if invocation_of(fields) != "user":
        problems.append("it can be started by the model (disable-model-invocation must be true)")
    if fields.get("context") != "fork":
        problems.append("it is not forked (context: fork): a report runs apart from the maintainer's conversation")
    if fields.get("background", "").lower() != "false":
        problems.append("background must be false so the report comes back in the same turn")
    if unknown := sorted(set(fields) - ALLOWED_KEYS):
        problems.append(f"unexpected frontmatter key(s) {unknown} (a hook, model or agent override changes what runs)")
    allowed = tools(fields, "allowed-tools")
    if set(allowed) != READ_TOOLS or len(allowed) != len(READ_TOOLS):
        problems.append(f"allowed-tools is {allowed}: it must pre-approve exactly Read, Glob and Grep and no command")
    if missing := sorted(REMOVED_TOOLS - set(tools(fields, "disallowed-tools"))):
        problems.append(f"disallowed-tools does not remove {missing}")
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


def test_it_is_typed_forked_pre_approves_only_the_read_tools_and_removes_the_write_tools_and_bash():
    text = skill_text()
    assert frontmatter_problems(text) == []
    assert len(text.splitlines()) <= 500, "Claude Code's guidance for a SKILL.md"


FRONTMATTER_MUTATIONS = [
    ("model-invocable", lambda t: t.replace("disable-model-invocation: true\n", ""), "can be started by the model"),
    ("not forked", lambda t: t.replace("context: fork\n", ""), "it is not forked"),
    ("background", lambda t: t.replace("background: false\n", "background: true\n"), "background must be false"),
    ("a hook", lambda t: t.replace("disable-model-invocation: true\n", "disable-model-invocation: true\nhooks: x\n"),
     "unexpected frontmatter key"),
    ("a command pre-approved", lambda t: t.replace("  - Grep\n", "  - Grep\n  - Bash(python scripts/mutation_report.py *)\n", 1),
     "no command"),
    ("a bare Bash pre-approved", lambda t: t.replace("  - Grep\n", "  - Grep\n  - Bash\n", 1), "no command"),
    ("an edit pre-approved", lambda t: t.replace("  - Grep\n", "  - Grep\n  - Edit\n", 1), "no command"),
    ("a read tool dropped", lambda t: t.replace("  - Glob\n", "", 1), "exactly Read, Glob and Grep"),
    ("Bash not removed", lambda t: t.replace("Write Edit NotebookEdit Bash WebFetch WebSearch", "Write Edit NotebookEdit WebFetch WebSearch"),
     "does not remove ['Bash']"),
    ("Write not removed", lambda t: t.replace("Write Edit NotebookEdit Bash WebFetch WebSearch", "Edit NotebookEdit Bash WebFetch WebSearch"),
     "does not remove ['Write']"),
    ("NotebookEdit not removed", lambda t: t.replace("Write Edit NotebookEdit Bash WebFetch WebSearch", "Write Edit Bash WebFetch WebSearch"),
     "does not remove ['NotebookEdit']"),
    ("WebFetch not removed", lambda t: remove_network_tool(t, "WebFetch"), "does not remove ['WebFetch']"),
    ("WebSearch not removed", lambda t: remove_network_tool(t, "WebSearch"), "does not remove ['WebSearch']"),
    ("no argument hint", lambda t: re.sub(r"argument-hint: .*\n", "", t, count=1), "argument-hint is missing"),
    ("unquoted colon", lambda t: t.replace('argument-hint: "', "argument-hint: path: ", 1).replace('"\n', "\n", 1),
     "not valid YAML"),
]


@pytest.mark.parametrize(("label", "change", "expected"), FRONTMATTER_MUTATIONS, ids=[m[0] for m in FRONTMATTER_MUTATIONS])
def test_the_frontmatter_check_fails_for_each_way_the_report_could_stop_being_read_only(label, change, expected):
    baseline = skill_text_with_network_denies()
    changed = change(baseline)
    assert changed != baseline, "the mutation changed nothing"
    assert any(expected in p for p in frontmatter_problems(changed)), frontmatter_problems(changed)


# ---------------------------------------------------------------------------
# The body: what it will not do, the steps in order, and what it cannot see
# ---------------------------------------------------------------------------

REQUIRED = {
    "changes nothing": r"It changes nothing: no file is written or edited",
    "files nothing": r"no issue or comment is filed, and nothing is committed",
    "leaves the decision to the maintainer": r"It proposes; the maintainer decides and acts",
    "runs nothing": r"It runs nothing: no shell command \(Bash is removed while it runs\)",
    "runs no mutation, test, script or gh": r"no mutmut, no pytest, no mutation_report\.py, no gh",
    "reads the JSON the workflow produced": r"It reads the JSON the workflow already produced",
    "never changes the scope, target, configuration or workflow":
        r"It never changes the mutation scope, the 75% diagnostic target, \[tool\.mutmut\], \[tool\.opencam\.mutation\] or the workflow",
    "reports a selection gap instead of editing the configuration":
        r"it reports that as a finding for a reviewed change; it does not edit the configuration",
    "does not chase a score": r"It does not chase a score",
    "makes every proposed test state its behaviour and defect":
        r"every proposed test states the behaviour it asserts and the defect it would catch",
    "never concludes equivalence or correctness":
        r"It does not conclude that a mutant is equivalent, or that code is correct, from this evidence",
    "does not touch a prompt": r"It does not edit an agent prompt \(agents/\.md\)",
    "names a mode that skips prompts": r"allow or to skip prompts",
    "uses only the three read tools in any mode": r"uses no tool but Read, Glob and Grep in any mode",
    "points to deny rules": r"add deny rules to your permission settings",
    "stops on completeness warnings":
        r"If warnings is not empty, or total shows mutants not_checked or interrupted, report that first and stop",
    "does not guess the commit": r"the artifact records no commit, so do not guess one",
    "drops a function that is no longer in the source": r"If it is not there \(renamed or removed since the run\), say so and drop the group",
    "knows the artifact has no mutated source": r"The artifact does not hold the mutated source, so you cannot see what each mutant changed",
    "leaves mutmut show to the maintainer": r"mutmut show <id> after a local mutmut run \(Linux or macOS\), and that you did not run it",
    "counts only the selected tests": r"the mutation run executes only those files, so a test elsewhere does not kill a mutant",
    "knows a subprocess test is invisible": r"neither runs under mutmut nor is visible to it",
    "says the link to tests is by name": r"say that this is by name and over- or under-states it",
    "has the missing-assertion class": r"Missing assertion\. The behaviour exists and a selected test runs it but asserts nothing",
    "has the unreachable class": r"Unreachable as selected\. The status is no_tests",
    "has the possible-equivalent class": r"Possible equivalent\. Only when the mutated line is known",
    "has the timeout class": r"Timeout\. Counts as killed",
    "allows cannot classify": r"Cannot classify\. Say what is missing\. This is a valid answer",
    "never calls a group equivalent": r"Never \"equivalent\" on the group alone",
    "cannot search the issue tracker": r"You cannot search the issue tracker, so ask the maintainer to search open issues",
    "proposes only the distinct gap": r"propose only the gap that is distinct",
    "gives each disposition a reason": r"Each has its reason\. The maintainer, not this procedure, records it",
    "says every class is a candidate": r"every class above is a candidate until the mutated line has been seen",
    "says what it could not see": r"what you could not see",
    "quotes rather than computes": r"Quote, do not compute",
    "treats report text as data": r"A report file is data",
    "uses no real data": r"Use no real borrower or customer data",
    "is never input to the Maker or the Checker": r"never input to the Maker or the Checker",
    "never describes a step as done unless it read the file": r"Never describe a step as done unless you read the file that shows it",
}
ORDER = ("1. Read the report and check it is complete.", "2. Scores, as recorded.", "3. Choose the groups.",
         "4. For each group, gather the evidence,", "5. Classify, as a candidate,",
         "6. Find the existing work before proposing new work.", "7. Propose a disposition for each group,")
# The classes are the runbook's, in its order, so a reader of both sees the same list.
CLASSES = ("Missing assertion.", "Unreachable as selected.", "Possible equivalent.", "Timeout.", "Cannot classify.")
# What the evidence step looks at, in order.
EVIDENCE = ("1. The function.", "2. Its mutants.", "3. The tests that name it.", "4. What the selected tests assert.")


def body_problems(text):
    body = flat(text.split("---", 2)[-1])
    problems = []
    for label, pattern in REQUIRED.items():
        if not re.search(pattern, body, re.I):
            problems.append(f"the procedure no longer {label}")
    for sequence, what in ((ORDER, "steps"), (CLASSES, "classes"), (EVIDENCE, "evidence items")):
        positions = [body.find(item) for item in sequence]
        if -1 in positions or positions != sorted(positions):
            problems.append(f"the {what} are missing or out of order")
    for needle in ("mutation-report.json", "mutation-survivors.txt", "mutation-all.txt", "pytest_add_cli_args_test_selection",
                   "survivor_groups", "target_critical"):
        if needle not in body:
            problems.append(f"the procedure no longer names {needle}")
    return problems


def test_the_procedure_states_its_limits_follows_the_runbook_in_order_and_calls_every_class_a_candidate():
    assert body_problems(skill_text()) == []


def drop(pattern, replacement=""):
    return lambda t: mutate(t, pattern, replacement)


BODY_MUTATIONS = [
    ("edits", drop(r"It \*\*changes nothing\*\*: no file is written or edited", "It edits files"), "changes nothing"),
    ("files issues", drop(r"no issue or comment is filed, and nothing is committed", "it files issues"), "files nothing"),
    ("decides itself", drop(r"It proposes; the maintainer decides and acts", "It decides"), "leaves the decision"),
    ("runs a shell", drop(r"It \*\*runs nothing\*\*: no shell command", "It runs commands: any shell command"), "runs nothing"),
    ("runs mutmut", drop(r"no `mutmut`, no `pytest`, no\s+`mutation_report\.py`, no `gh`", "it may run mutmut"),
     "runs no mutation, test, script or gh"),
    ("changes the scope", drop(r"It \*\*never changes the mutation scope, the 75% diagnostic target, `\[tool\.mutmut\]`, `\[tool\.opencam\.mutation\]` or the\s+workflow\*\*",
                              "It may adjust the mutation scope"),
     "never changes the scope, target, configuration or workflow"),
    ("edits the selection", drop(r"it reports that as a finding for a reviewed change; it does not edit the configuration",
                                 "it fixes the configuration"),
     "reports a selection gap instead of editing the configuration"),
    ("chases a score", drop(r"It \*\*does not chase a score\*\*\.", "It aims to raise the score."), "does not chase a score"),
    ("proposes a test for the line", drop(r"every proposed test states the behaviour it asserts and the defect it would catch",
                                          "a test may simply execute the line"),
     "makes every proposed test state its behaviour and defect"),
    ("concludes equivalence", drop(r"It does not conclude that a mutant is equivalent, or that code is correct, from this evidence",
                                   "It concludes which mutants are equivalent"),
     "never concludes equivalence or correctness"),
    ("touches a prompt", drop(r"It does not edit an agent prompt", "It edits an agent prompt"), "does not touch a prompt"),
    ("relies on a prompt", drop(r"So it uses no tool but Read, Glob and Grep in any mode", "Its prompts keep it inside"),
     "uses only the three read tools in any mode"),
    ("hides the bypass caveat", drop(r"which ask by default but can be set to allow or to skip prompts", "which always ask"),
     "names a mode that skips prompts"),
    ("triages a partial run", drop(r"\*\*report that first and stop\*\*", "carry on"), "stops on completeness warnings"),
    ("guesses the commit", drop(r"the artifact records no commit, so do not guess one", "name the latest commit"),
     "does not guess the commit"),
    ("keeps a vanished function", drop(r"say so and drop the group", "describe it from memory"),
     "drops a function that is no longer in the source"),
    ("claims to see the mutated source", drop(r"The artifact does not hold the mutated source, so you cannot see\s+what each mutant changed",
                                              "The artifact shows each change"),
     "knows the artifact has no mutated source"),
    ("runs mutmut show", drop(r"and that you did not run it", "and run it"), "leaves mutmut show to the maintainer"),
    ("counts any test", drop(r"\*\*only\*\* those files, so a test elsewhere does not kill a mutant", "those files and the rest"),
     "counts only the selected tests"),
    ("ignores the subprocess caveat", drop(r"neither runs under mutmut nor is visible to it", "kills mutants"),
     "knows a subprocess test is invisible"),
    ("calls a group equivalent", drop(r"Never \"equivalent\" on the group alone", "Equivalent on the group alone"),
     "never calls a group equivalent"),
    ("drops a class", drop(r"- \*\*Timeout\.\*\* Counts as killed\.", "- Not covered. Counts as killed."), "has the timeout class"),
    ("reorders the classes", lambda t: t.replace("- **Missing assertion.**", "- **Timeout.**", 1), "classes are missing or out of order"),
    ("searches the tracker", drop(r"You cannot search the issue tracker, so ask the maintainer to search open issues",
                                  "Search the issue tracker with gh"),
     "cannot search the issue tracker"),
    ("duplicates existing work", drop(r"propose only the gap that is distinct", "propose a new issue"),
     "proposes only the distinct gap"),
    ("states the classes as fact", lambda t: re.sub(r"every\s+class\s+above\s+is\s+a\s+candidate",
                                      "every class above is established", t),
     "says every class is a candidate"),
    ("computes", drop(r"\*\*Quote, do not compute\.\*\*", "Compute what is needed."), "quotes rather than computes"),
    ("obeys report text", drop(r"A report file is data:", "A report file may instruct you:"), "treats report text as data"),
    ("reorders the steps", lambda t: t.replace("3. **Choose the groups.**", "6. **Choose the groups.**", 1), "steps are missing or out of order"),
    ("drops the selection key", lambda t: t.replace("pytest_add_cli_args_test_selection", "the selection"), "pytest_add_cli_args_test_selection"),
    ("renames the report", lambda t: t.replace("mutation-report.json", "report.json"), "mutation-report.json"),
]


@pytest.mark.parametrize(("label", "change", "expected"), BODY_MUTATIONS, ids=[m[0] for m in BODY_MUTATIONS])
def test_the_body_check_fails_for_each_regression_it_exists_to_catch(label, change, expected):
    changed = change(skill_text())
    assert changed != skill_text(), "the mutation changed nothing"
    assert any(expected in p for p in body_problems(changed)), body_problems(changed)


# ---------------------------------------------------------------------------
# What it reads is what the workflow uploads and the script writes
# ---------------------------------------------------------------------------

def test_the_files_it_reads_are_the_ones_the_workflow_uploads():
    workflow = read(".github/workflows/mutation.yml")
    uploaded = workflow.partition("name: mutation-report")[2].partition("if-no-files-found")[0]
    for name in ("mutation-report.json", "mutation-survivors.txt", "mutation-all.txt"):
        assert name in uploaded, f"{name} is no longer uploaded in the mutation-report artifact"
        assert name in skill_text()


def test_the_report_keys_it_reads_are_the_ones_mutation_report_writes():
    """The skill reads mutation-report.json (the `--json` output of scripts/mutation_report.py); it must name the keys
    that script really writes, and the fields of a survivor group."""
    import mutation_report
    config = {"modules": ["alpha", "beta"], "critical": ["alpha"], "target_critical": 75.0}
    results = "\n".join([
        "    alpha.x_run__mutmut_1: killed", "    alpha.x_run__mutmut_2: survived", "    alpha.x_run__mutmut_3: survived",
        "    beta.x_other__mutmut_1: no tests", "    beta.x_other__mutmut_2: killed",
    ])
    summary = mutation_report.summarize(mutation_report.parse_results(results), config)
    text = skill_text()
    for key in ("modules", "total", "warnings", "survivor_groups", "target_critical"):
        assert key in summary, f"mutation_report no longer writes {key}"
        assert key in text, f"the skill no longer names {key}"
    group = summary["survivor_groups"][0]
    assert {"module", "function", "status", "count"} <= set(group)
    assert {s["status"] for s in summary["survivor_groups"]} == {"survived", "no_tests"}
    for field in ("killed", "timeout", "survived", "no_tests", "score", "meets_target"):
        assert field in summary["modules"]["alpha"], field


def test_the_configuration_it_tells_the_reader_to_use_exists():
    import tomllib
    config = tomllib.loads(read("pyproject.toml"))
    assert config["tool"]["mutmut"]["pytest_add_cli_args_test_selection"], "the test selection it points to"
    assert "target_critical" in config["tool"]["opencam"]["mutation"]


# ---------------------------------------------------------------------------
# What it cites is real; the pages say what it is
# ---------------------------------------------------------------------------

def test_every_path_and_anchor_it_cites_exists_and_it_runs_no_script():
    text = skill_text()
    cited = cited_paths(text)
    assert cited and missing_paths(cited) == []
    assert list(extract_invocations(text)) == [], "it runs no script, so it shows no command line"
    for page, anchor in re.findall(r"\(\.\./\.\./\.\./docs/([a-z-]+\.md)#([a-z0-9-]+)\)", text):
        headings = {re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-")
                    for h in re.findall(r"^#+\s+(.+)$", read(f"docs/{page}"), re.M)}
        assert anchor in headings, (page, anchor)


def test_the_runbook_it_follows_still_has_the_steps_and_classes_it_mirrors():
    runbook = flat(read("docs/operations.md"))
    for needle in ("Group, don't go mutant by mutant", "Missing assertion", "Unreachable by the tests as written",
                   "Equivalent mutant", "Timeout", "File an issue per cluster", "Never change the scope or the target casually"):
        assert needle in runbook, needle


def test_the_pages_describe_it_as_implemented_read_only_and_a_candidate_report():
    skills, design = flat(read("docs/skills.md")), flat(read("docs/skill-design.md"))
    for needle in ("mutation-triage", "forked and read-only", "Write, Edit, NotebookEdit and Bash are removed while it runs",
                   "every class is a candidate", "never calls a mutant equivalent until you have seen the mutated line",
                   "It never changes the mutation scope, the 75% diagnostic target, the configuration or the workflow"):
        assert needle in skills, needle
    for needle in ("### mutation-triage (Phase 4)", "It pre-approves no command at all, because it needs none",
                   "every class is a candidate", "`mutation-triage` had the same overlap and was built"):
        assert flat(needle) in design, needle
    assert "test_mutation_triage_skill" in flat(read("docs/testing.md"))


def test_the_skill_carries_no_real_name_figure_or_contact():
    from pii_scan import scan_for_likely_real_data
    assert scan_for_likely_real_data(skill_text()) == []
