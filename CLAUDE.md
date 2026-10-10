# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project overview

OpenCAM Framework is an open-source, agentic credit underwriting toolkit. It uses a
**Maker-Checker** pair of Claude prompts to draft and audit Credit Assessment Memorandums
(CAMs), then exports the result to `.docx` and `.xlsx`. There are two interfaces to the same
pipeline: `.claude/commands/*.md` (primary -- native Claude Code slash commands, no separate API
key) and the `scripts/*.py` CLIs (headless -- for automation/CI, needs `ANTHROPIC_API_KEY`).

## Documentation layers and source of truth

Three layers, each with one job:

- **`README.md`** -- the public landing page: what OpenCAM is, the two ways to run it, a quick start.
- **`docs/`** -- the human-facing documentation corpus (index: [docs/README.md](docs/README.md)): architecture,
  design decisions ([docs/decisions.md](docs/decisions.md)), reference pages for the scripts, state, financial
  model, outputs, configuration, workflows, commands, troubleshooting, testing, security, AI assurance and
  evaluation, and the contributing guide and maintainer runbook.
- **`CLAUDE.md`** (this file) -- the rules and engineering contracts Claude Code follows when it changes this
  repository. It is not a user manual: put an explanation, example or reference in `docs/`, and a rule that a
  change must not break here.

Documentation describes the system; the source defines it. Flags are defined by the scripts (`--help`), slash
commands by `.claude/commands/`, agent behaviour by `agents/*.md`, financial semantics by the implementation and
its tests, the state schema by `scripts/state_manager.py` and its tests, CI by `.github/workflows/`, tool
settings by `pyproject.toml`, and the operating rules by this file. When a page and its source disagree, fix the
page in the same change.

Several headings in this file are cited by name from code, commands and the registry (`Context Window & State
Management Protocol`, `Source material persistence`, `Persisted conventions`, `Execution scripts`, `What
config/settings.json actually controls`, `Testing and static analysis`, `Confidentiality rule for new features`,
and the bold `Promoting a local override upstream` and `Mutation testing` labels). Renaming one means updating
those citations; `tests/test_docs.py` fails if one disappears.

## File map

| Path | What it is | Detail |
| :--- | :--- | :--- |
| `agents/` | The two agent prompts: Underwriter (Maker) and Risk Reviewer (Checker) | [architecture](docs/architecture.md) |
| `.claude/commands/` | The slash commands (the interactive interface) | `config/skills_registry.md` |
| `config/` | `settings.json`, `system_instructions.md` (reference text that nothing loads), the slash-command registry; plus git-ignored generated files (`style_guide.md`, `credit_policy.md`, `credit_policy_notes.md`, `spreading_conventions.json`, `deal_learnings.md`) | [configuration](docs/configuration.md) |
| `scripts/` | The deterministic core (state, spreading, policy engine and checks, export, source manifest, conventions, text I/O), the headless entry points (`calibrate.py`, `orchestrator.py`) and the test-tooling scripts (`check_*`, `mutation_report.py`, `run_evals.py` and its `eval_*` modules) | [cli reference](docs/cli-reference.md), [data model](docs/data-model.md), [financial model](docs/financial-model.md) |
| `templates/` | Shipped generic CAM templates (`cam/`), the reference workbook layout (`spreading/`), git-ignored local overrides (`local/`) | [outputs](docs/outputs.md) |
| `tests/` | The pytest suite, golden snapshots (`snapshots/`), synthetic state fixtures (`fixtures/`) | [testing](docs/testing.md) |
| `evals/` | The local live-model evaluation harness's synthetic dataset, and its git-ignored results | [evaluation](docs/evaluation.md) |
| `badges/test-count.json` | The checked-in passing-test count, kept honest by CI | [testing](docs/testing.md) |
| `deals/` | Generated output, one folder per `[Company]/[Proposal]_[Date]` with `state.json` and `sources/` (git-ignored) | [data model](docs/data-model.md) |
| `inputs/` | Calibration samples and credit policy documents (git-ignored) | [security](docs/security.md) |
| `.github/workflows/` | `ci.yml` (`test`, `test-windows`, `runtime-smoke`, `security`) and the weekly `mutation.yml` | [testing](docs/testing.md) |
| `pyproject.toml` | The one place for tool settings: ruff, pytest, coverage floors, mutation scope | [testing](docs/testing.md) |

