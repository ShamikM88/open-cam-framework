# Move ledger

The record of what moved where when the documentation was reorganised in the first tranche of issue #117. Its source is `README.md` and `CLAUDE.md` exactly as they were at commit `2d1dffb` (read them with `git show 2d1dffb:README.md` and `git show 2d1dffb:CLAUDE.md`); each is divided into blocks (a heading, a top-level list item with its continuation lines, or a paragraph), and every block has a row below. This is a migration record, not a living document: it is not updated when the pages change afterwards.

**Dispositions.** *kept*: stays in the same file, unchanged. *moved*: removed from the source file and present, word for word apart from the fixes listed below, at the destination (a short or condensed rule is often kept in the source file as well; the note says so). *kept + copied*: present in both. *dropped*: a heading with no content of its own, replaced by a new one.

**Verification.** A script split both source files into paragraphs and checked that each one, after applying the recorded fixes, still occurs verbatim in the union of the new `README.md`, `CLAUDE.md` and `docs/` pages. The only paragraph not carried over is the heading `Directory layout`, which became `Repository map`.

## README.md

| Block | Source lines | Starts with | Destination | Disposition | Note |
| ---: | :--- | :--- | :--- | :--- | :--- |
| 11 | L76-76 | `## Maker-Checker agents` | docs/architecture.md | moved | heading re-created at the same level |
| 12 | L78-81 | `Agent  File  Role` | docs/architecture.md | moved |  |
| 13 | L83-84 | `The two prompts are kept deliberately independent — the Reviewer's val` | docs/architecture.md | moved |  |
| 14 | L86-86 | `### Grounding rule` | docs/architecture.md | moved | heading re-created |
| 15 | L88-95 | `Every fact in a generated CAM — company history, market/competitor dat` | docs/architecture.md | moved |  |
| 19 | L116-124 | `/triage, /research, /spread, /commercial, /collateral, and /project ea` | docs/architecture.md | moved | under a new heading, 'Slash commands and the agents' |
| 20 | L126-126 | `## Templates` | docs/outputs.md | moved | README keeps a four-line summary |
| 21 | L128-129 | `All reference templates live under templates/ — see` | docs/outputs.md | moved |  |
| 22 | L131-133 | `- templates/cam/corporate_credit_cam.md — general` | docs/outputs.md | moved |  |
| 23 | L134-137 | `- templates/cam/asset_finance_cam.md — asset-backed /` | docs/outputs.md | moved |  |
| 24 | L138-139 | `- templates/spreading/default_spreading_template.xlsx —` | docs/outputs.md | moved |  |
| 25 | L141-144 | `The CAM templates are plain Markdown with bracketed placeholder fields` | docs/outputs.md | moved |  |
| 26 | L146-146 | `### Calibrated overrides (templates/local/)` | docs/outputs.md | moved |  |
| 27 | L148-153 | `templates/local/cam/<deal_type>_cam.md — written by scripts/calibrate.` | docs/outputs.md | moved |  |
| 28 | L155-155 | `### Financial spreading workbook` | docs/outputs.md | moved |  |
| 29 | L157-164 | `scripts/spreading_builder.py builds the .xlsx output as a` | docs/outputs.md | moved |  |
| 30 | L166-169 | `> Custom spreading templates: if your organization already has a stand` | docs/outputs.md | moved |  |
| 45 | L332-332 | `### Configuration` | docs/configuration.md | dropped | heading only; replaced by 'Overview' |
| 46 | L334-340 | `config/settings.json sets maker_model (required), plus optional` | docs/configuration.md#overview | moved | textual fix: pointer to the table below |
| 38 | L251-251 | `### Running tests` | README.md | kept | heading 'Running tests' re-created in README (retitled 'Running tests') |
| 39 | L253-256 | `bash` | README.md + docs/testing.md | kept + copied | the install-and-run fence appears in both |
| 40 | L258-269 | `The full suite needs no ANTHROPIC_API_KEY/network access to run -- eve` | docs/testing.md#running-the-tests | moved |  |
| 42 | L280-303 | `CI also runs ruff check . and bandit -r scripts/ -ll before pytest --` | docs/testing.md#running-the-tests | moved |  |
| 43 | L305-309 | `Beyond example-based unit tests the suite includes property-based test` | docs/testing.md#running-the-tests | moved |  |
| 44 | L311-330 | `Test-count badge. badges/test-count.json ({"passed": <int>}) is this p` | docs/testing.md#running-the-tests | moved |  |
| 41 | L271-278 | `Separately, evals/ holds a local-only live-model evaluation harness (p` | docs/evaluation.md | moved | transformed: volatile status clause removed (evergreen page) |
| 47 | L342-342 | `## Confidentiality — what never belongs in this repo` | docs/security.md | moved | heading re-created; README keeps a short summary |
| 48 | L344-345 | `This is a public, forkable framework repo, not a place to store real d` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 49 | L347-348 | `- inputs/ — your calibration sample PDFs and credit policy documents (` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 50 | L349-349 | `- config/style_guide.md — derived from those samples, so treat it the` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 51 | L350-350 | `- config/credit_policy.md — your calibrated institutional credit polic` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 52 | L351-352 | `- config/credit_policy_notes.md — analyst-confirmed corrections to how` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 53 | L353-353 | `- config/spreading_conventions.json — analyst-confirmed enterprise-wid` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 54 | L354-354 | `- config/deal_learnings.md — analyst-confirmed enterprise-wide end-of-` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 55 | L355-356 | `- templates/local/ — calibration-derived or auto-saved template overri` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 56 | L357-359 | `- deals/ — generated output for real borrowers (names, financials, PII` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 57 | L360-361 | `- evals/results/ — output of the local live-model evaluation harness (` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 58 | L363-364 | `This is checked automatically: tests/test_confidential_paths.py fails` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 59 | L366-368 | `Only the framework itself (agent prompts, scripts, blank templates, co` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 60 | L370-376 | `Adding a new feature that touches real reference material? Put its sto` | docs/security.md#confidentiality--what-never-belongs-in-this-repo | moved |  |
| 1 | L1-1 | `# OpenCAM Framework` | README.md | kept |  |
| 2 | L3-6 | `An open-source, agentic credit underwriting framework. It turns a fold` | README.md | kept |  |
| 3 | L8-9 | `It is designed to be forked: point it at your own historical CAMs and` | README.md | kept |  |
| 4 | L11-11 | `Two ways to run it, covered in full under Getting started:` | README.md | kept |  |
| 5 | L13-17 | `- Claude Code slash commands (primary, recommended) — /calibrate, /cal` | README.md | kept |  |
| 6 | L18-21 | `- Headless Python scripts (scripts/calibrate.py, scripts/orchestrator.` | README.md | kept |  |
| 7 | L23-24 | `Both paths produce the same outputs and share the same templates, styl` | README.md | kept |  |
| 8 | L26-26 | `## How it works` | README.md | kept |  |
| 9 | L28-66 | `1. Setup (once per organization/desk): calibrate against a handful of` | README.md | kept |  |
| 10 | L68-74 | `If a deal's --type doesn't match any template — neither a calibrated o` | README.md | kept |  |
| 16 | L97-97 | `## Skills / slash commands` | README.md | kept |  |
| 17 | L99-101 | `config/skills_registry.md defines the step-by-step workflow;` | README.md | kept |  |
| 18 | L103-114 | `Command  Inputs  Produces` | README.md | kept |  |
| 31 | L171-171 | `## Getting started` | README.md | kept |  |
| 32 | L173-173 | `### Option A: Claude Code slash commands (recommended, no separate API` | README.md | kept |  |
| 33 | L175-176 | `Open this repo in Claude Code (or the desktop app's Code tab) — the co` | README.md | kept |  |
| 34 | L178-212 | `1. (Recommended) Calibrate to your own style and templates: put a few` | README.md | kept |  |
| 35 | L214-214 | `### Option B: Headless Python scripts (scriptable, needs ANTHROPIC_API` | README.md | kept |  |
| 36 | L216-216 | `For automation, CI, or running outside an interactive Claude Code sess` | README.md | kept |  |
| 37 | L218-249 | `1. Install dependencies (Python 3.10+ recommended):` | README.md | kept |  |
| 61 | L378-378 | `## Roadmap` | README.md | kept |  |
| 62 | L380-383 | `- x Derive a base CAM template shape from calibration samples, not jus` | README.md | kept |  |
| 63 | L384-385 | `-   Support ingesting a user-supplied Excel spreading template, so sub` | README.md | kept |  |
| 64 | L386-387 | `-   Similarity-based (not just type-string-based) detection of when a` | README.md | kept |  |
| 65 | L389-392 | `> Note on calibrated templates: calibrate.py instructs the model to st` | README.md | kept |  |
| 66 | L394-394 | `## License` | README.md | kept |  |
| 67 | L396-396 | `MIT` | README.md | kept |  |

