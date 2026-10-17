"""Documentation contracts, second set (issue #117, D3): the relationships no other test covers.

Already guarded elsewhere, and deliberately not repeated here: relative links and heading anchors, the cited
`CLAUDE.md` headings and the evergreen evaluation page (`test_docs.py`); every `--flag` a document passes to a script
(`test_command_flags.py`); the list of CLI scripts (`test_cli_subprocess.py`); CI's structure and security rules
(`test_ci_workflow.py`); the protected paths (`test_confidential_paths.py`); the figures and messages the worked
examples quote (`test_docs_examples.py`); refusal to downgrade a newer state (`test_state_manager.py`,
`test_legacy_state.py`).

What is left, and checked here, is a handful of facts a reader relies on that could change in the source without
any page noticing:

- the slash commands in `.claude/commands/` are the ones `docs/commands.md`, the registry and the repository map list;
- the settings keys documented as read or inert are the ones the code actually reads;
- the CI jobs the documentation names are the jobs in `ci.yml`;
- the repository paths the documentation cites as sources of truth exist;
- the schema version the data-model page shows is the one the code writes.

Each check is a small function over text, and each is also run on deliberately wrong input so it is shown to fail.
"""
import json
import re
from pathlib import Path

import pytest
from test_ci_workflow import split_jobs, workflow_text
from test_confidential_paths import PROTECTED_PATHS
from test_docs import doc_files, strip_fences

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def read(relative):
    return (REPO / relative).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Slash-command inventory
# ---------------------------------------------------------------------------

def command_files():
    return {p.stem for p in (REPO / ".claude" / "commands").glob("*.md")}


def commands_page_headings(text):
    """The commands that have their own `### /name` heading on the commands page."""
    return set(re.findall(r"^###\s+/([a-z][a-z-]*)\s*$", text, re.M))


def registry_commands(text):
    """The commands the registry lists: a numbered item whose first token is a backticked `/name`."""
    return set(re.findall(r"^\d+\.\s+`/([a-z][a-z-]*)`", text, re.M))


def repository_map_commands(text):
    """The commands named on the repository-map line that describes the registry."""
    line = next((ln for ln in text.splitlines() if "skills_registry.md" in ln and "Command and skill registry" in ln), "")
    return set(re.findall(r"(?<![\w/])/([a-z][a-z-]*)\b", line.partition("registry:")[2]))


def inventory_problems(actual, listings):
    """One message per listing that differs from the real set of command files."""
    problems = []
    for where, listed in listings.items():
        if listed != actual:
            problems.append(f"{where} lists {sorted(listed)}; .claude/commands/ has {sorted(actual)} "
                            f"(missing: {sorted(actual - listed)}, not a command: {sorted(listed - actual)})")
    return problems


def test_every_slash_command_file_is_listed_by_the_commands_page_the_registry_and_the_repository_map():
    listings = {
        "docs/commands.md (### /name headings)": commands_page_headings(read("docs/commands.md")),
        "config/skills_registry.md (numbered items)": registry_commands(read("config/skills_registry.md")),
        "docs/architecture.md (repository map)": repository_map_commands(read("docs/architecture.md")),
    }
    assert command_files(), "no slash commands found: the test is looking in the wrong place"
    assert inventory_problems(command_files(), listings) == []


def test_the_inventory_check_reports_a_command_that_is_missing_or_that_does_not_exist():
    actual = {"spread", "review"}
    problems = inventory_problems(actual, {"page": {"spread"}, "registry": {"spread", "review", "ghost"}})
    assert len(problems) == 2
    assert "missing: ['review']" in problems[0] and "not a command: ['ghost']" in problems[1]
    assert inventory_problems(actual, {"page": {"spread", "review"}}) == []


def test_the_command_parsers_read_what_the_documents_actually_use():
    assert commands_page_headings("## Deal steps\n\n### /triage\n\n### /calibrate-policy\n\ntext /not-a-heading\n") \
        == {"triage", "calibrate-policy"}
    assert registry_commands("1. `/triage`\n   - x\n2. `/research` (alt)\n- `/loose`\n") == {"triage", "research"}
    arch = "│   ├── skills_registry.md       Command and skill registry: /calibrate /calibrate-policy /review\n"
    assert repository_map_commands(arch) == {"calibrate", "calibrate-policy", "review"}


# ---------------------------------------------------------------------------
# Settings: read versus inert
# ---------------------------------------------------------------------------

