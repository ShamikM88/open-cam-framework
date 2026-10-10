"""The contract of each shipped skill (issue #196, Phase 2).

`test_skill_inventory.py` keeps the inventory equal to the files and keeps every prompt, command and script from
reaching a skill. This file tests the skills themselves, the three analyst report skills in `.claude/skills/`:

- each is typed by a person (never started by the model), forked, may carry no hook or model override, has the write
  tools removed, and pre-approves only the read-only script calls it needs. `allowed-tools` pre-approves; it is not an
  allowlist, so the other tools stay under the user's permission settings. These tests prove what the files say and what
  the commands they show do; they do not prove an operating-system sandbox;
- the commands in its body use only those scripts and flags, each one is pre-approved by its own `allowed-tools`, and
  the real commands, run against a synthetic deal, leave everything under `deals/` byte-for-byte unchanged (and create
  nothing when there is no deal);
- the dated folder a skill is told to read is the one `state_manager` and the scripts use, a company or proposal is put
  in a quoted command only if it passes the stated refusal rule, and a `null` ratio is explained by basis;
- every repository path it cites exists, every flag it passes is real, and its links resolve;
- its files carry no real name, figure or contact detail, and its frontmatter stays inside a small YAML subset that a
  strict parser written for it reads. That parser is not a general YAML validator.

What no test can show is that a model follows the prose. A few tests pin a sentence of a skill's text (the refusal rule,
the dated-folder rule, the basis-aware `null` wording, the tool-error rule) so it cannot be deleted by accident; a
passing pin shows the instruction is present, never that it is obeyed. A skill's text is a prompt expectation, like a
command's.
"""
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pii_scan import scan_for_likely_real_data
from policy_check import compute as policy_compute
from spreading_builder import evaluate_financial_model
from state_manager import write_state
from test_cli_subprocess import has_main_block
from test_command_flags import extract_invocations
from test_docs_contracts import cited_paths, missing_paths
from test_skill_inventory import REGISTRY, body_of, frontmatter, inventory_rows, invocation_of, read

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


# ---------------------------------------------------------------------------
# Review of Phase 2 (PR #206): what a skill may say about a `null` ratio, the manifest check's scope, which dated folder
# is read, what reaches a shell, and what the frontmatter test does and does not prove
# ---------------------------------------------------------------------------