## CLAUDE.md

| Block | Source lines | Starts with | Destination | Disposition | Note |
| ---: | :--- | :--- | :--- | :--- | :--- |
| 5 | L13-13 | `## Directory layout` | docs/architecture.md | moved | heading 'Directory layout' became 'Repository map'; CLAUDE.md keeps a short file map |
| 6 | L15-81 | `` | docs/architecture.md | moved | the annotated tree, verbatim |
| 53 | L357-394 | `- scripts/calibrate.py --type <deal_type> (headless; needs ANTHROPIC_A` | docs/cli-reference.md#calibrate | moved | condensed rule kept in CLAUDE.md |
| 55 | L431-441 | `- scripts/orchestrator.py (headless; needs ANTHROPIC_API_KEY) — the ma` | docs/cli-reference.md#orchestrator | moved | condensed rule kept in CLAUDE.md |
| 56 | L442-460 | `- scripts/deal_export.py — no anthropic dependency, so it's callable t` | docs/cli-reference.md#export | moved | condensed rule kept in CLAUDE.md |
| 58 | L466-521 | `- scripts/state_manager.py — no anthropic dependency, same testability` | docs/data-model.md#state-manager-and-statejson | moved | condensed rules kept in CLAUDE.md |
| 59 | L522-544 | `- scripts/source_manifest.py — no anthropic dependency, same testabili` | docs/data-model.md#source-manifest | moved | condensed rule kept in CLAUDE.md |
| 64 | L652-655 | `- scripts/conventions.py — see "Persisted conventions" above for its f` | docs/data-model.md#persisted-convention-stores | moved | textual fix: 'above' reference |
| 60 | L546-598 | `- scripts/policy_engine.py — no anthropic dependency. evaluate_deal_po` | docs/financial-model.md#policy-engine | moved | condensed rules kept in CLAUDE.md |
| 61 | L599-611 | `- scripts/policy_checks.py — no anthropic dependency. parse_underwrite` | docs/financial-model.md#draft-audit | moved | condensed rules kept in CLAUDE.md |
| 62 | L612-622 | `- scripts/policy_check.py — no anthropic dependency, same "importable` | docs/financial-model.md#combined-policy-check | moved | condensed rules kept in CLAUDE.md |
| 63 | L623-651 | `- scripts/spreading_check.py — no anthropic dependency, same "importab` | docs/financial-model.md#spreading-check | moved | condensed rules kept in CLAUDE.md |
| 57 | L461-465 | `- scripts/template_resolver.py — pure path-resolution logic shared acr` | docs/outputs.md#template-resolution | moved |  |
| 71 | L770-770 | `### What config/settings.json actually controls` | docs/configuration.md#which-settings-are-read | moved | heading kept in CLAUDE.md with a short summary (cited by orchestrator.py) |
| 72 | L772-774 | `Only the model and temperature keys are read; the other keys the shipp` | docs/configuration.md#which-settings-are-read | moved | summary kept in CLAUDE.md |
| 73 | L776-786 | `Key  Read by  Effect` | docs/configuration.md#which-settings-are-read | moved | summary kept in CLAUDE.md |
| 74 | L788-790 | `Wiring these keys in is deliberately not done: one max_tokens could no` | docs/configuration.md#which-settings-are-read | moved | summary kept in CLAUDE.md |
| 75 | L792-792 | `## Testing and static analysis` | CLAUDE.md | kept | heading kept; body condensed (see 76-87) |
| 76 | L794-795 | `All tool settings live in pyproject.toml (issues #139, #142); CI and l` | docs/testing.md | moved |  |
| 77 | L797-802 | `- Ruff (ruff check .): E9,F63,F7,F82 (syntax/undefined names) plus B (` | docs/testing.md#ruff-and-bandit | moved | condensed requirement kept in CLAUDE.md |
| 78 | L803-806 | `- Pytest: filterwarnings = "error" (every warning fails; fix the cause` | docs/testing.md#pytest | moved | condensed requirement kept in CLAUDE.md |
| 79 | L807-821 | `- Coverage: pytest --cov measures line + branch coverage of scripts/.` | docs/testing.md#coverage | moved | condensed requirement kept in CLAUDE.md |
| 80 | L822-834 | `- The command-line surface is tested as a user meets it (issue #144).` | docs/testing.md#the-command-line-surface | moved | condensed requirement kept in CLAUDE.md |
| 81 | L835-838 | `- The configuration is itself tested: tests/test_pytest_config.py runs` | docs/testing.md#the-configuration-is-itself-tested | moved | condensed requirement kept in CLAUDE.md |
| 82 | L839-850 | `- Numbered cross-references between prompts and docs are guarded (test` | docs/testing.md#numbered-cross-references-between-prompts-and-docs | moved | condensed requirement kept in CLAUDE.md |
| 83 | L851-871 | `- CI (.github/workflows/ci.yml, structure pinned by tests/test_ci_work` | docs/testing.md#ci | moved | condensed requirement kept in CLAUDE.md |
| 84 | L872-897 | `- Mutation testing (.github/workflows/mutation.yml, configuration tool` | docs/testing.md#mutation-testing | moved | condensed requirement kept in CLAUDE.md |
| 85 | L898-904 | `- Property-based tests (tests/test_properties.py, issue #143; hypothes` | docs/testing.md#property-based-tests | moved | condensed requirement kept in CLAUDE.md |
| 86 | L905-911 | `- Legacy and malformed state.json (tests/test_legacy_state.py, issue #` | docs/testing.md#legacy-and-malformed-state.json | moved | condensed requirement kept in CLAUDE.md |
| 87 | L912-923 | `- Golden-output tests (tests/test_golden_outputs.py, issue #145): the` | docs/testing.md#golden-output-tests | moved | condensed requirement kept in CLAUDE.md |
| 66 | L662-667 | `- scripts/check_coverage.py — no anthropic dependency. python scripts/` | docs/testing.md#test-tooling-scripts | moved | condensed rule kept in CLAUDE.md |
| 67 | L668-672 | `- scripts/mutation_report.py — no anthropic dependency. Turns mutmut r` | docs/testing.md#test-tooling-scripts | moved | condensed rule kept in CLAUDE.md |
| 68 | L673-702 | `- scripts/check_test_count.py — no anthropic dependency. parse_passed_` | docs/testing.md#test-tooling-scripts | moved | condensed rule kept in CLAUDE.md |
| 65 | L656-661 | `- scripts/pii_scan.py — no anthropic dependency. Heuristic (currently` | docs/security.md#pii-scan | moved |  |
| 69 | L703-765 | `- scripts/run_evals.py, eval_cases.py, eval_oracles.py, eval_report.py` | docs/evaluation.md#how-it-works | moved | transformed: the sentence 'no live evaluation has been run yet' replaced by a pointer to issue #151; the dated status lives in docs/README.md and CLAUDE.md |
| 1 | L1-1 | `# CLAUDE.md` | CLAUDE.md | kept |  |
| 2 | L3-3 | `Guidance for Claude Code when working in this repository.` | CLAUDE.md | kept |  |
| 3 | L5-5 | `## Project overview` | CLAUDE.md | kept |  |
| 4 | L7-11 | `OpenCAM Framework is an open-source, agentic credit underwriting toolk` | CLAUDE.md | kept |  |
| 7 | L83-83 | `## Maker-Checker agents` | CLAUDE.md | kept |  |
| 8 | L85-85 | `Two independent agent prompts drive every deal, invoked in sequence by` | CLAUDE.md | kept |  |
| 9 | L87-90 | `- agents/underwriter_agent.md — the "Maker". Acts as an` | CLAUDE.md | kept |  |
| 10 | L91-97 | `- agents/risk_reviewer_agent.md — the "Checker". Acts as an` | CLAUDE.md | kept |  |
| 11 | L99-100 | `Keep these two prompts independent — the Checker's value comes from au` | CLAUDE.md | kept |  |
| 12 | L102-102 | `## Context Window & State Management Protocol` | CLAUDE.md | kept |  |
| 13 | L104-109 | `A CAM is assembled across multiple steps (/triage → /spread → /commerc` | CLAUDE.md | kept |  |
| 14 | L111-114 | `File-backed state. All financial metrics, user inputs (PD, LGD, Experi` | CLAUDE.md | kept |  |
| 15 | L116-130 | `json` | CLAUDE.md | kept |  |
| 16 | L132-135 | `model_provenance (written by orchestrator.py's headless pipeline) reco` | CLAUDE.md | kept |  |
| 17 | L137-143 | `financials_source is "framework-computed" (default -- every ratio inde` | CLAUDE.md | kept |  |
| 18 | L145-148 | `Two separate raw-figure stores, never blended (see issue #121). A deal` | CLAUDE.md | kept |  |
| 19 | L149-151 | `- multi_period_financials -- written only by scripts/spreading_check.p` | CLAUDE.md | kept |  |
| 20 | L152-154 | `- analyst_supplied_financials -- written only by /spread's analyst-sup` | CLAUDE.md | kept |  |
| 21 | L156-161 | `scripts/deal_export.py's _financial_data_from_state() is the one place` | CLAUDE.md | kept |  |
| 22 | L163-170 | `Checkpoint after every step. /triage, /research, /spread, /commercial,` | CLAUDE.md | kept |  |
| 23 | L172-175 | `Re-hydration rule. At the start of any step, or when a session resumes` | CLAUDE.md | kept |  |
| 24 | L177-178 | `No in-memory-only state. Absolute figures, debt values, and audit verd` | CLAUDE.md | kept |  |
| 25 | L180-181 | `Confidentiality. state.json lives under deals/, which is already git-i` | CLAUDE.md | kept |  |
| 26 | L183-183 | `## Source material persistence` | CLAUDE.md | kept |  |
| 27 | L185-189 | `state.json's sources lists (written by /triage, /research, /commercial` | CLAUDE.md | kept |  |
| 28 | L191-199 | `Per-step, not batched. Matching the state-management protocol above, w` | CLAUDE.md | kept |  |
| 29 | L201-204 | `Only the source of record, not scratch artifacts. Save the actual docu` | CLAUDE.md | kept |  |
| 30 | L206-207 | `Confidentiality. sources/ lives under deals/<Company>/<Proposal>_<Date` | CLAUDE.md | kept |  |
| 31 | L209-209 | `## Persisted conventions` | CLAUDE.md | kept |  |
| 32 | L211-218 | `Every convention adjustment an analyst confirms is otherwise deal-scop` | CLAUDE.md | kept |  |
| 33 | L220-221 | `Three independent mechanisms, each with two independent scopes (borrow` | CLAUDE.md | kept |  |
| 34 | L222-228 | `- Spreading conventions (/spread's "Check for a persisted convention"` | CLAUDE.md | kept |  |
| 35 | L229-233 | `- Credit-policy interpretation notes (/review's "Persisting a policy-i` | CLAUDE.md | kept |  |
| 36 | L234-245 | `- Deal learnings (/assemble's and /research's own "Surface end-of-deal` | CLAUDE.md | kept |  |
| 37 | L247-253 | `Always confirmed, always disclosed, never silently assumed. /spread su` | CLAUDE.md | kept |  |
| 38 | L255-260 | `Only ever consulted interactively. scripts/conventions.py is deliberat` | CLAUDE.md | kept |  |
| 39 | L262-266 | `Confidentiality. deals/<Company>/_conventions.json and deals/<Company>` | CLAUDE.md | kept |  |
| 40 | L268-268 | `## Slash commands (primary interface)` | CLAUDE.md | kept |  |
| 41 | L270-271 | `.claude/commands/.md implement config/skills_registry.md as` | CLAUDE.md | kept |  |
| 42 | L273-275 | `- /calibrate --type <deal_type> — Claude reads the PDFs in inputs/cali` | CLAUDE.md | kept |  |
| 43 | L276-283 | `- /calibrate-policy — org-wide, one-time setup (no --type, unlike /cal` | CLAUDE.md | kept |  |
| 44 | L284-294 | `- /triage, /spread, /commercial, /collateral, /project — each loads th` | CLAUDE.md | kept |  |
| 45 | L295-302 | `- /research — standalone alternative to the full pipeline for a deal t` | CLAUDE.md | kept |  |
| 46 | L303-306 | `- /review — loads the Risk Reviewer role (agents/risk_reviewer_agent.m` | CLAUDE.md | kept |  |
| 47 | L307-310 | `- /assemble — resolves the CAM template via template_resolver logic (l` | CLAUDE.md | kept |  |
| 48 | L312-317 | `Gotcha when editing these files: there is no @path/to/file inline file` | CLAUDE.md | kept |  |
| 49 | L319-326 | `Gotcha when drafting Markdown that gets exported to .docx (a /research` | CLAUDE.md | kept |  |
| 50 | L328-338 | `Second gotcha, same function: a fenced code block tagged  json  is del` | CLAUDE.md | kept |  |
| 51 | L340-353 | `Third gotcha, same function: a standalone !alt line embeds that image` | CLAUDE.md | kept |  |
| 52 | L355-355 | `## Execution scripts` | CLAUDE.md | kept | heading kept; body condensed, per-script reference moved to docs/ |
| 54 | L395-430 | `- scripts/textio.py — no anthropic dependency. The repository's one te` | CLAUDE.md | kept | normative encoding/stdio rules |
| 70 | L767-768 | `Both calibrate.py and orchestrator.py require ANTHROPIC_API_KEY in the` | CLAUDE.md | kept |  |
| 88 | L925-925 | `## Confidentiality rule for new features` | CLAUDE.md | kept |  |
| 89 | L927-937 | `Before adding any feature where the user provides or the framework der` | CLAUDE.md | kept |  |
| 90 | L939-952 | `These locations are enforced, not just documented (issue #149): tests/` | CLAUDE.md | kept |  |
| 91 | L954-956 | `Don't copy a user-shared reference document into the repo at all unles` | CLAUDE.md | kept |  |
| 92 | L958-970 | `Promoting a local override upstream: if a file under templates/local/c` | CLAUDE.md | kept |  |
| 93 | L972-972 | `## Git workflow` | CLAUDE.md | kept |  |
| 94 | L974-977 | `- Auto-commit core changes: whenever a change is made to a core file —` | CLAUDE.md | kept |  |
| 95 | L978-979 | `- Pushing to GitHub (origin/main) still requires explicit confirmation` | CLAUDE.md | kept |  |

