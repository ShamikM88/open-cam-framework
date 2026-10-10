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
- no agent prompt, command or the always-loaded `CLAUDE.md` names, points at or allows a skill, and no production
  script (so no helper a headless entry point imports) builds a path into `.claude/`;
- the design page's first-tranche table is the inventory's skill rows.

Each check is a function over text, and each is also run on deliberately wrong input so it is shown to fail.
"""
import ast
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

# The instructions a session or a model is given: the Maker's and the Checker's prompts, the commands that run in
# their sessions, and CLAUDE.md, which every session loads (a recommendation there reaches all of them).
PIPELINE_TEXT = (*sorted((REPO / "agents").glob("*.md")), *sorted((REPO / ".claude" / "commands").glob("*.md")),
                 REPO / "CLAUDE.md")
# The scripts that call a model themselves (the headless pipeline and its evaluation harness).
HEADLESS_ENTRY_POINTS = ("orchestrator", "calibrate", "run_evals",
                         "eval_baseline", "eval_budget", "eval_cases", "eval_oracles", "eval_report", "eval_runner")
# The documented placeholder form of a skill's path (`.claude/skills/<name>/SKILL.md`) names no skill.
PLACEHOLDER_SKILL_PATH = re.compile(r"\.claude/skills/<[^>/\s]+>/SKILL\.md")


def skill_names(text):
    return [r["name"] for r in inventory_rows(text) if r["kind"] == "skill"]


def body_of(text):
    """The text after the frontmatter (all of it when there is none)."""
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                return "\n".join(lines[i + 1:])
    return text


def wiring_problems(files, names):
    """Where an instruction file names a real skill, points at a skills path, or lets a session run one by its
    frontmatter or by telling it to use the Skill tool. It is about actual names, paths and invocation wiring: the
    word "skill" in ordinary prose, and the documented `.claude/skills/<name>/SKILL.md` placeholder, are fine."""
    problems = []
    for path, text in files:
        concrete = PLACEHOLDER_SKILL_PATH.sub("", text)
        for name in names:
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", concrete):
                problems.append(f"{path} names the skill {name}")
        if re.search(r"\.claude/skills|SKILL\.md", concrete):
            problems.append(f"{path} points at the skills folder")
        fields = frontmatter(text)
        if re.search(r"(?<![\w(])Skill\b", fields.get("allowed-tools", "")):
            problems.append(f"{path} lets the session run a skill (allowed-tools)")
        if "skills" in fields:
            problems.append(f"{path} preloads skills (frontmatter skills)")
        if re.search(r"\bSkill tool\b|(?<![\w-])Skill\(", body_of(concrete)):
            problems.append(f"{path} tells the session to use the Skill tool")
    return problems


def test_no_agent_prompt_or_command_names_loads_or_allows_a_skill():
    names = skill_names(read(REGISTRY))
    assert names, "no skills in the inventory: the test would check nothing"
    files = [(p.relative_to(REPO).as_posix(), p.read_text(encoding="utf-8")) for p in PIPELINE_TEXT]
    assert len(files) >= 13 and "CLAUDE.md" in [path for path, _ in files], "the always-loaded instructions are checked"
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


def test_the_wiring_check_covers_claude_md_and_every_way_a_session_could_be_pointed_at_a_skill():
    names = ["information-gaps", "financial-analysis"]
    directing = [
        ("CLAUDE.md", "Before drafting the Financial Analysis section, run information-gaps."),
        ("CLAUDE.md", "Use /financial-analysis to interpret the ratios."),
        ("CLAUDE.md", "Skills live in `.claude/skills/information-gaps/SKILL.md`; read it first."),
        ("CLAUDE.md", "Load everything under .claude/skills before you start."),
        ("CLAUDE.md", "Open SKILL.md for the procedure."),
        ("CLAUDE.md", "Call the Skill tool with the name of the skill that fits."),
        ("CLAUDE.md", "Invoke Skill(information-gaps) when information is missing."),
        ("agents/x.md", "---\nskills: [anything]\n---\nYou are an underwriter."),
        (".claude/commands/y.md", "---\nallowed-tools: Skill(anything) Bash(python scripts/a.py *)\n---\nbody"),
    ]
    for path, text in directing:
        assert wiring_problems([(path, text)], names), text
    assert wiring_problems([directing[0]], names) == ["CLAUDE.md names the skill information-gaps"]
    assert wiring_problems([directing[2]], names) == ["CLAUDE.md names the skill information-gaps",
                                                      "CLAUDE.md points at the skills folder"]
    assert wiring_problems([directing[-2]], names) == ["agents/x.md preloads skills (frontmatter skills)"]
    assert wiring_problems([directing[-1]], names) == [".claude/commands/y.md lets the session run a skill (allowed-tools)"]


def test_the_wiring_check_leaves_ordinary_talk_about_skills_alone():
    names = ["information-gaps", "financial-analysis"]
    benign = [
        ("CLAUDE.md", "- **Skills are aids, never input to the Maker or the Checker.** A skill adds a procedure and "
                      "points to a rule; the rule stays here. A skillful analyst reads the Financial Analysis section."),
        ("CLAUDE.md", "| `.claude/commands/` | The slash commands; a skill, once any exists, is "
                      "`.claude/skills/<name>/SKILL.md` | `config/skills_registry.md` |"),
        ("CLAUDE.md", "See [skill design](docs/skill-design.md) and config/skills_registry.md."),
        ("agents/x.md", "---\ndescription: An agent with skills in credit analysis\n---\nUse your judgement and skill."),
        (".claude/commands/y.md", "---\nallowed-tools: Bash(python scripts/a.py *)\n---\nA skilled reviewer."),
    ]
    assert wiring_problems(benign, names) == []


def local_modules():
    """{module name: source} for every production script; the headless entry points import their helpers from here."""
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted((REPO / "scripts").glob("*.py"))}


def local_import_closure(sources, entries):
    """The modules in `sources` that `entries` import, directly or through each other (function-level imports
    included): the helpers a headless entry point can reach."""
    seen, todo = set(), list(entries)
    while todo:
        name = todo.pop()
        if name in seen or name not in sources:
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(sources[name])):
            if isinstance(node, ast.Import):
                todo.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                todo.append(node.module.split(".")[0])
    return seen


def claude_path_constants(source):
    """(line, text) of each string constant in code that is a path into `.claude/` or the folder name itself: it
    contains `.claude` and no whitespace (an f-string's literal pieces count). Docstrings and comments are not code,
    and a sentence that merely mentions `.claude/commands/...` (it has spaces) is prose, not a path."""
    tree = ast.parse(source)
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)}
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
            and ".claude" in node.value and not any(ch.isspace() for ch in node.value)]


def claude_folder_problems(sources, entries=None):
    """{module: constants} for the modules that build a `.claude` path: all of `sources`, or only the closure of
    `entries`."""
    scope = local_import_closure(sources, entries) if entries is not None else set(sources)
    return {name: found for name in sorted(scope) if (found := claude_path_constants(sources[name]))}


def test_no_production_script_and_so_no_helper_of_a_headless_entry_point_builds_a_path_into_the_claude_folder():
    """The headless pipeline calls the model through the SDK with prompts it builds itself, so a skill cannot reach
    it; this keeps a helper from changing that. Every production script is scanned, so a new helper is covered the
    day it is added, whether or not an entry point imports it yet, and the entry points' import closure is shown to
    lie inside what is scanned. The limits: it sees string constants written in code (a path spelled `".claude"`,
    `os.path.join(".claude", ...)`, an f-string piece), not one assembled from fragments at run time, a module outside
    `scripts/`, or `exec`/`eval`; and it does not read text a script merely prints."""
    sources = local_modules()
    assert set(HEADLESS_ENTRY_POINTS) <= set(sources)
    closure = local_import_closure(sources, HEADLESS_ENTRY_POINTS)
    assert {"state_manager", "spreading_builder", "policy_engine", "textio"} <= closure, "the closure walk reads real code"
    assert closure <= set(sources)
    assert claude_folder_problems(sources) == {}
    assert claude_folder_problems(sources, HEADLESS_ENTRY_POINTS) == {}


def test_the_claude_folder_check_catches_a_helper_an_entry_point_imports_and_ignores_prose():
    sources = {
        "orchestrator": "import helper\nfrom calibrate import x\n",
        "calibrate": "def x():\n    from late import y\n",
        "late": "import pathlib\nSKILLS = pathlib.Path('.claude') / 'skills'\n",
        "helper": "import os\nroot = os.path.join('.claude', 'skills')\n",
        "fstring": "def f(base):\n    return f'{base}/.claude/skills/x/SKILL.md'\n",
        "unrelated": "import os\nPATH = '../.claude/skills'\n",
        "docs_only": ('"""Reads .claude/skills/x.md per the module docstring."""\n'
                      '# a comment about .claude/skills\n'
                      'def f():\n    """See .claude/commands/spread.md."""\n'
                      '    return "see .claude/commands/spread.md for the step"\n'),
    }
    reached = claude_folder_problems(sources, ["orchestrator"])
    assert set(reached) == {"helper", "late"}, "a direct and a function-level import are both followed"
    assert reached["helper"] == [(2, ".claude")] and reached["late"][0][1] == ".claude"
    everything = claude_folder_problems(sources)
    assert set(everything) == {"helper", "late", "fstring", "unrelated"}, "scanning all catches an unimported helper"
    assert "docs_only" not in everything, "docstrings, comments and sentences are not paths"
    assert claude_path_constants("x = 'a .claude b'\ny = '.claude'\n") == [(2, ".claude")]


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