COMPANY, PROPOSAL = "Synthetic Co", "Synthetic Fleet Loan"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run_script(name, *args, cwd):
    return subprocess.run([sys.executable, str(SCRIPTS / f"{name}.py"), *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)


# --- 1. a `null` ratio means different things on the two bases ---------------------------------------------------

def covenant_reason(**state):
    write_state(COMPANY, PROPOSAL, covenants=[{"metric": state.pop("metric"), "type": "minimum", "threshold": 1.0}],
                **state)
    (result,) = policy_compute(COMPANY, PROPOSAL)["policy_state"]["covenant_results"]
    assert result["status"] == "UNRESOLVABLE" and result["actual"] is None
    return result["reason"]


def test_a_framework_computed_null_ratio_names_the_recorded_denominator(workdir):
    model = evaluate_financial_model({"FY-Current": {"revenue": 100, "cost_of_sales": 60}})   # no interest paid
    assert model["ratios"]["FY-Current"]["ebit_interest_cover"] is None
    reason = covenant_reason(metric="ebit_interest_cover", financials_source="framework-computed",
                             financials=model["financials"], ratios=model["ratios"])
    assert "interest paid is zero" in reason


def test_an_analyst_supplied_null_ratio_gets_no_denominator_the_record_does_not_show(workdir):
    """Recorded as given: with no raw lines the script cannot know why the analyst reported N/A, and says only that
    there is no value. A skill must not supply a reason the record does not hold."""
    reason = covenant_reason(metric="dscr", financials_source="analyst-supplied",
                             financials={"FY-Current": {"ebitda": 250}}, ratios={"FY-Current": {"dscr": None}})
    assert "no computed value" in reason
    assert not any(word in reason for word in ("zero", "negative", "denominator"))


def test_an_analyst_supplied_null_ratio_names_a_denominator_only_when_a_recorded_subtotal_shows_it(workdir):
    reason = covenant_reason(metric="gross_leverage", financials_source="analyst-supplied",
                             financials={"FY-Current": {"ebitda": -40}}, ratios={"FY-Current": {"gross_leverage": None}})
    assert "EBITDA is negative" in reason


def null_passages(text):
    """The blocks of a skill's body (split at blank lines and list items) that mention a ratio recorded as `null`."""
    blocks = re.split(r"\n\s*\n|\n\s*(?:[-*]|\d+\.)\s+", body_of(text))
    return [block for block in blocks if "`null`" in block]


@pytest.mark.parametrize("name", ["financial-analysis", "information-gaps"])
def test_the_skills_explain_a_null_ratio_by_basis_and_not_by_an_assumed_denominator(name):
    passages = null_passages(skill_text(name))
    assert passages, "the skill says what a null ratio is"
    for passage in passages:
        assert "framework-computed" in passage and "analyst-supplied" in passage, passage
    assert null_passages("- A ratio recorded as `null` is N/A (a zero or negative denominator).") != []
    assert not all("analyst-supplied" in p for p in null_passages("- A ratio recorded as `null` is N/A."))


# --- 2. what --check-sources guarantees ----------------------------------------------------------------------

@pytest.mark.parametrize("state, manifest, declared_but_nothing_saved", [
    ({"triage": {"sources": ["https://example.invalid/registry"]}}, [], True),
    ({"commercial": {"sources": ["Synthetic filing"]}}, [], True),
    ({"triage": {"sources": ["  "]}}, [], False),                                  # a blank citation declares nothing
    ({"triage": {"sources": ["x"]}}, [{"filename": "a.txt", "claim": "something unrelated"}], False),
    ({"triage": {"sources": ["x", "y"]}}, [{"filename": "gone.txt", "claim": "x"}], False),   # no file check
    ({"research": {"sources": ["x"]}}, [], False),                                  # other keys are not read
    ({"sources": ["x"]}, [], False),
    ({"inputs": {"sources": ["x"]}}, [], False),
], ids=["triage", "commercial", "blank", "one-unrelated-entry", "file-not-checked", "research-key", "top-level", "inputs"])
def test_check_sources_is_a_deal_level_floor_over_triage_and_commercial_citations(state, manifest,
                                                                                 declared_but_nothing_saved):
    """The whole guarantee the skills may rely on: `true` only when `triage` or `commercial` declares a citation and
    the manifest has no entries at all. `false` does not mean every citation is backed."""
    from source_manifest import missing_saved_sources
    assert missing_saved_sources(state, manifest) is declared_but_nothing_saved


def deal_with_manifest(workdir, content):
    write_state(COMPANY, PROPOSAL, triage={"sources": ["https://example.invalid/registry"]})
    (state_dir,) = (workdir / "deals" / COMPANY).glob(f"{PROPOSAL}_*")
    (state_dir / "sources").mkdir()
    (state_dir / "sources" / "manifest.json").write_text(content, encoding="utf-8")


@pytest.mark.parametrize("content", ["{ not json", "{}", "null", '{"a": 1}', '"text"'])
@pytest.mark.xfail(strict=True, reason="#207: a malformed manifest is a traceback or a false 'nothing saved'")
def test_check_sources_reports_an_unusable_manifest_as_one_error_line(workdir, content):
    deal_with_manifest(workdir, content)
    result = run_script("source_manifest", "--check-sources", "--company", COMPANY, "--proposal", PROPOSAL, cwd=workdir)
    assert result.returncode == 1 and result.stdout == ""
    assert result.stderr.startswith("error: ") and len(result.stderr.splitlines()) == 1


@pytest.mark.parametrize("name", ["evidence-discipline", "information-gaps"])
def test_the_skills_treat_a_tool_error_or_unusable_manifest_as_a_tool_error_not_a_finding(name):
    text = " ".join(skill_text(name).split())
    assert "tool error" in text and "traceback" in text, "a script failure is reported as such"
    assert "manifest.json" in text and "list of objects" in text, "the manifest is checked before it is trusted"


# --- 3. one dated folder ---------------------------------------------------------------------------------------

def documented_folder(names, proposal):
    """The rule the skills state: only folders named exactly `<proposal>_YYYY-MM-DD`, and the latest of them by
    comparing the date text."""
    dates = [n[len(proposal) + 1:] for n in names if n.startswith(proposal + "_")
             and re.fullmatch(r"\d{4}-\d{2}-\d{2}", n[len(proposal) + 1:])]
    return f"{proposal}_{max(dates)}" if dates else None


def test_the_folder_the_rule_selects_is_the_one_state_manager_and_its_scripts_use(workdir):
    from state_manager import state_path
    for proposal, date_str, steps in [
        (PROPOSAL, "2025-12-31", ["triage"]), (PROPOSAL, "2026-01-05", ["triage", "spread"]),
        (PROPOSAL, "2026-02-01", ["spread", "collateral"]),                       # the latest
        (PROPOSAL, "2026-9-1", ["project"]), (PROPOSAL, "2026-02-01.bak", ["project"]), (PROPOSAL, "latest", ["project"]),
        (PROPOSAL + "_B", "2026-09-09", ["project"]), ("Synthetic Fleet", "2027-01-01", ["project"]),
    ]:
        write_state(COMPANY, proposal, date_str=date_str, steps_completed=steps)
    names = [p.name for p in (workdir / "deals" / COMPANY).iterdir()]
    assert len(names) == 8
    chosen = documented_folder(names, PROPOSAL)
    assert chosen == f"{PROPOSAL}_2026-02-01"
    assert Path(state_path(COMPANY, PROPOSAL)).parent.name == chosen
    state = json.loads((workdir / "deals" / COMPANY / chosen / "state.json").read_text(encoding="utf-8"))
    assert state["date"] == "2026-02-01" and state["date"] == chosen.rsplit("_", 1)[1], "the file's own date agrees"
    result = run_script("state_manager", "--check-steps", "--company", COMPANY, "--proposal", PROPOSAL,
                        "--required", "spread,collateral", cwd=workdir)
    assert json.loads(result.stdout) == {"missing_steps": [], "ok": True}, "the script read the same file"


def test_the_rule_ignores_a_folder_that_is_not_exactly_the_proposal_and_an_iso_date():
    names = ["P_2026-01-01", "P_2026-02-01", "P_2026-3-1", "P_B_2027-01-01", "P_2027-01-01.bak", "Q_2030-01-01", "P_x"]
    assert documented_folder(names, "P") == "P_2026-02-01"
    assert documented_folder(["P_latest"], "P") is None


@pytest.mark.parametrize("name", [n for n in ["evidence-discipline", "financial-analysis", "information-gaps"]])
def test_each_skill_states_the_dated_folder_rule_and_reports_the_folder_it_read(name):
    text = " ".join(skill_text(name).split())
    assert "YYYY-MM-DD" in text and "exactly" in text and "latest" in text
    assert "name the folder" in text, "the report says which dated folder was read"


# --- 4. what reaches a shell -----------------------------------------------------------------------------------

def posix_sh():
    """A real POSIX sh, on any platform: `sh` on PATH, or the one Git for Windows ships next to git. None if there is
    none, and then the shell-level tests skip (as the git-dependent tests do), so the passing count is the same on every
    machine that has git, which the test-count badge relies on."""
    if sys.platform != "win32":
        return shutil.which("sh")
    git = shutil.which("git")
    for relative in ("../usr/bin/sh.exe", "../../usr/bin/sh.exe") if git else ():
        candidate = (Path(git).parent / relative).resolve()
        if candidate.is_file():
            return str(candidate)
    return None


needs_sh = pytest.mark.skipif(posix_sh() is None, reason="no POSIX sh available")
REFUSED_CHARACTERS = '\\ / : * ? " < > | $'
REFUSAL = f"contains any of `{REFUSED_CHARACTERS}`"


def refused(name):
    """The rule the skills state for a company or proposal before it is put inside double quotes in a command."""
    return (not name.strip() or name in (".", "..") or name.startswith("-")
            or any(ch in REFUSED_CHARACTERS.replace(" ", "") or ch == "`" or ord(ch) < 32 for ch in name))


ACCEPTED_NAMES = ["Synthetic Co", "O'Brien & Sons (UK), Ltd", "A;B", "x # y", "a && b", "%PATH% ~ {a,b}",
                  "Synthetic [Holdings] #2", "a  b", "Yahoo! Ltd", "Société Générale 株式会社"]
REFUSED_NAMES = ['x"; touch pwned; "', "$(touch pwned)", "`touch pwned`", "a\\b", "a/b", "a|b", "a:b", "a*b", "a?b",
                 "a<b", "a>b", "a\nb", "a\x00b", "--help", "-x", "..", ".", "", "   ", '"', "$HOME"]


def test_the_refusal_rule_refuses_every_payload_and_none_of_the_ordinary_names():
    assert [n for n in REFUSED_NAMES if not refused(n)] == []
    assert [n for n in ACCEPTED_NAMES if refused(n)] == []


@pytest.mark.parametrize("name", ["evidence-discipline", "financial-analysis", "information-gaps"])
def test_each_skill_states_the_refusal_rule_before_it_runs_anything(name):
    body = body_of(skill_text(name))
    text = " ".join(body.split())
    assert REFUSAL in text and "backtick" in text and "starts with `-`" in text
    lines = body.splitlines()
    first_command = next(i for i, line in enumerate(lines) if line.strip().startswith("python scripts/"))
    refusal_line = next(i for i, line in enumerate(lines) if "contains any of" in line or "Refuse (say why" in line)
    assert refusal_line < first_command, "the check comes before the first command that is run"


@pytest.mark.parametrize("deal_name", ACCEPTED_NAMES)
def test_a_name_the_rule_accepts_reaches_the_scripts_as_one_literal_argument(deal_name, workdir):
    write_state(deal_name, deal_name, steps_completed=["spread"])
    result = run_script("state_manager", "--check-steps", "--company", deal_name, "--proposal", deal_name,
                        "--required", "spread", cwd=workdir)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"missing_steps": [], "ok": True}, "found the deal of exactly that name"
    assert [p.name for p in (workdir / "deals").iterdir()] == [deal_name], "and invented no other folder"