SETTINGS_ROW = re.compile(r"^\|\s*`(?P<key>[a-z_]+)`(?P<extra>[^|]*)\|(?P<reader>[^|]*)\|", re.M)


def documented_settings(text):
    """({keys documented as read}, {keys documented as inert}) from the 'Which settings are read' table."""
    section = text.partition("## Which settings are read")[2].partition("\n## ")[0]
    read, inert = set(), set()
    for row in SETTINGS_ROW.finditer(section):
        keys = {row["key"], *re.findall(r"`([a-z_]+)`", row["extra"])}
        (inert if "**nothing**" in row["reader"] else read).update(keys)
    return read, inert


def keys_the_code_reads(scripts_dir=SCRIPTS):
    """Settings keys read through `settings.get("key")` / `settings["key"]` in any script."""
    found = set()
    for path in scripts_dir.glob("*.py"):
        found.update(re.findall(r"""\bsettings(?:\.get\(|\[)\s*["']([a-z_]+)["']""", path.read_text(encoding="utf-8")))
    return found


def settings_problems(read, inert, code_reads, shipped):
    problems = []
    if read != code_reads:
        problems.append(f"documented as read {sorted(read)} but the code reads {sorted(code_reads)}")
    undocumented = (shipped | code_reads) - (read | inert)
    if undocumented:
        problems.append(f"keys in config/settings.json or read by the code with no row in the table: "
                        f"{sorted(undocumented)}")
    if read & inert:
        problems.append(f"documented as both read and inert: {sorted(read & inert)}")
    wrongly_inert = inert & code_reads
    if wrongly_inert:
        problems.append(f"documented as inert but read by the code: {sorted(wrongly_inert)}")
    return problems


def test_the_settings_documented_as_read_or_inert_are_the_ones_the_code_reads():
    read_keys, inert = documented_settings(read("docs/configuration.md"))
    shipped = set(json.loads(read("config/settings.json")))
    assert read_keys and inert, "the 'Which settings are read' table was not found or is empty"
    assert settings_problems(read_keys, inert, keys_the_code_reads(), shipped) == []
    # The operating rules say the same thing; they must name every inert key the shipped file carries.
    claude = read("CLAUDE.md")
    assert [key for key in sorted(inert) if f"`{key}`" not in claude] == [], "CLAUDE.md does not name an inert key"


def test_the_settings_check_reports_each_kind_of_disagreement():
    ok = settings_problems({"maker_model"}, {"max_tokens"}, {"maker_model"}, {"maker_model", "max_tokens"})
    assert ok == []
    assert "code reads" in settings_problems({"maker_model"}, set(), {"maker_model", "checker_model"}, set())[0]
    assert any("no row" in p for p in settings_problems({"maker_model"}, set(), {"maker_model"}, {"new_key"}))
    assert any("inert but read" in p for p in settings_problems(set(), {"maker_model"}, {"maker_model"}, set()))


def test_the_table_parser_separates_read_keys_from_inert_ones():
    table = ("## Which settings are read\n\n| Key | Read by | Effect |\n| --- | --- | --- |\n"
             "| `maker_model` (required) | `orchestrator.py` | x |\n"
             "| `maker_temperature`, `checker_temperature` | **nothing** | y |\n"
             "| `max_tokens` | **nothing** | z |\n\n## Credentials\n| `other` | `x.py` | no |\n")
    assert documented_settings(table) == ({"maker_model"}, {"maker_temperature", "checker_temperature", "max_tokens"})


# ---------------------------------------------------------------------------
# CI job names in reader-facing documentation
# ---------------------------------------------------------------------------

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}


def ci_job_problems(job_ids, page_text, page_name):
    """The jobs a page should name in backticks, and the number of jobs it states, against the real workflow."""
    problems = [f"{page_name} does not name the CI job `{job}`" for job in job_ids if f"`{job}`" not in page_text]
    stated = re.search(r"\b(one|two|three|four|five|six|seven|eight) jobs\b", page_text)
    if stated and NUMBER_WORDS[stated.group(1)] != len(job_ids):
        problems.append(f"{page_name} says {stated.group(1)} jobs; ci.yml defines {len(job_ids)}: {sorted(job_ids)}")
    return problems


