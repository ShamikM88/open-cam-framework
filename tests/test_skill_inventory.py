"""The one inventory of commands and skills, and the boundaries a skill must stay inside (issue #196, Phase 1).

`config/skills_registry.md` lists every command and skill by name, and `docs/skill-design.md` records why. No skill
exists yet, so most of what is checked here is the relationship, not a skill's content:

- the commands in the inventory are the files in `.claude/commands/`, and each one's Invocation is what its
  frontmatter says (a command's `disable-model-invocation` decides whether the model can run it);
- a name is used once across both kinds, because in Claude Code a skill wins over a command of the same name and
  would silently disable it;
- a skill that exists on disk is in the inventory as implemented and one that does not exist is planned, and an
  implemented skill's frontmatter says what its row says;
- every skill is an aid (`Role in the pipeline` is `None (aid)`, `Headless` is `no`); making one the Maker's or the
  Checker's input is a deliberate change to this file, not a side effect;
- no agent prompt or command names or loads a skill, and the headless entry points never read `.claude/`;
- the design page's first-tranche table is the inventory's skill rows.

Each check is a function over text, and each is also run on deliberately wrong input so it is shown to fail.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

REGISTRY = "config/skills_registry.md"
DESIGN = "docs/skill-design.md"

ROW = re.compile(r"^\|(?P<cells>[^\n]*)\|[ \t]*$", re.M)
NAME = re.compile(r"^`(/?[a-z][a-z0-9-]*)`$")
KINDS = {"command", "skill"}
AUDIENCES = {"analyst", "maintainer"}
INVOCATIONS = {"user", "user+model", "model"}
ROLES = {"Maker", "Checker", "Setup", "None (aid)"}
HEADLESS = {"yes", "no"}
STATUS = re.compile(r"^(implemented|planned)(?:: phase (\d))?$")
TRUE_VALUES = {"true", "yes", "on", "1"}


def read(relative):
    return (REPO / relative).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Reading the files
# ---------------------------------------------------------------------------

def inventory_rows(text):
    """One dict per inventory row: a table row whose first cell is a backticked name."""
    rows = []
    for match in ROW.finditer(text):
        cells = [c.strip() for c in match["cells"].split("|")]
        if not NAME.match(cells[0]):
            continue
        keys = ("name", "kind", "audience", "status", "invocation", "role", "headless")
        values = dict(zip(keys, [cells[0].strip("`"), *cells[1:]], strict=False))
        rows.append(dict.fromkeys(keys, "") | values | {"cells": len(cells)})
    return rows


def frontmatter(text):
    """The simple `key: value` lines between the first two `---` lines (all this repository's files use)."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key and not key.startswith((" ", "\t")):
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def invocation_of(fields):
    """`user`, `user+model` or `model`, as Claude Code reads the frontmatter; `invalid` for a file no one can run."""
    user_only = fields.get("disable-model-invocation", "false").lower() in TRUE_VALUES
    model_only = fields.get("user-invocable", "true").lower() not in TRUE_VALUES
    if user_only and model_only:
        return "invalid"
    return "user" if user_only else "model" if model_only else "user+model"


def command_files():
    return {p.stem: frontmatter(p.read_text(encoding="utf-8")) for p in (REPO / ".claude" / "commands").glob("*.md")}


def skill_files():
    return {p.parent.name: frontmatter(p.read_text(encoding="utf-8"))
            for p in (REPO / ".claude" / "skills").glob("*/SKILL.md")}


# ---------------------------------------------------------------------------
# The relationship between the inventory and the files
# ---------------------------------------------------------------------------

def inventory_problems(rows, commands, skills):
    """One message per way the inventory and the files disagree. `commands` and `skills` map name -> frontmatter."""
    problems = []
    names = [r["name"].lstrip("/") for r in rows]
    for name in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"{name} is listed more than once (a name must be unique across commands and skills)")
    for r in rows:
        name = r["name"]
        if r["cells"] != 7:
            problems.append(f"{name}: the row has {r['cells']} cells, not 7")
            continue
        checks = ((r["kind"], KINDS, "Kind"), (r["audience"], AUDIENCES, "Audience"),
                  (r["invocation"], INVOCATIONS, "Invocation"), (r["role"], ROLES, "Role in the pipeline"),
                  (r["headless"], HEADLESS, "Headless"))
        problems += [f"{name}: {label} is {value!r}, not one of {sorted(allowed)}"
                     for value, allowed, label in checks if value not in allowed]
        status = STATUS.match(r["status"])
        if not status:
            problems.append(f"{name}: Status is {r['status']!r}, not implemented / planned: phase N")
        if (r["kind"] == "command") != name.startswith("/"):
            problems.append(f"{name}: a command is written /name and a skill name, but this is a {r['kind']}")
        if r["kind"] == "skill":
            if status and not status[2]:
                problems.append(f"{name}: a skill's Status needs its phase")
            if r["role"] != "None (aid)":
                problems.append(f"{name}: a skill is an aid (Role None (aid)), not {r['role']}")
            if r["headless"] != "no":
                problems.append(f"{name}: a skill has no headless equivalent (Headless no)")
            if name in commands:
                problems.append(f"{name}: a command has this name, and the skill would shadow it")
    listed_commands = {r["name"][1:] for r in rows if r["kind"] == "command" and r["name"].startswith("/")}
    listed_skills = {r["name"] for r in rows if r["kind"] == "skill"}
    if listed_commands != set(commands):
        problems.append(f"the inventory's commands {sorted(listed_commands)} are not the files in .claude/commands/ "
                        f"{sorted(commands)}")
    implemented = {r["name"] for r in rows if r["kind"] == "skill" and r["status"].startswith("implemented")}
    if implemented != set(skills):
        problems.append(f"the implemented skills {sorted(implemented)} are not the SKILL.md files on disk "
                        f"{sorted(skills)} (add the row, or flip planned to implemented, in the same change)")
    for r in rows:
        fields = (commands if r["kind"] == "command" else skills).get(r["name"].lstrip("/"))
        if fields is not None and invocation_of(fields) != r["invocation"]:
            problems.append(f"{r['name']}: the inventory says Invocation {r['invocation']}, the file's frontmatter "
                            f"says {invocation_of(fields)}")
    return problems