## Textual fixes applied to moved text

A moved block's relative links are re-based one directory down (`templates/` becomes `../templates/`). Beyond that, only these changes were made, each because the original wording pointed at something that is no longer in the same place:

| Original wording | Replaced by | Why |
| :--- | :--- | :--- |
| `see "Persisted conventions" above for its full behavior` | `see "Persisted conventions" in `CLAUDE.md` for its full behavior` | the section it pointed at stays in CLAUDE.md, no longer 'above' |
| `see "Testing and static analysis" for the floors.` | `see the Coverage section of this page for the floors.` | the floors are described on the page the text now lives on |
| `See "Testing and static analysis" for the floors.` | `See the Coverage section of this page for the floors.` | the floors are described on the page the text now lives on |
| `see "Testing and static analysis", Mutation testing)` | `see Mutation testing on this page)` | the section it pointed at moved into this page |
| `(see CLAUDE.md, "What `config/settings.json` actually controls")` | `(see "Which settings are read" below)` | the table it pointed at moved into this page |
| `see the confidentiality rule's "Promoting a local override upstream" note below` | `see the "Promoting a local override upstream" rule in `CLAUDE.md`'s confidentiality section` | the rule stays in CLAUDE.md |
| pattern `see "(?!Sample length\|Malformed state fails clearly\|Which settings are read)([^"]+)" below` | `see "\1" in `CLAUDE.md`` | section stays in CLAUDE.md; no longer 'below' |
| pattern `Maker-Checker agent prompts \(see below\)` | `Maker-Checker agent prompts (see Maker-Checker agents above)` | the section now sits above the tree on the same page |
| pattern `\(gitignored, see below\)` | `(gitignored, see security.md)` | the explanation moved to the security page |
| pattern `\(see the state management\s+protocol above\)` | `(see the state management protocol in `CLAUDE.md`)` | the protocol stays in CLAUDE.md |
| pattern `see the confidentiality rule below\)` | `see the confidentiality rule in `CLAUDE.md`)` | the rule stays in CLAUDE.md |
| pattern `see that script's\s+entry above and issue #97` | `see that script's entry in the CLI reference and issue #97` | deal_export's entry is on another page |
| pattern `See "Source material persistence" above\.` | `See "Source material persistence" in `CLAUDE.md`.` | the section stays in CLAUDE.md |
| pattern `shared across the scripts\s+above and `/assemble`` | `shared across the scripts and `/assemble`` | 'above' referred to the old script list |
| pattern `on the roadmap to support directly \(see below\)` | `on the roadmap to support directly (see the roadmap in the README)` | the roadmap stays in the README |
| pattern `See "Testing\s+and static analysis" for the floors\.` | `See the Coverage section of the testing page for the floors.` | the floors are described on the testing page |
| pattern `\(see the directory layout above\)` | `(see the repository map in the architecture page)` | the tree moved to the architecture page |
| the README's "no live evaluation has been run yet" clause (in the evaluation summary) | removed | `docs/evaluation.md` is evergreen; the dated status is in `docs/README.md` ("Project status") and `CLAUDE.md` |
| `CLAUDE.md`'s "no live evaluation has been run yet" sentence (in the evaluation harness entry) | replaced by a pointer to issue #151 | same |
| the README roadmap's "(see above)" | a link to `docs/outputs.md` | the Templates section it pointed at moved there |

## New text with no source block

- `docs/README.md` (index, source-of-truth table, project status, how to keep the docs current), `docs/architecture.md` (everything above "Maker-Checker agents"), `docs/decisions.md`, and the "Credentials" section of `docs/configuration.md`.
- In `CLAUDE.md`: "Documentation layers and source of truth", "File map", "Editing the agent prompts and commands", the condensed "Execution scripts" entries (except `textio.py` and the credentials paragraph, which are kept as they were), the short "What `config/settings.json` actually controls", and the condensed "Testing and static analysis" list.
- In `README.md`: "Authentication, cost and credentials", the short Maker-Checker, Templates, Running tests and Confidentiality sections, and the Documentation index.
- The `docs/` page introductions and status banners, and `tests/test_docs.py`.