def test_the_ci_jobs_the_documentation_names_are_the_jobs_in_the_workflow():
    jobs = set(split_jobs(workflow_text()))
    assert jobs, "no jobs found in ci.yml"
    problems = []
    for page in ("docs/testing.md", "docs/architecture.md"):
        problems += ci_job_problems(jobs, read(page), page)
    assert problems == []
    # The weekly workflow is named by its file, which must exist, in the same pages.
    assert (REPO / ".github" / "workflows" / "mutation.yml").is_file() and "mutation.yml" in read("docs/testing.md")


def test_the_job_check_reports_a_job_the_page_forgot_and_a_wrong_count():
    problems = ci_job_problems({"test", "lint"}, "CI runs three jobs: `test`.", "page.md")
    assert problems == ["page.md does not name the CI job `lint`", "page.md says three jobs; ci.yml defines 2: ['lint', 'test']"]
    assert ci_job_problems({"test"}, "CI runs one jobs: `test`.", "p") == []


# ---------------------------------------------------------------------------
# Repository paths cited as sources of truth
# ---------------------------------------------------------------------------

CITED_ROOTS = ("scripts/", "tests/", "agents/", "config/", "docs/", "templates/", "evals/", "badges/",
               ".claude/", ".github/")
CITED_ROOT_FILES = {"pyproject.toml", "README.md", "CLAUDE.md", "requirements.txt", "requirements-dev.txt",
                    "requirements-mutation.txt", "requirements-security.txt", ".gitignore", "LICENSE"}
# Pages that quote old text on purpose (the move ledger quotes the README as it was).
PATH_CHECK_SKIPS = {"move-ledger.md"}


def cited_paths(text):
    """Concrete repository paths written as inline code: no placeholder, wildcard or example marker."""
    paths = set()
    for span in re.findall(r"`([^`\n]+)`", strip_fences(text)):
        token = re.sub(r":\d+(-\d+)?$", "", span.strip().split("::")[0]).split("#")[0]
        if not (token.startswith(CITED_ROOTS) or token in CITED_ROOT_FILES):
            continue
        if re.search(r"[<>*\[\]{}\s]|\.\.\.|YYYY", token):
            continue
        paths.add(token)
    return paths


def is_local_only(path):
    """A path under a git-ignored location: it exists on a user's machine, not in a fresh clone."""
    return any(path == protected.rstrip("/") or path.startswith(protected) for protected in PROTECTED_PATHS)


def missing_paths(paths, root=REPO):
    return sorted(p for p in paths if not is_local_only(p) and not (root / p.rstrip("/")).exists())


def test_every_repository_path_the_documentation_cites_exists():
    problems = {}
    for page in doc_files():
        if page.name in PATH_CHECK_SKIPS:
            continue
        gone = missing_paths(cited_paths(page.read_text(encoding="utf-8")))
        if gone:
            problems[page.relative_to(REPO).as_posix()] = gone
    assert problems == {}, "documentation cites repository paths that do not exist (renamed or removed?)"


def test_the_path_check_finds_real_citations_ignores_placeholders_and_reports_a_missing_file(tmp_path):
    text = ("See `scripts/state_manager.py:120`, `tests/test_x.py::test_y`, `docs/page.md#section`, `config/style_guide.md`,\n"
            "`scripts/<name>.py`, `deals/Acme/state.json`, `evals/baselines/<file>.json` and `scripts/gone.py`.\n"
            "```\n`scripts/in_a_fence.py`\n```\nPlain scripts/not_code.py is prose.\n")
    assert cited_paths(text) == {"scripts/state_manager.py", "tests/test_x.py", "docs/page.md",
                                 "config/style_guide.md", "scripts/gone.py"}
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "state_manager.py").write_text("", encoding="utf-8")
    assert missing_paths(cited_paths(text), root=tmp_path) == ["docs/page.md", "scripts/gone.py", "tests/test_x.py"]


@pytest.mark.parametrize("path, local", [("deals/Acme/state.json", True), ("inputs/", True), ("config/style_guide.md", True),
                                         ("evals/results/run/results.json", True), ("config/settings.json", False),
                                         ("evals/README.md", False)])
def test_git_ignored_locations_are_not_required_to_exist(path, local):
    assert is_local_only(path) is local


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

