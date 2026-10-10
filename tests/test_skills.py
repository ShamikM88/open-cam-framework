"""The contract of each shipped skill (issue #196, Phase 2): what an analyst aid may be and do.

`test_skill_inventory.py` keeps the inventory equal to the files and keeps every prompt, command and script from
reaching a skill. This file tests the skills themselves, the three analyst report skills in `.claude/skills/`:

- each is typed by a person (never started by the model), forked, has no write tool, may carry no hook or model
  override, and pre-approves only the read-only script calls it needs;
- the commands in its body use only those scripts and flags, each one is pre-approved by its own `allowed-tools`, and
  the real commands, run against a synthetic deal, leave everything under `deals/` byte-for-byte unchanged (and create
  nothing when there is no deal);
- every repository path it cites exists, every flag it passes is real, and its links resolve;
- its files carry no real name, figure or contact detail, and its frontmatter is safe YAML.

What no test can show is that a model follows the prose well. A skill's text is a prompt expectation, like a command's.
"""
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from pii_scan import scan_for_likely_real_data
from state_manager import write_state
from test_cli_subprocess import has_main_block
from test_command_flags import extract_invocations
from test_docs_contracts import cited_paths, missing_paths
from test_skill_inventory import REGISTRY, frontmatter, inventory_rows, invocation_of, read

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

# The only script calls a report skill may pre-approve or run: they read a deal's record and print, and nothing else.
READ_ONLY_CALLS = {
    "python scripts/state_manager.py --check-steps *": ("state_manager", {"--check-steps", "--company", "--proposal", "--required"}),
    "python scripts/source_manifest.py --check-sources *": ("source_manifest", {"--check-sources", "--company", "--proposal"}),
    "python scripts/policy_check.py *": ("policy_check", {"--company", "--proposal"}),
}
READ_ONLY_TOOLS = {"Read", "Glob", "Grep"}
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
ALLOWED_KEYS = {"name", "description", "argument-hint", "disable-model-invocation", "context", "background",
                "allowed-tools", "disallowed-tools"}
TOOL_TOKEN = re.compile(r"Bash\([^)]*\)|\S+")
COMPANY_EXAMPLE = re.compile(r"--company\s+\"([^\"]*)\"")


def report_skills():
    """The implemented analyst skills, from the one inventory."""
    return sorted(r["name"] for r in inventory_rows(read(REGISTRY))
                  if r["kind"] == "skill" and r["audience"] == "analyst" and r["status"].startswith("implemented"))


def skill_path(name):
    return REPO / ".claude" / "skills" / name / "SKILL.md"


def skill_text(name):
    return skill_path(name).read_text(encoding="utf-8")


def tools(fields, key):
    return TOOL_TOKEN.findall(fields.get(key, ""))


def test_the_three_analyst_aids_of_phase_two_are_the_implemented_report_skills():
    assert report_skills() == ["evidence-discipline", "financial-analysis", "information-gaps"]


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------

def frontmatter_problems(name, text):
    fields = frontmatter(text)
    problems = []
    if fields.get("name") != name:
        problems.append(f"name is {fields.get('name')!r}, not the folder name {name!r}")
    if invocation_of(fields) != "user":
        problems.append("it can be started by the model (disable-model-invocation must be true, user-invocable not false)")
    if fields.get("context") != "fork":
        problems.append("it does not run forked (context: fork)")
    if unknown := sorted(set(fields) - ALLOWED_KEYS):
        problems.append(f"unexpected frontmatter key(s) {unknown} (a hook, model or agent override changes what runs)")
    if missing := sorted(WRITE_TOOLS - set(tools(fields, "disallowed-tools"))):
        problems.append(f"disallowed-tools does not remove {missing}")
    for token in tools(fields, "allowed-tools"):
        if token in READ_ONLY_TOOLS:
            continue
        call = re.fullmatch(r"Bash\((.*)\)", token)
        if not call or call[1] not in READ_ONLY_CALLS:
            problems.append(f"allowed-tools pre-approves {token}, which is not a read-only tool or script call")
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
        # a plain YAML scalar cannot hold ": " or " #" (quote the value, or leave the text out)
        if sep and not line.startswith(" ") and not value.startswith(("'", '"')) and (": " in value or " #" in value):
            problems.append(f"{key} holds ': ' or ' #' in an unquoted value, which is not valid YAML")
    return problems