@needs_sh
@pytest.mark.parametrize("deal_name", ACCEPTED_NAMES)
def test_a_name_the_rule_accepts_is_one_literal_word_inside_double_quotes_in_a_real_shell(deal_name, workdir):
    command = f'printf "%s" "{deal_name}"'
    done = subprocess.run([posix_sh(), "-c", command], cwd=workdir, capture_output=True, text=True, encoding="utf-8")
    assert done.stdout == deal_name and done.returncode == 0
    assert list(workdir.iterdir()) == [], "nothing else was run or created"


@needs_sh
@pytest.mark.parametrize("payload", ["$(touch pwned)", "`touch pwned`", 'x"; touch pwned; "'])
def test_the_payloads_the_rule_refuses_would_have_run_a_command_so_the_refusal_is_needed(payload, workdir):
    subprocess.run([posix_sh(), "-c", f'printf "%s" "{payload}"'], cwd=workdir, capture_output=True, text=True)
    assert (workdir / "pwned").exists(), "a harmless command ran: the check really can detect injection"


# --- 5. the frontmatter test and what it proves -----------------------------------------------------------------

def strict_frontmatter(text):
    """(fields, problems) for the YAML subset these files use, parsed strictly: `key: value` lines whose values are
    plain or quoted scalars (true/false for booleans), or `key:` followed by `  - item` lines, between `---` lines, one
    key once, spaces only. Anything else is a problem. This is NOT a general YAML parser: it proves the files stay
    inside a small, well-understood subset (the shapes Claude Code's documentation shows), not that every YAML
    construct would be read as intended."""
    lines = text.replace("\r\n", "\n").split("\n")
    problems, fields = [], {}
    if not lines or lines[0] != "---":
        return {}, ["the file does not start with a '---' line"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, ["the frontmatter is not closed with a '---' line"]
    last = None
    for number, line in enumerate(lines[1:end], start=2):
        where = f"line {number}"
        if "\t" in line:
            problems.append(f"{where}: a tab")
            continue
        item = re.fullmatch(r"  - (\S.*)", line)
        if item:
            if last is None or not isinstance(fields[last], list):
                problems.append(f"{where}: a list item that does not follow a bare 'key:' line")
                continue
            value = scalar(item[1], where, problems)
            fields[last].append(value)
            continue
        entry = re.fullmatch(r"([a-z][a-z-]*):(?: (.*))?", line)
        if not entry:
            problems.append(f"{where}: not 'key: value', 'key:' or '  - item'")
            continue
        key, raw = entry[1], entry[2]
        if key in fields:
            problems.append(f"{where}: {key} appears twice")
        if raw is None or raw == "":
            fields[key] = []
        else:
            fields[key] = scalar(raw, where, problems)
        last = key
    # a list that stayed empty is a key with no value at all
    return {k: (v if v != [] else "") for k, v in fields.items()}, problems


def scalar(raw, where, problems):
    if raw[0] in "'\"":
        if len(raw) < 2 or raw[-1] != raw[0]:
            problems.append(f"{where}: an unclosed quote")
        return raw[1:-1]
    if raw[0] in "[]{}&*!|>%@`#" or raw.startswith(("- ", "? ", ": ")):
        problems.append(f"{where}: a plain value cannot start with {raw[0]!r} (flow, anchor, tag, block or indicator)")
    if ": " in raw or " #" in raw or raw.endswith(":"):
        problems.append(f"{where}: a plain value cannot contain ': ' or ' #' or end with ':'")
    if raw in ("yes", "no", "on", "off", "True", "False", "TRUE", "FALSE", "null", "~"):
        problems.append(f"{where}: {raw!r} is a YAML boolean or null spelling; use true or false")
    return raw


@pytest.mark.parametrize("name", report_skills())
def test_the_shipped_frontmatter_parses_strictly_and_agrees_with_the_tests_other_reading(name):
    fields, problems = strict_frontmatter(skill_text(name))
    assert problems == []
    assert fields["disable-model-invocation"] == "true" and fields["context"] == "fork"
    assert fields["background"] == "false"
    assert fields["name"] == name and set(fields) == set(frontmatter(skill_text(name)))


@pytest.mark.parametrize("bad, expected", [
    ("---\nname: x\n\tdescription: y\n---\n", "a tab"),
    ("---\nname: x\nname: y\n---\n", "appears twice"),
    ("---\nname: x\nallowed-tools: [Read, Grep]\n---\n", "cannot start with '['"),
    ("---\nname: x\ndescription: |\n---\n", "cannot start with '|'"),
    ("---\nname: x\ndescription: &a y\n---\n", "cannot start with '&'"),
    ("---\nname: x\ndescription: *a\n---\n", "cannot start with '*'"),
    ("---\nname: x\ndescription: a: b\n---\n", "cannot contain ': '"),
    ("---\nname: x\ndescription: a # b\n---\n", "cannot contain ': '"),
    ("---\nname: x\ndescription: \"unclosed\n---\n", "an unclosed quote"),
    ("---\nname: x\ncontext: fork\n  - Read\n---\n", "does not follow a bare"),
    ("---\nname: x\ndisable-model-invocation: yes\n---\n", "use true or false"),
    ("---\nname: x\nallowed-tools:\n  - {a: b}\n---\n", "cannot start with '{'"),
    ("---\nname: x\n Name: y\n---\n", "not 'key: value'"),
    ("name: x\n", "does not start"),
    ("---\nname: x\n", "not closed"),
])
def test_the_strict_parser_rejects_what_the_looser_checks_would_let_through(bad, expected):
    _, problems = strict_frontmatter(bad)
    assert any(expected in p for p in problems), (bad, problems)


def test_the_strict_parser_accepts_the_shapes_the_documentation_shows():
    ok = ('---\nname: x\ndescription: "Quoted: with a colon"\nargument-hint: --company "<Name>" [more]\n'
          'allowed-tools:\n  - Read\n  - Bash(python scripts/policy_check.py *)\ncontext: fork\n---\nbody\n')
    fields, problems = strict_frontmatter(ok)
    assert problems == [] and fields["allowed-tools"] == ["Read", "Bash(python scripts/policy_check.py *)"]
    assert fields["description"] == "Quoted: with a colon"


# --- 6. read-only means what was tested, not a sandbox ----------------------------------------------------------

def test_the_user_page_describes_the_tested_read_only_procedure_and_not_a_sandbox():
    page = " ".join((REPO / "docs" / "skills.md").read_text(encoding="utf-8").split())
    assert "not an operating-system sandbox" in page and "pre-approves" in page and "not an allowlist" in page
    assert "unlisted" in page and "permission settings" in page
    assert "has no write or edit tool" not in page and "cannot write" not in page