The complete annotated tree is in [docs/architecture.md](docs/architecture.md#repository-map).

## Maker-Checker agents

Two independent agent prompts drive every deal, invoked in sequence by `scripts/orchestrator.py`:

- **[agents/underwriter_agent.md](agents/underwriter_agent.md)** — the "Maker". Acts as an
  institutional Credit Risk Underwriter: processes financial statements, calculates TNW,
  EBITDA, DSCR, Gross Leverage and Working Capital Days, and drafts the CAM. Must cite source
  documents and never invent figures; follows the tone/layout rules in `config/style_guide.md`.

- **[agents/risk_reviewer_agent.md](agents/risk_reviewer_agent.md)** — the "Checker". Acts as an
  independent Credit Risk Officer: re-verifies the Maker's ratio calculations, flags ungrounded
  assertions or missing sources, challenges weak risk mitigants, and returns a verdict of
  `APPROVED` or `REJECTED` with revision notes. `/research` (a Go/No-Go screen, not a full CAM)
  gets a scoped-down Checker pass too — grounding/narrative checks apply, credit-policy and
  risk-mitigant checks don't, since no lending decision or structuring exists yet (see `/review`'s
  `--research-brief` mode, issue #87).

Keep these two prompts independent — the Checker's value comes from auditing the Maker without
sharing its reasoning, so avoid merging them or having one import the other's context.

## Editing the agent prompts and commands

- **Keep the two agent prompts independent.** Do not merge them or have one import the other's context (see
  [docs/decisions.md](docs/decisions.md#d1-the-maker-and-the-checker-are-independent-prompts)).
- **A prompt edit is a deliberate act of its own.** A deal records a content hash of each agent prompt in its
  `model_provenance`, and an evaluation baseline records the prompt hashes it measured, so editing
  `agents/*.md` makes earlier comparisons stale. Do not change a prompt as a side effect of other work.
- **Prompts do not depend on the documentation layout.** `agents/*.md` currently
  reference neither `README.md` nor `CLAUDE.md`, so moving documentation never needs a prompt edit; keep it that
  way. The few citations of `CLAUDE.md` by heading name (in `config/skills_registry.md`,
  `.claude/commands/assemble.md` and code comments) are why the headings listed under "Documentation layers" are
  stable.
- **Numbered references are guarded.** `Guideline N` and `Audit Checklist item N` are plain numbers in prose;
  inserting an item mid-list re-points every later reference. `tests/test_prompt_consistency.py` resolves each
  one against the real numbered list, so renumber deliberately and update `EXPECTED_SUBJECTS` in the same change.
- **Command files use plain prose to include another file** ("Read `agents/underwriter_agent.md` and act
  according to that role"); see the gotcha below about `@path` syntax.

## Context Window & State Management Protocol

A CAM is assembled across multiple steps (`/triage` → `/spread` → `/commercial` → `/collateral`
→ `/project` → `/assemble`, or `orchestrator.py`'s two agent calls), often in one long
conversation. Don't
rely on the conversation itself to remember the figures produced along the way — it can be
compacted/summarized, and a session can be resumed later. Every step checkpoints its results to
disk instead.

**File-backed state.** All financial metrics, user inputs (PD, LGD, Experian/bureau score),
calculated ratios, and deal metadata are persisted to
`deals/<Company>/<Proposal>_<Date>/state.json`, via [`scripts/state_manager.py`](scripts/state_manager.py).
Minimal schema (additional step-specific keys are fine; this is a floor, not a closed set):

```json
{
  "company": "...", "proposal": "...", "deal_type": "...", "date": "YYYY-MM-DD",
  "inputs": {"pd": "...", "lgd": "...", "experian_score": "..."},
  "financials": {"...": "..."},
  "financials_source": "framework-computed",
  "steps_completed": ["triage", "spread", "..."],
  "review_verdict": "...",
  "draft_path": "...",
  "model_provenance": {
    "maker_model": "...", "checker_model": "...",
    "underwriter_prompt_hash": "...", "risk_reviewer_prompt_hash": "..."
  }
}
```

`model_provenance` (written by `orchestrator.py`'s headless pipeline) records which model drafted/
audited this deal and a short content hash of `agents/underwriter_agent.md`/`risk_reviewer_agent.md`
at that moment -- so a historical deal stays identifiable even after the model or a prompt file
changes later (a model-risk-management concern; see the gap-analysis issue this closed).

`financials_source` is `"framework-computed"` (default -- every ratio independently recomputed
from raw line items via `scripts/spreading_builder.py`) or `"analyst-supplied"` (`/spread`'s
alternative mode -- an analyst's own pre-spread template figures, recorded exactly as given
rather than reconciled against this framework's raw schema, since institutions often treat
certain line items differently, e.g. Depreciation embedded in Cost of Goods Sold). When
`"analyst-supplied"`, `agents/underwriter_agent.md`'s Guideline 9 requires the CAM to carry an
explicit caveat disclosing this reduced audit guarantee.

**Two separate raw-figure stores, never blended (see issue #121).** A deal's raw line items (if
recorded at all -- an analyst-supplied deal isn't required to supply them, only their own
subtotals) live in exactly one of two places depending on `financials_source`, and no code reads
either one without checking which applies first:

- `multi_period_financials` -- written only by `scripts/spreading_check.py` (the framework-computed
  path: `/spread`'s default mode, `/project`), used as its own merge base for a later call and as
  `evaluate_downside_case()`'s stress-shock input.

- `analyst_supplied_financials` -- written only by `/spread`'s analyst-supplied mode, when the
  analyst also gives a raw breakdown alongside their own pre-spread figures. Same per-period raw
  shape as `multi_period_financials` above, but populated independently -- never merged with it.

`scripts/deal_export.py`'s `_financial_data_from_state()` is the one place that reads "this deal's
raw figures" for the exported workbook: it checks `financials_source` and reads
`analyst_supplied_financials` for an analyst-supplied deal, `financials`'s own nested `"raw"`
sub-key (framework-computed, from `evaluate_financial_model()`'s own output) otherwise -- any
future consumer needing the same thing must follow the same branch, never assume one store is
universal.

**Checkpoint after every step.** `/triage`, `/research`, `/spread`, `/commercial`, `/collateral`,
`/project`, `/review`, `/assemble`, and each of `orchestrator.py`'s two agent calls (draft, then
audit) write their results to `state.json` on completion. This is "checkpoint at every step
boundary," **not** a "pre-compaction hook" — there is no such callback available to a script or
a command's prompt, so don't document or reason about it as one. The guarantee this gives you is
weaker but real: whatever was true as of the last completed step is always on disk, so at worst
a compaction or a resumed session loses only the in-progress step, never anything already
checkpointed.

**Re-hydration rule.** At the start of any step, or when a session resumes, read `state.json`
for that company/proposal first, if it exists, and treat it as the source of truth. Never
reconstruct a financial figure, input, or verdict from a compacted/summarized conversation when
`state.json` already has it recorded.

**No in-memory-only state.** Absolute figures, debt values, and audit verdicts must exist as
structured disk artifacts (in `state.json`), never only in chat history.

**Confidentiality.** `state.json` lives under `deals/`, which is already git-ignored (see the
confidentiality rule below) — this introduces no new confidentiality gap.

## Source material persistence

`state.json`'s `sources` lists (written by `/triage`, `/research`, `/commercial`, and any other
step that cites a claim) only ever record the citation *string* — a URL, a filing name. That is
not enough on its own: a citation is only as auditable as the document it points to, and a web
page can change or disappear after the fact. [`scripts/source_manifest.py`](scripts/source_manifest.py)
closes that gap by persisting the actual source material itself.

**Per-step, not batched.** Matching the state-management protocol above, whichever step
(`/triage`, `/research`, `/spread`, `/commercial`, `/collateral`, `/project`) fetches or is given
source material saves it as part of that step's own checkpoint — see each command's own "Source
material" section. `save_source()` copies an already-downloaded/saved file into
`deals/<Company>/<Proposal>_<Date>/sources/` and appends an entry — `filename`, `url` (if any),
`step`, `claim`, `fetched_date` — to `sources/manifest.json`. It never fetches anything itself
(no network/browser dependency, matching `state_manager.py`'s own dependency-free pattern); the
session doing the fetching (a slash command's Claude session) hands it a file that already exists
on disk.

**Only the source of record, not scratch artifacts.** Save the actual document a claim was
grounded in — a downloaded filing, a saved web page, an analyst-provided document — never an
incidental intermediate artifact a step happened to produce along the way (e.g. an OCR page
render used only to extract a figure).

**Confidentiality.** `sources/` lives under `deals/<Company>/<Proposal>_<Date>/`, so it inherits
that same git-ignore wholesale — no new confidentiality gap, same as `state.json` above.

## Persisted conventions

Every convention adjustment an analyst confirms is otherwise deal-scoped and one-shot: if a
borrower's accounts embed Depreciation inside Cost of Goods Sold (see `/spread`'s analyst-supplied
mode), or the Risk Reviewer repeatedly flags the same credit-policy interpretation gap (see
`/review`'s Audit Checklist item 4, against a policy calibrated by `/calibrate-policy`), or any
other reusable insight surfaces once a deal is done, the analyst would otherwise have to
re-confirm or re-correct it fresh on every new deal from that same source. The three mechanisms
below each close one of these gaps -- a **deterministic, file-based local memory**, explicitly
not a model-training/fine-tuning loop and not the framework "learning" in an ML sense.

**Three independent mechanisms, each with two independent scopes (borrower-specific vs.
enterprise-wide) where a scope question applies.**

- Spreading conventions (`/spread`'s "Check for a persisted convention" step): borrower-specific
  (`deals/<Company>/_conventions.json`, one level above the dated proposal folders — the first
  company-level, cross-proposal file in this codebase) or enterprise-wide
  (`config/spreading_conventions.json`, applying across every borrower). `/spread` always asks
  the analyst explicitly which scope a newly confirmed convention applies at — never inferred.
  The confirmed note is copied into that deal's own `state.json` as `financials_source_note`, so
  `agents/underwriter_agent.md`'s Guideline 9 can cite it verbatim in the CAM's caveat.

- Credit-policy interpretation notes (`/review`'s "Persisting a policy-interpretation correction"
  step): fork-wide (`config/credit_policy_notes.md`), read by both agents the same dual-path way
  `config/credit_policy.md` itself is (see `agents/underwriter_agent.md`'s Guideline 10 and
  `agents/risk_reviewer_agent.md`'s Audit Checklist item 4) — **mandatory** for the Risk Reviewer
  to check.

- Deal learnings (`/assemble`'s and `/research`'s own "Surface end-of-deal learnings" steps,
  issues #86/#89): borrower-specific (`deals/<Company>/_learnings.md`) or enterprise-wide
  (`config/deal_learnings.md`). Sourced only from what's already checkpointed on disk by the end
  of a deal (`review_trail`'s REJECTED-verdict notes) — never freshly re-mined from a conversation
  that may already be compacted by deal's end. `/assemble`'s version must exclude any
  code-enforced deterministic reason mixed into the same `notes` string (those are bugs in that
  specific draft, never a standing fact) — `/research`'s version doesn't need this filter at all,
  since `--research-brief` mode skips `/review`'s Code-enforced check section entirely (issue #87),
  so every REJECTED entry there is already purely the Reviewer's own qualitative judgment. Both
  surface candidates as one batch, not a prompt per candidate. Unlike the other two mechanisms,
  this is **purely advisory** (`agents/underwriter_agent.md`'s Guideline 11 only — no corresponding
  Risk Reviewer checklist item, no structured-output declaration, never code-enforced).

**Always confirmed, always disclosed, never silently assumed.** `/spread` surfaces whatever
convention is on file and requires an explicit analyst response before applying it — even an
exact match — and never merges or auto-picks between the borrower-specific and enterprise-wide
scopes itself if they conflict. `/review` and `/assemble` only persist a note/learning on an
explicit "yes," never inferred from the analyst simply continuing the conversation. Every
application of a persisted convention must be visibly disclosed (the CAM's caveat, or the audit),
matching this framework's existing "never invent, always grounded, auditable" design.

**Only ever consulted interactively.** `scripts/conventions.py` is deliberately never wired into
`scripts/orchestrator.py`'s headless CLI — headless mode has no analyst present to confirm
anything (same for `/review`'s and `/assemble`'s own direct-file-write steps, which have no
headless equivalent either). The headless pipeline can still *inherit* an already-confirmed
`financials_source_note`/`credit_policy_notes.md`/deal-learnings file from a deal's `state.json`
or this fork's own `config/`/`deals/`, but it can never originate a new one on its own.

**Confidentiality.** `deals/<Company>/_conventions.json` and `deals/<Company>/_learnings.md` both
inherit the wholesale `deals/` git-ignore automatically. `config/spreading_conventions.json`,
`config/credit_policy_notes.md`, and `config/deal_learnings.md` each need their own explicit
`.gitignore` line (see below) — landed before any of their write paths were wired in, per the
confidentiality rule below.

## Slash commands (primary interface)

`.claude/commands/*.md` implement [`config/skills_registry.md`](config/skills_registry.md) as
native Claude Code commands, run in an interactive session with no `ANTHROPIC_API_KEY` needed:

The per-command descriptions (what each command does, its inputs and outputs, the state it writes) are in
[docs/commands.md](docs/commands.md); the files in `.claude/commands/` are authoritative. The facts a change
must not break:

- `/spread`'s default mode and `/project`'s forward-year and downside-case handling run `python
  scripts/spreading_check.py` as a Bash step rather than recalculating subtotals and ratios by hand (issue
  #98); `/project` always passes `--no-update-financials-source`, and `/spread`'s analyst-supplied mode
  deliberately skips independent recomputation.
- `/research` loops `/review --research-brief` until `APPROVED` (issue #87), then exports with
  `python scripts/research_export.py`; `/assemble` loops `/review` until `APPROVED`, then exports with
  `python scripts/deal_export.py`.
- `/spread` routes any figure it reads out of an image (a screenshot, a photo, a scanned page) through
  `scripts/transcription_check.py` in both modes (issue #132): nothing is written to `state.json`, and `spread` is
  not appended to `steps_completed`, until the analyst has explicitly confirmed a read-back; silence, an ambiguous
  answer or the model's own confidence is never confirmation, and a correction needs a new read-back. Signs are
  read under a declared, confirmed convention (never guessed), one deal never mixes the two modes, and the
  confirmation is bound to the image's bytes. Figures read from text keep the ordinary path.
- `/project` has an opt-in analyst-supplied mode (issue #124): it asks which mode applies and never infers or changes
  the default. Supplied forward-year subtotals/ratios go through `scripts/supplied_forecast.py`, are recorded as given
  and never pass through `evaluate_financial_model()` to produce a recorded value; `forecast_source` and the deal-wide
  `financials_source` say so (one basis per deal, no blending with framework-computed years). The framework's stress
  shocks apply to such a year only if its raw lines reproduce the supplied figures; otherwise the downside is the
  analyst's own scenario or is recorded under `downside_case["unavailable"]`, which the policy engine reports as an
  UNRESOLVABLE downside breach (never silently absent, never a pass).
- `/review` loads the Risk Reviewer role and returns `APPROVED` or `REJECTED`; a code-enforced reason makes
  `REJECTED` mandatory.
- `/calibrate-policy` and `/research` have no headless equivalent.

**Gotcha when editing these files:** there is no `@path/to/file` inline file-inclusion syntax in
Claude Code slash commands (a common misconception) -- use plain prose instructing Claude to
read the file with its Read tool instead (e.g. "Read `agents/underwriter_agent.md` and act
according to that role"). Don't reach for `` !`cat ...` `` dynamic context injection for this
either unless you've confirmed the target shell actually has `cat` -- some Windows/sandboxed
environments don't, and the Read-tool approach works everywhere regardless of shell.

**Gotcha when drafting Markdown that gets exported to `.docx`** (a `/research` brief, a
`/assemble` CAM draft): [`scripts/docx_builder.py`](scripts/docx_builder.py) follows normal
Markdown paragraph semantics -- a single newline is a soft wrap, not a paragraph break, so
consecutive plain lines join into one continuous paragraph. A short header/metadata block (e.g.
company/proposal/date/facility) or any other list of distinct fields must be written as a bullet
list (`- **Label:** value`) or genuinely blank-line-separated paragraphs -- never a bare run of
consecutive `**Label:** value` lines, which will merge into one run-on paragraph in the exported
document instead of rendering as separate lines.

**Second gotcha, same function:** a fenced code block tagged ```` ```json ```` is deliberately
stripped from the exported document entirely -- that's reserved for the Underwriter's own trailing
structured-output block (needed for `policy_checks.py`'s parsing, never meant for a client-facing
CAM). Any other fenced block (untagged, or tagged anything else) renders as a real monospace
block instead -- one paragraph per line, Consolas font, whitespace preserved exactly -- see issue
#113 (the ownership-tree diagram in `agents/underwriter_agent.md`'s Guideline 12 is the first use
of this). Using ```` ```json ```` for anything you actually want to appear in the document (the
ownership tree included) would silently delete it. A tagged fence line only ever *opens* a block,
never closes one (as in CommonMark) -- so a tree whose closing ```` ``` ```` was forgotten can't be
"closed" by the trailing ```` ```json ```` block's opening line, which would otherwise render the
narrative between them as monospace and leak the structured-output JSON into the document.

**Third gotcha, same function:** a standalone `![alt](path)` line embeds that image as its own
block (see issue #114) -- scaled down, never up, to the page's text width, with `alt` as both its
accessibility description and an italic caption beneath it (leave `alt` empty for no caption).
Only a real local file of type png/jpg/gif/bmp is embedded; the path is resolved against the
working directory like every other path in this repo, so a relative path must be valid from
wherever the export runs. Anything else -- a remote URL (this module never touches the network), a
missing file, an unsupported type such as SVG, an unreadable image -- never crashes the export and
is never silently dropped: it becomes a visible `[Image not embedded: ...]` placeholder paragraph
in the document plus a stderr warning, because the Risk Reviewer audits the Markdown (where the
image line still looks fine), not the exported file, so nothing else would catch a missing chart.
An image reference must be a line of its own -- inline mid-sentence images aren't supported. The
path is not restricted to `deals/` (an analyst's own screenshot may live anywhere), but it is
model-written Markdown text, so an image line pointing outside the deal's `sources/` folder is worth
a second look in review.

## Execution scripts

What follows are the rules each script's behaviour must keep; the full per-script reference (flags, return
values, the reasoning behind each rule) is in `docs/`: [CLI reference](docs/cli-reference.md),
[data model](docs/data-model.md), [financial model](docs/financial-model.md),
[outputs](docs/outputs.md), [testing](docs/testing.md), [security](docs/security.md) and
[evaluation](docs/evaluation.md). Every script marked "no `anthropic` dependency" must stay importable and
runnable without the SDK, so a slash command's Bash step and the tests can use it.

- **`scripts/calibrate.py`** (headless; needs `ANTHROPIC_API_KEY`) -- falls back to `--mock` (placeholder
  output, no API call, never an invented figure) when no key is set. A sample longer than `CHUNK_CHAR_LIMIT`
  (12,000 characters) is never silently truncated: the run asks to ignore or split, `--on-overflow` pre-answers,
  and with no terminal the default is split. Both result files are computed in memory before either is written.
  Full behaviour: [CLI reference](docs/cli-reference.md#calibrate).
- **`scripts/textio.py`** — no `anthropic` dependency. The repository's one text-reading policy
  (issue #137): **every text file is UTF-8**, because Python's default `open()` encoding is the
  platform's (cp1252 on Windows), which silently garbled an UTF-8 file's non-ASCII characters --
  the em dash and the box-drawing ownership-tree example in `agents/underwriter_agent.md` reached
  the model as mojibake in headless runs on Windows. `read_text(path, legacy_fallback=False)` is
  what `orchestrator.py` uses for every file it loads into a prompt: **strict UTF-8** (a BOM is
  tolerated) for shipped, repository-owned files (`agents/*.md`), where a decode error fails loudly
  with `TextEncodingError` naming the file; **UTF-8, then cp1252 with a `[WARN]` on stderr** naming
  the file, for user-owned files that may predate the policy (`config/style_guide.md`, the
  credit-policy and learnings files -- `calibrate.py` on Windows used to write the style guide in
  cp1252); a file that decodes under neither -- or whose cp1252 decode contains NUL bytes, i.e. it
  is really UTF-16 -- raises. The CAM template is read the same strict way. It **never** substitutes characters
  (`errors="replace"`): a `£` quietly turned into `?` in a policy is worse than a failure. Every
  writer passes `encoding="utf-8"` to `open()` itself. When adding any new `open()` of a text
  file, pass `encoding=` explicitly (ruff's `PLW1514` enforces it in CI, see "Testing and static analysis");
  tests use the `cp1252_default_open` fixture in `tests/conftest.py`, which makes an `open()` with
  no encoding behave like Windows on every platform so such a regression can't hide on Linux CI
  (it patches `builtins.open` only -- `pathlib`'s `read_text()`/`write_text()`, `io.open` and
  `os.fdopen` are not covered by it, and `PLW1514` only partly, so review those by eye; no
  production script uses unguarded pathlib text I/O today).
  **Output streams too (issue #154):** when a Windows script's stdout is redirected or piped (CI
  logs, Task Scheduler, `> log.txt`), Python encodes it as cp1252, so a `print()` of a character
  cp1252 lacks -- a `≥` in the Risk Reviewer's notes, a letter in a company name -- raised
  `UnicodeEncodeError` and aborted the run *after* the verdict was decided but before the revision
  or export. `configure_stdio()` makes stdout/stderr UTF-8 when they aren't already (an
  interactive console and an already-UTF-8 stream are left alone, and a closed or `None` stream is
  skipped; each stream keeps its own error policy, so there is no lossy `errors="replace"`; stdout
  becomes UTF-8 even under a non-UTF-8 POSIX locale, since UTF-8 is this repo's convention), and
  **every script's `if __name__ == "__main__":` block must start with `from textio import
  configure_stdio` then the bare call `configure_stdio()`** (a static test in
  `tests/test_stdio_encoding.py` fails for any CLI whose block doesn't -- including a call placed
  after another statement, a guarded call, or an `import textio` style call -- so a new script can't
  forget). It's called from the entry-point block,
  never from functions, so importing a module never reconfigures the interpreter's streams. The
  tests reproduce a cp1252 pipe on any platform (an in-memory cp1252 wrapper, or a child process
  started with `PYTHONIOENCODING=cp1252`), including the real `deal_export.py` CLI.
- **`scripts/orchestrator.py`** (headless; needs `ANTHROPIC_API_KEY`) -- loads state, prompts and template,
  runs the deterministic spreading and policy engine, then the Underwriter draft and the Risk Reviewer audit with
  code-enforced checks on the draft, checkpointing to `state.json` after each agent call, and exports through
  `deal_export.py`. A code-enforced rejection overrides a Reviewer `APPROVED`, never the reverse. It resolves the
  deal's dated folder once and passes that date on to the export. Full behaviour:
  [CLI reference](docs/cli-reference.md#orchestrator).
- **`scripts/deal_export.py`** (no `anthropic` dependency; importable and a CLI) -- every caller with a deal in
  progress resolves the dated folder itself (`state_manager.resolve_date_str()`); `export_deal()` reads and
  validates state **before** it creates a folder or auto-saves a template, and never writes into the git-tracked
  `templates/cam/`. Full behaviour: [CLI reference](docs/cli-reference.md#export).
- **`scripts/state_manager.py`** (no `anthropic` dependency) -- `write_state()` shallow-merges and keeps
  `company`/`proposal`/`date` in sync; **`write_state()` and `append_review_trail()` never downgrade a state**
  (a newer or malformed `schema_version` raises `SchemaVersionError` and leaves the file unchanged); each consumer
  validates only the keys it reads (`read_state(..., keys=...)`, a `STATE_KEYS_READ` per consumer) and every CLI
  turns a `StateError` into one `error:` line and exit status 1. Full behaviour:
  [data model](docs/data-model.md#state-manager-and-statejson).
- **`scripts/source_manifest.py`** (no `anthropic` dependency) -- saves the document of record, never scratch
  artifacts, per step; `--check-sources` is a deal-level floor (at least one saved source), not a 1:1 match.
  Full behaviour: [data model](docs/data-model.md#source-manifest).
- **`scripts/policy_engine.py`, `policy_checks.py`, `policy_check.py`, `spreading_check.py`** (no `anthropic`
  dependency) -- the deterministic governance core, which both interfaces share. **A ratio whose denominator is
  zero or negative is N/A (`None`); a covenant on an N/A ratio is UNRESOLVABLE for minimum and maximum alike,
  never PASS and never a false FAIL, with a `reason`.** Forward-year covenant results
  (`forward_covenant_results`) are **disclosure only**: no code-enforced reason reads them, so a forward FAIL or
  UNRESOLVABLE never rejects a deal by itself and can never go unrecorded; downside breaches (base PASS to
  stressed FAIL/UNRESOLVABLE) are enforced. A spreading merge is two levels deep and never a blind replace, and
  `/project`'s call never flips `financials_source` back to `framework-computed`. Full behaviour:
  [financial model](docs/financial-model.md).
- **`scripts/transcription_check.py`** (no `anthropic` dependency; no OCR, no network) -- the safeguards for figures
  read from an image (issue #132). The default is a read-back that never touches a deal; `--commit --confirm
  <digest>` records the figures only if the digest matches the staged figures as they are now and no cross-foot
  discrepancy is unresolved, and otherwise exits `1` with nothing written. Which lines feed a subtotal comes from
  `evaluate_financial_model()`, never a second mapping; ratios are never cross-footed; a check needs every feeding
  line transcribed; a mismatch a different sign reading would explain cannot be acknowledged; a commit is refused for
  a stale digest (including a changed image), a deal already in the other mode, and any predictable state problem
  (newer schema, malformed key, unreadable manifest, a computation that cannot succeed), all before the image is
  saved. The figures, the transcription record and the `spread` step go to `state.json` in one update, after the image
  is saved (the two are not atomic). It cannot see the image or
  know the analyst said yes; do not describe it as verifying figures.
  Full behaviour: [CLI reference](docs/cli-reference.md#transcription-check).
- **`scripts/supplied_forecast.py`** (no `anthropic` dependency) -- `/project`'s analyst-supplied mode (issue #124). One
  state update; records forward-year subtotals, ratios and optional raw lines exactly as given; a deal whose forward years
  are framework-computed, or whose history is framework-computed or of unknown basis, is refused (no blending); once an
  analyst-supplied forecast is on file `spreading_check` refuses to overwrite its forward years or relabel the deal.
  A year is stressed by the framework only after its lines reproduce the figures supplied. Full behaviour:
  [CLI reference](docs/cli-reference.md#supplied-forecast).
- **`scripts/conventions.py`** (no `anthropic` dependency) -- the persisted-convention stores; deliberately never
  wired into `orchestrator.py`. See "Persisted conventions" above and the [data model](docs/data-model.md).
- **`scripts/pii_scan.py`** -- heuristic scan for likely-real data in a calibrated template before it is promoted
  upstream; a clean result is not a guarantee. See [security](docs/security.md).
- **`scripts/check_test_count.py`** -- **fails CI on a mismatch and never auto-corrects** `badges/test-count.json`;
  CI never passes `--write`; whoever's PR changes the passing-test count updates the file in that PR with `python
  scripts/check_test_count.py pytest_output.txt --write` from a complete green run, and PRs that touch it merge
  one at a time (the second rebases, reruns pytest and runs `--write`). See [testing](docs/testing.md).
- **`scripts/check_coverage.py`, `scripts/mutation_report.py`** -- the coverage-floor check and the per-module
  mutation report (diagnostic: it never fails over a score). See [testing](docs/testing.md).
- **`scripts/run_evals.py` and the `eval_*` modules** (the local live-model evaluation harness) -- run only by an
  explicit `python scripts/run_evals.py`, never imported by `orchestrator.py`, `conftest.py` or CI; a hard call
  ceiling is checked **before the API key is read**; synthetic data only; results only under git-ignored
  `evals/results/`; a baseline is committed only by a deliberate manual step. The tests exercise it with fake
  clients: zero model calls, no key. The current evaluation status is under "Project status" in
  [docs/README.md](docs/README.md#project-status) and in issue #151. See [evaluation](docs/evaluation.md).

Both `calibrate.py` and `orchestrator.py` require `ANTHROPIC_API_KEY` in the environment and the
model configured in `config/settings.json`.

### What `config/settings.json` actually controls

Only the model and temperature keys are read: `maker_model` (required), and optional `checker_model`,
`maker_temperature`, `checker_temperature` and `calibrate_model`. The other keys the shipped file carries
(`max_tokens`, `default_currency`, `output_directory`, `template_directory`, `spreading_template_directory`) are
**inert**: editing them changes nothing, because the values are constants in the code (issue #108). Wiring them in
is deliberately not done: one `max_tokens` could not stand for three call sites with different limits, and the
evaluation harness's budgets and baselines assume the current values. The full table, with where each key is read
and the real values, is in [docs/configuration.md](docs/configuration.md#which-settings-are-read).

## Testing and static analysis

All tool settings live in [`pyproject.toml`](pyproject.toml), so CI and local runs read the same file and a rule
or threshold changes in one reviewed diff, never in a workflow command line. These are the requirements a change
must meet; the full description of each (what it checks, how it is tested, why) is in
[docs/testing.md](docs/testing.md).

- **Ruff and Bandit.** `ruff check .` (rules `E9,F63,F7,F82`, `B`, `UP`, `S`, and the preview rule `PLW1514`:
  `open()` without `encoding=`) and `bandit -r scripts/ -ll`. A finding in `scripts/` is fixed or carries a
  `# noqa: <code>` with the reason beside it. `tests/` ignores only the rules that are noise there.
- **Pytest.** `filterwarnings = ["error"]`, `--disable-socket` (no test opens a network connection) and a
  120-second per-test timeout. Install `requirements-dev.txt` before running `pytest`, or pytest rejects the
  options.
- **Coverage.** Line + branch over `scripts/`. CI enforces an 85% overall floor, a 90% floor on each of eight
  governance modules (`policy_engine`, `policy_checks`, `policy_check`, `spreading_builder`, `spreading_check`,
  `state_manager`, `deal_export`, `docx_builder`) and 90% on the lines a pull request changes
  (`diff-cover ... --config-file pyproject.toml`; without `--config-file` the floor is silently 0).
  `# pragma: no cover` needs a reason on the same line; code that only runs on Windows needs one. Lowering a floor
  or dropping a module is a reviewed decision.
- **The command-line surface is tested as a user meets it.** Every script with a `__main__` block is run as a real
  subprocess (`tests/test_cli_subprocess.py`; a new CLI must be added to its list), and every `python
  scripts/<name>.py --flag` that a document or command tells a reader to use is checked against that script's
  real `--help` (`tests/test_command_flags.py`).
- **Numbered cross-references are guarded.** `Guideline N` and `Audit Checklist item N` references are resolved
  against the real numbered lists (`tests/test_prompt_consistency.py`); after a renumbering, fix the references it
  lists and update its `EXPECTED_SUBJECTS` in the same change.
- **Documentation links and cited headings are checked** (`tests/test_docs.py`): relative links and heading
  anchors resolve, every page under `docs/` is linked from the index, and the headings that code and commands cite
  by name still exist.
- **CI** (`.github/workflows/ci.yml`, structure pinned by `tests/test_ci_workflow.py`): four jobs. `test` (Ubuntu)
  is the ruleset's required check -- keep the name. Workflow rules, all tested: every third-party action is pinned
  to a full commit SHA with a `# vX.Y.Z` comment; `permissions: {}` at the top and `contents: read` per job;
  `persist-credentials: false` on every checkout; no `pull_request_target`; CI never passes `--write` or
  `--update-snapshots`.
- **Ignoring a reviewed advisory.** Pin a fixed version if one exists; otherwise a reviewed PR adds `--ignore-vuln
  <ID>` to the `pip-audit` command with a comment line `# pip-audit ignore: <ID> -- <package> -- reason: <why it
  cannot be exploited here> -- revisit by: YYYY-MM-DD` (a test fails on an ignore without one). Never
  `continue-on-error`, `|| true`, or dropping a requirements file from the audit.
- **Mutation testing** (`.github/workflows/mutation.yml`, `[tool.mutmut]` in `pyproject.toml`): weekly and on
  demand only, never on a pull request, never a required check, and it never fails over a score. The eight
  governance modules have a diagnostic target of 75% (`[tool.opencam.mutation] target_critical`); survivors are
  triaged into issues. `calibrate.py` is in scope as a supporting module. Changing the scope keeps `only_mutate`
  equal to the critical modules plus the named supporting list (a test pins it).
- **Golden snapshots** (`tests/test_golden_outputs.py`): regenerate only with `pytest tests/test_golden_outputs.py
  --update-snapshots` after an intended change, and review the snapshot diff like code; a missing snapshot fails
  rather than being created, and CI never passes the flag.
- **Known-bad behaviour** found by a property test or a state fixture is a *strict* `xfail` naming its issue, so
  fixing it fails the test until the marker is removed. A change to `state.json`'s shape must keep the fixtures in
  `tests/fixtures/state/` working or add a new one.

## Confidentiality rule for new features

Before adding any feature where the user provides or the framework derives real reference
material from their business (calibration sample CAMs, a derived template, a future
user-supplied Excel spreading template, etc.), its storage location must already be listed in
`.gitignore` — never add it under a git-tracked path like `templates/cam/` or
`templates/spreading/`. This repo is public and forkable; the shipped defaults must stay generic
and shareable, while anything derived from one user's real documents stays local to their fork.
Current gitignored locations: `inputs/`, `config/style_guide.md`, `config/credit_policy.md`,
`config/credit_policy_notes.md`, `config/spreading_conventions.json`, `config/deal_learnings.md`,
`templates/local/`, `evals/results/` (the evaluation harness's per-run output -- it may contain
model text, even on synthetic data), `deals/` (which also covers any
`deals/<Company>/_conventions.json` and `deals/<Company>/_learnings.md`).

These locations are **enforced, not just documented** (issue #149): `tests/test_confidential_paths.py`
runs in the ordinary `test` job and fails, naming the path, if `git ls-files` lists any file under one
of them, if one is no longer ignored by the repository's own `.gitignore` (a global excludes file
cannot stand in for it, and a negated `!` rule counts as un-ignoring it), or if a `.gitignore` entry
is neither protected nor classified as not confidential. **A new confidential location must be added
to `PROTECTED_PATHS` in that test in the same PR as its `.gitignore` line**, and a new `.gitignore`
line that is *not* confidential (build noise, a secret type already covered by secret scanning) must be
added to `NOT_CONFIDENTIAL` there with a reason, so every ignore line is a conscious decision. A
deliberately tracked placeholder under a protected path (for example a `.gitkeep`) goes in
`ALLOWED_TRACKED` with a reason. The tracked-file check is case-insensitive (`Deals/`, `INPUTS/`) and
catches a tracked file or symlink with a protected directory's bare name; the ignore-rule check is
about the repository's own `.gitignore`, which is case-sensitive on Linux. The tests strip `GIT_*`
environment variables so they cannot be redirected at the real repository when run from a git hook.
It cannot see real names in GitHub issue or PR text; that stays a process rule.

Don't copy a user-shared reference document into the repo at all unless asked — even into an
already-gitignored folder — since that creates a new persistent copy of sensitive data they
didn't explicitly request.

**Promoting a local override upstream:** if a file under `templates/local/cam/` turns out to be
a genuinely useful, well-generalized CAM structure — not just this one deal's content — and has
been fully scrubbed of real data (see the roadmap note on this in the README: derivation is
prompted, not code-enforced, so verify by hand), recommend to the user that they open a PR
against the shared framework repo to add it as a new default under `templates/cam/`, rather than
leaving it stuck local to their fork. Before recommending it, run
`python scripts/pii_scan.py templates/local/cam/<deal_type>_cam.md` as a second line of defense
against the by-hand review above — it heuristically flags likely-real currency figures, emails,
phone numbers, dates, company names, and Companies House-style registration numbers left over
from calibration. A clean result is not a guarantee (it's a mechanical pattern scan, not
comprehension), so the by-hand review is still required either way. Never do this promotion (copy
a local override into `templates/cam/`, or open such a PR) without the user explicitly asking for
it first.

## Git workflow

- **Auto-commit core changes**: whenever a change is made to a core file — anything under
  `agents/`, `config/`, `scripts/`, or `.claude/commands/` — commit it to git right after the
  edit, with a message describing what changed and why. Don't batch unrelated core changes into
  one commit.

- Pushing to GitHub (`origin/main`) still requires explicit confirmation in chat for each push,
  per standard safety practice — auto-commit covers the local commit only, not the push.