def test_the_inventory_is_the_command_and_skill_files():
    rows = inventory_rows(read(REGISTRY))
    assert command_files(), "no slash commands found: the test is looking in the wrong place"
    assert rows, "no inventory rows found: the registry's table changed shape"
    assert inventory_problems(rows, command_files(), skill_files()) == []


def row(name, kind="skill", **overrides):
    values = {"name": name, "kind": kind, "audience": "analyst", "status": "planned: phase 2",
              "invocation": "user", "role": "None (aid)", "headless": "no", "cells": 7}
    if kind == "command":
        values |= {"status": "implemented", "role": "Maker", "headless": "yes"}
    return values | overrides


def test_the_inventory_check_reports_each_way_the_files_and_the_table_can_disagree():
    commands = {"spread": {"disable-model-invocation": "true"}, "review": {}}
    skills = {"info": {"disable-model-invocation": "true"}}
    good = [row("/spread", "command"), row("/review", "command", invocation="user+model", role="Checker"),
            row("info", status="implemented: phase 2")]
    assert inventory_problems(good, commands, skills) == []

    def messages(rows, cmds=commands, sk=skills):
        return "\n".join(inventory_problems(rows, cmds, sk))

    assert "listed more than once" in messages([*good, row("info")])
    assert "would shadow it" in messages([*good, row("spread")])
    assert "not the files in .claude/commands/" in messages(good[:1])
    assert "not the files in .claude/commands/" in messages([*good, row("/ghost", "command")])
    assert "not the SKILL.md files on disk" in messages([*good[:2], row("info")])
    assert "not the SKILL.md files on disk" in messages([*good, row("other", status="implemented: phase 2")])
    assert "the inventory says Invocation user+model, the file's frontmatter says user" in \
        messages([row("/spread", "command", invocation="user+model"), *good[1:]])
    assert "an aid" in messages([*good[:2], row("info", status="implemented: phase 2", role="Checker")])
    assert "no headless equivalent" in messages([*good[:2], row("info", status="implemented: phase 2", headless="yes")])
    assert "'maybe'" in messages([*good[:2], row("info", status="implemented: phase 2", audience="maybe")])
    assert "needs its phase" in messages([*good[:2], row("info", status="implemented")])
    assert "not implemented / planned" in messages([*good[:2], row("info", status="draft")])
    assert "but this is a skill" in messages([*good[:2], row("/info", "skill", status="implemented: phase 2")])
    assert "has 6 cells" in messages([*good, row("short", cells=6)])


def test_the_parsers_read_what_the_registry_actually_uses():
    text = ("| Name | Kind |\n| :--- | :--- |\n| `/a` | command | analyst | implemented | user | Maker | yes |\n"
            "| `b-c` | skill | maintainer | planned: phase 3 | user | None (aid) | no |\n"
            "1. `/not-a-row`\n| plain | text |\n")
    rows = inventory_rows(text)
    assert [(r["name"], r["kind"], r["status"], r["cells"]) for r in rows] == \
        [("/a", "command", "implemented", 7), ("b-c", "skill", "planned: phase 3", 7)]
    fields = frontmatter('---\ndescription: Do a thing: with a colon\nargument-hint: "--x"\n'
                         'disable-model-invocation: true\n---\nbody: not frontmatter\n')
    assert fields == {"description": "Do a thing: with a colon", "argument-hint": "--x",
                      "disable-model-invocation": "true"}
    assert frontmatter("no frontmatter here\n") == {}
    assert [invocation_of(f) for f in ({}, {"disable-model-invocation": "true"}, {"user-invocable": "false"},
                                         {"disable-model-invocation": "True", "user-invocable": "false"})] == \
        ["user+model", "user", "model", "invalid"]


# ---------------------------------------------------------------------------
# A skill is never input to the Maker, the Checker or the headless pipeline
# ---------------------------------------------------------------------------