@pytest.mark.parametrize("name", report_skills())
def test_a_report_skill_is_typed_forked_and_cannot_write(name):
    assert frontmatter_problems(name, skill_text(name)) == []
    assert len(skill_text(name).splitlines()) <= 500, "Claude Code's guidance for a SKILL.md"


def test_the_frontmatter_check_reports_each_way_a_skill_could_stop_being_a_safe_aid():
    good = ("---\nname: x\ndescription: Does a thing.\nargument-hint: --company \"<Name>\"\n"
            "disable-model-invocation: true\ncontext: fork\nallowed-tools: Read Bash(python scripts/policy_check.py *)\n"
            "disallowed-tools: Write Edit NotebookEdit\n---\nbody\n")
    assert frontmatter_problems("x", good) == []

    def broken(old, new):
        assert old in good
        return "\n".join(frontmatter_problems("x", good.replace(old, new)))

    assert "not the folder name" in frontmatter_problems("y", good)[0]
    assert "can be started by the model" in broken("disable-model-invocation: true\n", "")
    assert "can be started by the model" in broken("disable-model-invocation: true", "disable-model-invocation: false")
    assert "does not run forked" in broken("context: fork", "context: inline")
    assert "unexpected frontmatter key" in broken("context: fork\n", "context: fork\nhooks: x\n")
    assert "unexpected frontmatter key" in broken("context: fork\n", "context: fork\nmodel: other\n")
    assert "does not remove ['Edit']" in broken("Write Edit NotebookEdit", "Write NotebookEdit")
    assert "does not remove" in broken("disallowed-tools: Write Edit NotebookEdit\n", "")
    assert "pre-approves Bash(python scripts/spreading_check.py *)" in broken(
        "Bash(python scripts/policy_check.py *)", "Bash(python scripts/spreading_check.py *)")
    assert "pre-approves Bash(python scripts/policy_check.py --draft x)" in broken(
        "Bash(python scripts/policy_check.py *)", "Bash(python scripts/policy_check.py --draft x)")
    assert "pre-approves Write" in broken("allowed-tools: Read", "allowed-tools: Read Write")
    assert "description is missing" in broken("description: Does a thing.", "description: ")
    assert "description is missing" in broken("description: Does a thing.", "description: " + "x" * 1600)
    assert "argument-hint is missing" in broken("argument-hint: --company \"<Name>\"\n", "")
    assert "not valid YAML" in broken("Does a thing.", "Does a thing: for each deal.")
    assert frontmatter_problems("x", good.replace("Does a thing.", "\"Does a thing: for each deal.\"")) == []


# ---------------------------------------------------------------------------
# What the body runs
# ---------------------------------------------------------------------------

def body_commands(text):
    """[(script, flags)] for each `python scripts/<name>.py ...` the body shows."""
    return [(script, flags) for _line, script, flags in extract_invocations(text)]


def command_problems(text):
    fields = frontmatter(text)
    approved = [READ_ONLY_CALLS[m[1]] for m in map(lambda t: re.fullmatch(r"Bash\((.*)\)", t), tools(fields, "allowed-tools"))
                if m and m[1] in READ_ONLY_CALLS]
    problems = []
    for script, flags in body_commands(text):
        known = next((allowed for name, allowed in READ_ONLY_CALLS.values() if name == script), None)
        if known is None:
            problems.append(f"runs scripts/{script}.py, which is not a read-only call a report skill may make")
            continue
        if not set(flags) <= known:
            problems.append(f"passes {sorted(set(flags) - known)} to scripts/{script}.py")
        if not any(name == script for name, _ in approved):
            problems.append(f"scripts/{script}.py is run but not pre-approved by allowed-tools")
    runnable = {name for name, _ in READ_ONLY_CALLS.values()}
    for script in sorted(set(re.findall(r"scripts/([A-Za-z0-9_]+)\.py", text))):
        path = SCRIPTS / f"{script}.py"
        if script not in runnable and (not path.is_file() or has_main_block(path)):
            problems.append(f"names scripts/{script}.py, a command-line script that is not a read-only call")
    return problems


@pytest.mark.parametrize("name", report_skills())
def test_a_skill_runs_only_read_only_scripts_and_each_is_pre_approved(name):
    text = skill_text(name)
    assert body_commands(text), "the skill delegates to a deterministic script"
    assert command_problems(text) == []


