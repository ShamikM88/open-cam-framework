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
             "| `maker_temperature`, `checker_temperature` | `orchestrator.py` | y |\n"
             "| `max_tokens` | **nothing** | z |\n\n## Credentials\n| `other` | `x.py` | no |\n")
    assert documented_settings(table) == ({"maker_model", "maker_temperature", "checker_temperature"}, {"max_tokens"})


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
# Checker independence by interface (issue #204): the interactive claim is about the command files
# ---------------------------------------------------------------------------

def test_the_pages_say_interactive_review_shares_the_drafts_conversation_for_as_long_as_it_does():
    """`/review` runs in the conversation that holds the draft unless a command isolates it (a forked context or a
    subagent). Nothing does today, so the pages that distinguish the interfaces must say so. If #204 is resolved by
    isolating `/review`, the first assertions fail: update those pages in the same change."""
    from test_skill_inventory import frontmatter
    commands = REPO / ".claude" / "commands"
    review = frontmatter((commands / "review.md").read_text(encoding="utf-8"))
    assert "context" not in review and "agent" not in review, "/review is isolated now: revise the interface statements"
    for name in ("assemble", "research", "review"):
        text = (commands / f"{name}.md").read_text(encoding="utf-8")
        assert not re.search(r"\bsubagent\b|\bAgent tool\b|\bTask tool\b", text), f"{name}.md starts a subagent"
    for page in ("docs/ai-assurance.md", "docs/mvp-v1-prd.md"):
        assert "same conversation" in " ".join(read(page).split()), page