# The files that make up the Maker's and the Checker's instructions, and the commands that run in their sessions.
PIPELINE_TEXT = (*sorted((REPO / "agents").glob("*.md")), *sorted((REPO / ".claude" / "commands").glob("*.md")))
# The scripts that call a model themselves (the headless pipeline and its evaluation harness).
HEADLESS_ENTRY_POINTS = ("orchestrator.py", "calibrate.py", "run_evals.py",
                         "eval_baseline.py", "eval_budget.py", "eval_cases.py", "eval_oracles.py",
                         "eval_report.py", "eval_runner.py")


def skill_names(text):
    return [r["name"] for r in inventory_rows(text) if r["kind"] == "skill"]


def wiring_problems(files, names):
    """Where a prompt or command names a skill, points at the skills folder, or lets the model call one."""
    problems = []
    for path, text in files:
        for name in names:
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
                problems.append(f"{path} names the skill {name}")
        if re.search(r"\.claude/skills|SKILL\.md", text):
            problems.append(f"{path} points at the skills folder")
        allowed = frontmatter(text).get("allowed-tools", "")
        if re.search(r"(?<![\w(])Skill\b", allowed):
            problems.append(f"{path} lets the session run a skill (allowed-tools)")
    return problems


def test_no_agent_prompt_or_command_names_loads_or_allows_a_skill():
    names = skill_names(read(REGISTRY))
    assert names, "no skills in the inventory: the test would check nothing"
    files = [(p.relative_to(REPO).as_posix(), p.read_text(encoding="utf-8")) for p in PIPELINE_TEXT]
    assert len(files) >= 12
    assert wiring_problems(files, names) == []


def test_the_wiring_check_reports_a_prompt_that_reaches_a_skill():
    files = [("agents/x.md", "Use information-gaps before you draft."),
             ("agents/y.md", "Read .claude/skills/info/SKILL.md first."),
             (".claude/commands/z.md", "---\nallowed-tools: Bash(python scripts/a.py *) Skill(info)\n---\nbody"),
             (".claude/commands/ok.md", "---\nallowed-tools: Bash(python scripts/a.py *)\n---\nThe skillful analyst.")]
    problems = wiring_problems(files, ["information-gaps"])
    assert problems == ["agents/x.md names the skill information-gaps", "agents/y.md points at the skills folder",
                        ".claude/commands/z.md lets the session run a skill (allowed-tools)"]
    assert wiring_problems([("a.md", "run /information-gaps first")], ["information-gaps"]) == \
        ["a.md names the skill information-gaps"]
    assert wiring_problems([("a.md", "see docs/information-gaps-notes.md and my-information-gaps")],
                           ["information-gaps"]) == []   # a longer name is not the skill's name


def test_the_headless_entry_points_never_read_the_claude_folder():
    """They call the model through the SDK with prompts they build, so a skill cannot reach them; keep it that way."""
    assert all((REPO / "scripts" / name).is_file() for name in HEADLESS_ENTRY_POINTS)
    reading = [name for name in HEADLESS_ENTRY_POINTS
               if ".claude" in (REPO / "scripts" / name).read_text(encoding="utf-8")]
    assert reading == []


# ---------------------------------------------------------------------------
# The design page and the inventory name the same skills
# ---------------------------------------------------------------------------

TRANCHE_ROW = re.compile(r"^\|\s*`([a-z][a-z0-9-]*)`\s*\|\s*(\d)\s*\|\s*(analyst|maintainer)\s*\|\s*(user\+model|user|model)\s*\|",
                         re.M)


def tranche_from_design(text):
    section = text.partition("\n## The first tranche")[2].partition("\n## ")[0]
    return {(m[1], m[2], m[3], m[4]) for m in TRANCHE_ROW.finditer(section)}


def tranche_from_inventory(text):
    return {(r["name"], STATUS.match(r["status"])[2], r["audience"], r["invocation"])
            for r in inventory_rows(text) if r["kind"] == "skill" and STATUS.match(r["status"])}


def test_the_design_pages_first_tranche_is_the_inventorys_skills():
    from_design = tranche_from_design(read(DESIGN))
    assert from_design, "no tranche rows found: the design page's table changed shape"
    assert from_design == tranche_from_inventory(read(REGISTRY))


def test_the_tranche_parsers_read_what_the_pages_actually_use():
    design = ("## Other\n| `x` | 9 | analyst | user |\n\n## The first tranche\n\n| Skill | Phase | Audience | Invocation |\n"
              "| :--- | :--- | :--- | :--- |\n| `a-b` | 2 | analyst | user |\n| `c` | 3 | maintainer | user |\n\n## Next\n"
              "| `y` | 1 | analyst | user |\n")
    assert tranche_from_design(design) == {("a-b", "2", "analyst", "user"), ("c", "3", "maintainer", "user")}
    registry = ("| `/a` | command | analyst | implemented | user | Maker | yes |\n"
                "| `a-b` | skill | analyst | planned: phase 2 | user | None (aid) | no |\n")
    assert tranche_from_inventory(registry) == {("a-b", "2", "analyst", "user")}