def test_the_schema_version_the_data_model_page_shows_is_the_one_the_code_writes():
    from state_manager import SCHEMA_VERSION
    shown = set(re.findall(r'"schema_version":\s*"([0-9]+\.[0-9]+\.[0-9]+)"', read("docs/data-model.md")))
    assert shown == {SCHEMA_VERSION}, (
        f"docs/data-model.md shows schema_version {sorted(shown)}; scripts/state_manager.py writes {SCHEMA_VERSION}")


# ---------------------------------------------------------------------------
# The removed system-instructions file (issue #188)
# ---------------------------------------------------------------------------

REMOVED_FILE = "config/system_instructions.md"
# The claims that file made possible: it is named, or something is called the shared (top-level) system prompt.
SYSTEM_PROMPT_CLAIM = re.compile(r"system_instructions|shared (?:top-level )?system prompt", re.I)
# The one page that records, as history, what was once said and moved.
HISTORY_PAGES = {"docs/move-ledger.md"}


def live_sources():
    """Every tracked file that describes or drives the framework today: the documents, the agent prompts, the commands,
    skills and registry, the scripts and the configuration. Tests are left out (they may name the file to guard it)."""
    patterns = ("CLAUDE.md", "README.md", "docs/*.md", "evals/README.md", "templates/README.md", "agents/*.md",
                ".claude/commands/*.md", ".claude/agents/*.md", ".claude/skills/*/SKILL.md", "config/*.md",
                "config/*.json", "scripts/*.py")
    found = {p for pattern in patterns for p in REPO.glob(pattern)}
    return sorted(p for p in found if p.relative_to(REPO).as_posix() not in HISTORY_PAGES)


def system_prompt_claims(files):
    """{path: [matching lines]} over a {path: text} mapping."""
    claims = {}
    for path, text in files.items():
        lines = [ln.strip() for ln in text.splitlines() if SYSTEM_PROMPT_CLAIM.search(ln)]
        if lines:
            claims[path] = lines
    return claims