def test_the_command_check_reports_a_write_script_a_stray_flag_and_a_missing_approval():
    head = "---\nname: x\nallowed-tools: Read Bash(python scripts/policy_check.py *)\n---\n"
    assert command_problems(head + "`python scripts/policy_check.py --company c --proposal p`") == []
    assert "not a read-only call" in command_problems(head + "`python scripts/spreading_check.py --company c`")[0]
    assert "passes ['--draft']" in command_problems(head + "`python scripts/policy_check.py --company c --draft d.md`")[0]
    assert "not pre-approved" in command_problems(
        head + "`python scripts/source_manifest.py --check-sources --company c`")[0]
    assert "names scripts/deal_export.py" in command_problems(head + "Never run scripts/deal_export.py.")[0]
    assert command_problems(head + "The definitions are in scripts/spreading_builder.py and scripts/policy_engine.py.") == []


# ---------------------------------------------------------------------------
# The real commands leave a deal untouched
# ---------------------------------------------------------------------------

def deal_files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def run_commands(name, cwd):
    """Run every command the skill's body shows, with the placeholders filled, as a real subprocess."""
    results = []
    for line in skill_text(name).splitlines():
        line = line.strip()
        if not line.startswith("python scripts/"):
            continue
        line = line.replace('"<company>"', '"Synthetic Co"').replace('"<proposal>"', '"Synthetic Fleet Loan"')
        words = shlex.split(line, posix=True)
        argv = [sys.executable, str(SCRIPTS / words[1].removeprefix("scripts/")), *words[2:]]
        results.append(subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60))
    return results


@pytest.fixture
def rich_deal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Synthetic Co", "Synthetic Fleet Loan", steps_completed=["triage", "spread"], inputs={"pd": "0.20%"},
                triage={"verdict": "Go", "sources": ["https://example.invalid/registry"]},
                financials_source="analyst-supplied", financials_source_note="Synthetic convention note",
                financials={"FY-Current": {"ebitda": 250}}, ratios={"FY-Current": {"dscr": 1.4}},
                covenants=[{"metric": "dscr", "type": "minimum", "threshold": 1.25}])
    return tmp_path


@pytest.mark.parametrize("name", report_skills())
def test_the_commands_a_skill_runs_leave_the_deal_byte_for_byte_unchanged(name, rich_deal):
    before = deal_files(rich_deal)
    results = run_commands(name, rich_deal)
    assert results, "the skill shows at least one command"
    for result in results:
        assert result.returncode == 0, result.stderr
        json.loads(result.stdout)
    assert deal_files(rich_deal) == before, "no changed state.json, no new file, no lock or temporary file"


@pytest.mark.parametrize("name", report_skills())
def test_the_commands_a_skill_runs_create_nothing_when_there_is_no_deal(name, tmp_path):
    for result in run_commands(name, tmp_path):
        assert result.returncode in (0, 1), result.stderr
        assert "Traceback" not in result.stderr
    assert list(tmp_path.iterdir()) == [], "not even a deals/ folder"


# ---------------------------------------------------------------------------
# Citations and confidentiality
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", report_skills())
def test_every_repository_path_a_skill_cites_exists(name):
    cited = cited_paths(skill_text(name))
    assert cited, "a skill declares its authoritative sources"
    assert missing_paths(cited) == []
    assert missing_paths({"docs/gone.md", "scripts/state_manager.py"}) == ["docs/gone.md"]


def confidentiality_problems(text):
    problems = [f"possible real data: {finding}" for finding in scan_for_likely_real_data(text)]
    for example in COMPANY_EXAMPLE.findall(text):
        if not (example.startswith("<") or example.startswith("Synthetic ")):
            problems.append(f"example company {example!r} is not a placeholder or a 'Synthetic ' name")
    return problems


@pytest.mark.parametrize("path", [*(skill_path(n) for n in report_skills()), REPO / "docs" / "skills.md",
                                  REPO / "docs" / "skill-design.md"], ids=lambda p: p.parent.name + "/" + p.name)
def test_skill_files_and_their_pages_carry_no_real_name_figure_or_contact(path):
    assert confidentiality_problems(path.read_text(encoding="utf-8")) == []


def test_the_confidentiality_check_flags_a_real_looking_example():
    assert confidentiality_problems('Run with --company "Acme Holdings Ltd" and write to jo@bank.example') != []
    assert confidentiality_problems('--company "Synthetic Co" --proposal "<Proposal name>" and --company "<Name>"') == []