def test_the_unused_system_instructions_file_stays_removed_and_nothing_claims_a_shared_system_prompt():
    """Nothing ever loaded `config/system_instructions.md`, so the pages that called it the shared system prompt were
    wrong and the file was removed. It must not return as a file nothing reads, and no live page, prompt, command or script
    may name it or say a shared system prompt exists. (Wiring one in would change model input: that is a prompt change
    with its own baseline, not a side effect.)"""
    assert not (REPO / REMOVED_FILE).exists(), f"{REMOVED_FILE} is back; nothing loads it (issue #188)"
    claims = system_prompt_claims({p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8") for p in live_sources()})
    assert claims == {}, "a live file refers to the removed system-instructions file or a shared system prompt"


def test_the_system_prompt_claim_check_flags_each_wording_and_passes_the_current_text():
    wrong = {
        "a.md": "`config/system_instructions.md` is the shared top-level system prompt both agents inherit.",
        "b.md": "Both agents inherit the shared system prompt.",
        "c.py": "text = read_text('config/system_instructions.md')",
        "d.md": "See System_Instructions.md.",
    }
    assert set(system_prompt_claims(wrong)) == set(wrong)
    right = {"agents.md": "Agent behaviour is set by `agents/*.md` alone; nothing adds a shared prompt.",
             "e.md": "Each agent has its own system prompt, loaded by the orchestrator."}
    assert system_prompt_claims(right) == {}
    assert live_sources(), "the check must scan something"
    assert REPO / "docs" / "move-ledger.md" not in live_sources()


# ---------------------------------------------------------------------------
# Dated deal-state lookup is consistent across the seven state-reading commands
# ---------------------------------------------------------------------------

STATE_LOOKUP_COMMANDS = (
    ".claude/commands/triage.md",
    ".claude/commands/research.md",
    ".claude/commands/spread.md",
    ".claude/commands/commercial.md",
    ".claude/commands/collateral.md",
    ".claude/commands/project.md",
    ".claude/commands/assemble.md",
)


# The maintainer-authorised paragraph (issue #251, comment 6229146299), identical in all seven commands. A PR may change
# placement and formatting only; any other change to this text needs a fresh authorisation on that issue.
AUTHORISED_STATE_LOOKUP = (
    "Glob `deals/<company>/<proposal>_*/state.json`. It lists at most 100 files, newest-modified first, which is not "
    "the same as latest by date; if the list reaches 100 files (the tool also flags truncation) it may be incomplete: "
    "say that the latest dated folder could not be established, and stop. Otherwise consider only a folder named "
    "exactly `<proposal>_YYYY-MM-DD` (the proposal, an underscore, a four-digit year, two-digit month and two-digit "
    "day); ignore every other folder, including another proposal that merely starts with the same words. Take the "
    "latest of those by comparing the date text: that is the folder the scripts read too (`state_manager` applies the "
    "same rule). Read its `state.json` and note the folder."
)


def dated_state_lookup_problems(text):
    """Report when a command does not use /review's exact dated-folder lookup and write-back rule."""
    body = " ".join(text.split())
    problems = []
    if AUTHORISED_STATE_LOOKUP not in body:
        problems.append("the lookup paragraph is not the maintainer-authorised text")
    if re.search(r"there should be at most one, regardless of date", body, re.I):
        problems.append("still claims there is at most one state file regardless of date")
    required = {
        "bounded glob": "Glob `deals/<company>/<proposal>_*/state.json`",
        "100-result stop": "if the list reaches 100 files",
        "incomplete-list warning": "it may be incomplete",
        "exact-folder naming rule": "only a folder named exactly `<proposal>_YYYY-MM-DD`",
        "latest-by-date rule": "Take the latest of those by comparing the date text",
        "same-file write-back rule": "write back to that same file",
    }
    for label, phrase in required.items():
        if phrase not in body:
            problems.append(f"missing {label}")
    return problems


@pytest.mark.parametrize("path", STATE_LOOKUP_COMMANDS)
def test_state_reading_commands_use_the_exact_dated_folder_rule(path):
    assert dated_state_lookup_problems(read(path)) == []


def test_the_dated_state_lookup_guard_detects_a_deliberately_wrong_glob_rule():
    correct = (
        "Glob `deals/<company>/<proposal>_*/state.json`. It lists at most 100 files, newest-modified first, "
        "which is not the same as latest by date; if the list reaches 100 files it may be incomplete. Otherwise "
        "consider only a folder named exactly `<proposal>_YYYY-MM-DD`; ignore other folders. Take the latest of "
        "those by comparing the date text. Read its `state.json` and write back to that same file."
    )
    wrong = correct.replace(
        "only a folder named exactly `<proposal>_YYYY-MM-DD`",
        "any folder matching the proposal prefix",
    )
    assert wrong != correct, "the mutation must change the text the guard checks"
    assert "missing exact-folder naming rule" in dated_state_lookup_problems(wrong)
    assert "the lookup paragraph is not the maintainer-authorised text" in dated_state_lookup_problems(wrong)


def documented_state_lookup_problems(text):
    body = " ".join(text.split())
    required = {
        "exact-folder naming": "exact `<proposal>_YYYY-MM-DD` folder",
        "latest-by-date selection": "choose the latest by comparing the date text",
        "bounded lookup": "stop if the lookup reaches 100 files",
        "incomplete results warning": "the list may be incomplete",
    }
    return [f"missing {label}" for label, phrase in required.items() if phrase not in body]


def test_commands_documentation_describes_the_same_dated_state_lookup_rule():
    assert documented_state_lookup_problems(read("docs/commands.md")) == []


def test_the_documented_lookup_guard_detects_a_deliberately_wrong_date_rule():
    correct = ("Look up an exact `<proposal>_YYYY-MM-DD` folder, choose the latest by comparing the date text, "
               "and stop if the lookup reaches 100 files because the list may be incomplete.")
    wrong = correct.replace("choose the latest by comparing the date text", "choose the first result")
    assert wrong != correct, "the mutation must change the text the guard checks"
    assert "missing latest-by-date selection" in documented_state_lookup_problems(wrong)

def test_public_issue_reporting_surfaces_private_security_policy_and_synthetic_data_only():
    security = read("SECURITY.md")
    security_guide = read("docs/security.md")
    issue_config = read(".github/ISSUE_TEMPLATE/config.yml")
    bug_template = read(".github/ISSUE_TEMPLATE/bug_report.md")

    assert "private vulnerability reporting" in security.lower()
    assert "report a vulnerability" in security.lower()
    assert "SECURITY.md" in security_guide
    assert "security/advisories/new" in issue_config
    assert "public" in issue_config.lower() and "do not disclose details publicly" in issue_config.lower()
    assert "public issue" in bug_template.lower() and "synthetic data only" in bug_template.lower()
    for heading in ("## Problem", "## Concrete scenario", "## Proposed fix", "## Acceptance criteria"):
        assert heading in bug_template

