# Data model

> The sections before "Module reference" are written for readers; "Module reference" and what follows were moved from `CLAUDE.md` or the README, unchanged apart from the fixes in the [move ledger](move-ledger.md).

How a deal's state is stored and guarded: the `state.json` file, its keys, the two raw-figure stores, resuming a deal, schema versions, saved sources and the persisted-convention stores. The normative protocol and the state-file schema example stay in `CLAUDE.md`.

## state.json at a glance

Each deal has one state file, `deals/<Company>/<Proposal>_<YYYY-MM-DD>/state.json`, plus a `sources/` folder beside
it. It is the record of the deal: every step reads it first and writes its results back when it finishes, so a
resumed session or a compacted conversation never has to reconstruct a figure from memory. (The rules are in
`CLAUDE.md`, "Context Window & State Management Protocol"; the reasons in [Design decisions](decisions.md), D3.)
`deals/` is git-ignored, so state never reaches version control.

An annotated fragment of the synthetic deal's state after `/spread`, `/project` and the structure steps (values
shortened):

```json
{
  "company": "Synthetic Co",
  "proposal": "Synthetic Fleet Loan",
  "date": "2026-10-04",
  "schema_version": "1.1.0",
  "deal_type": "asset_finance",
  "inputs": {"pd": "0.20%", "lgd": "LGD 3 (15%)"},
  "steps_completed": ["spread", "project"],
  "financials_source": "framework-computed",
  "multi_period_financials": {"FY-Current": {"revenue": 5000, "cost_of_sales": 3000, "...": "..."}},
  "financials": {"FY-Current": {"raw": {"...": "..."}, "ebitda": 1000, "tangible_net_worth": 2200, "...": "..."}},
  "ratios": {"FY-Current": {"dscr": 2.0, "gross_leverage": 2.0, "...": "..."},
             "FY+2": {"dscr": -0.20408163265306123, "gross_leverage": null, "...": "..."}},
  "stress_assumptions": {"revenue_haircut_pct": 5, "interest_rate_bump_bps": 200},
  "downside_case": {"financials": {"FY+1": {"...": "..."}}, "ratios": {"FY+1": {"gross_leverage": 5.15625, "...": "..."}}},
  "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
  "collateral": [{"asset_id": "AST-001", "exposure": 1200, "collateral_value": 1500, "...": "..."}],
  "security_package": [{"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "First"}],
  "guarantees": [{"provider": "Synthetic Parent Holdings", "type": "Corporate Guarantee", "amount": 500}]
}
```

### The keys

| Key | Written by | Holds |
| :--- | :--- | :--- |
| `company`, `proposal`, `date`, `schema_version` | every write | Deal identity, kept in sync on every write; the schema version of the writing framework |
| `deal_type`, `inputs` | `/triage`, `/collateral`, `/assemble`, the orchestrator | The CAM type; `pd`, `lgd` (user-supplied, never adjusted) and `experian_score` or another bureau score |
| `steps_completed` | every step | The names of steps that have finished (`triage`, `spread`, ...); `/assemble` hard-gates on `spread` |
| `triage`, `commercial` | `/triage`, `/commercial`, `/research` | The Go/No-Go verdict and rationale; the company and sector sections |
| `financials`, `ratios`, `multi_period_financials`, `financials_source` | `spreading_check.py` (run by `/spread` and `/project`) | Per-period subtotals (each with its raw inputs under `raw`) and ratios; the raw inputs as supplied; `"framework-computed"` |
| `analyst_supplied_financials`, `financials_source_note` | `/spread`, analyst-supplied mode | The analyst's raw breakdown (if given); the convention note the CAM caveat quotes (for figures read from an image, with a sentence saying so) |
| `financials_transcriptions` | `transcription_check.py --commit` (`/spread`, figures read from an image) | A list with one record per confirmed transcription: the kind of image, the mode, the saved source file and its SHA-256 fingerprint (never the image), the unit, the declared sign conventions, the periods, the confirmed digest and date, and the cross-foot outcome (matched, acknowledged with the analyst's reason, not assessed). Absent for figures read from text. One deal holds only one basis: a transcription in the other `/spread` mode is refused |
| `stress_assumptions`, `downside_case` | `spreading_check.py` (run by `/project`) | The three stress shocks; the stressed forward-year figures and ratios |
| `collateral`, `security_package` | `/collateral` | One entry per asset; one per charge |
| `covenants`, `guarantees` | `/project` | `{"metric", "type", "threshold"}` entries; `{"provider", "type", "amount"}` entries |
| `policy_state` | `/assemble`, the orchestrator | The computed conditions, covenant results, security gaps, downside breaches and forward covenant results, stored whole |
| `review_trail`, `review_verdict` | `/review`, the orchestrator's audit | The append-only history of verdicts and notes; the latest verdict |
| `draft_path`, `model_provenance` | `/assemble`; the orchestrator | The exported `.docx` path; which models and which prompt versions (content hashes) drafted and audited this deal |

Ratios that cannot be computed are `null`, never `0`. A step may add keys of its own; this table is a
floor.

### Two raw-figure stores, never blended

A deal's raw line items live in exactly one of two places, depending on `financials_source`:

| `financials_source` | Raw figures live in | Written by |
| :--- | :--- | :--- |
| `"framework-computed"` (default) | `multi_period_financials`, and `financials[period]["raw"]` | `spreading_check.py` |
| `"analyst-supplied"` | `analyst_supplied_financials` | `/spread`, when the analyst also gives a raw breakdown |

Code that needs "this deal's raw figures" (the workbook exporter, for one) checks `financials_source` first and reads
the matching store; it never assumes one store is universal. An analyst-supplied deal is not required to supply raw
figures at all, only its own subtotals.

### Resuming a deal

A step begins by finding the deal's state (the most recent dated folder for that company and proposal; a bare library
call or a brand-new deal gets today's date) and treats everything recorded there as true. A new annual review uses a
fresh folder (`orchestrator.py --new-review`) so it never inherits last year's inputs. The orchestrator also
checkpoints its grounded figures and `policy_state` to the state **before** it calls either agent, which is why a run
that fails at its first model call (for instance with no API key) still leaves the figures on disk.

### Schema versions and validation

`schema_version` is `MAJOR.MINOR.PATCH`. A state written by an older or unversioned framework is read and upgraded on
the next write; a **newer** or malformed version is refused on write (the file is left unchanged), because
writing would silently drop fields. Reading a newer file still works. Each step validates only the keys it reads, so
a deal is never rejected for a key a step does not use; the types it checks are: `financials`, `ratios`,
`multi_period_financials` and `analyst_supplied_financials` are objects keyed by period; `inputs` and
`stress_assumptions` are objects; `covenants`, `collateral`, `security_package`, `guarantees`, `steps_completed` and
`review_trail` are lists; `financials_source` is `framework-computed` or `analyst-supplied`. A null value is "not
recorded". The messages and fixes are in [Troubleshooting](troubleshooting.md#state-errors).

### Saved sources

`sources/manifest.json` is a list with one entry per saved document: `filename`, `url` (if it came from the web),
`step`, `claim` (what it backs) and `fetched_date`. The document itself sits beside it. A step that declares
citations but saved nothing is caught by `source_manifest.py --check-sources`.

### Persisted conventions and learnings

Three small stores keep analyst-confirmed knowledge between deals, each written only on an explicit yes, always
disclosed where it is applied, and none ever created by the headless pipeline:

| Store | Scope | Written by | Used by |
| :--- | :--- | :--- | :--- |
| `deals/<Company>/_conventions.json`, `config/spreading_conventions.json` | one borrower, or every borrower | `/spread` (via `conventions.py`) | `/spread` surfaces it; the CAM caveat quotes the note |
| `config/credit_policy_notes.md` | the fork | `/review` | both agents; mandatory for the Risk Reviewer |
| `deals/<Company>/_learnings.md`, `config/deal_learnings.md` | one borrower, or every borrower | `/assemble`, `/research` | the Underwriter, advisory only |

The convention file looks like the example in [Workflows](workflows.md#spreading-the-financial-figures). These are
plain local files, not a trained model.

## Module reference

*The sections from here on are the module-level reference, moved from `CLAUDE.md` ([ledger](move-ledger.md)).*

## State manager and state.json

**`scripts/state_manager.py`** — no `anthropic` dependency, same testability pattern as
`template_resolver.py`. `state_path(company, proposal, date_str=None, base_dir=None)` resolves
`deals/<Company>/<Proposal>_<Date>/state.json`; when `date_str` isn't given, it auto-discovers
an existing dated folder for that company/proposal (most recent wins) instead of defaulting to
today, so a deal resumed on a later calendar day still finds its original file. (This is
deliberately different from the bare `deal_export.export_deal()` default, which is still
today -- so callers resolve the date themselves via `resolve_date_str()`, see that script's entry in the CLI reference and issue #97.) `read_state(company, proposal, keys=())` returns the parsed dict or `None`; it
raises `StateShapeError` for a corrupt or non-UTF-8 file, a top level that is not an object, or (when `keys`
names them) a wrongly-typed key -- see "Malformed state fails clearly" below.
`write_state(company, proposal, **fields)` shallow-merges `fields` into the existing state (if
any), always keeps `company`/`proposal`/`date` in sync, creates the deal directory if needed
(reusing an existing one per the auto-discovery above), and returns the full merged state.
**`write_state()` and `append_review_trail()` never downgrade a state (issue #170).** Before touching anything, they
compare the file's recorded `schema_version` with this code's `SCHEMA_VERSION`,
numerically per `MAJOR.MINOR.PATCH` part (`1.10.0` is newer than `1.9.0`; `parse_schema_version()`). Equal,
older, and absent (read back as `LEGACY_SCHEMA_VERSION`, `0.0.0`) are written and upgraded to `SCHEMA_VERSION`
as before, unknown fields preserved. A **newer** version raises `SchemaVersionError` (a `ValueError`) naming
both versions and the file, and a **malformed** one (not a string, or not exactly `MAJOR.MINOR.PATCH` digits:
`null`, `1.2`, `v1.1.0`, ...) raises it too, since it cannot be shown to be older -- in both cases the file is
left byte-for-byte unchanged and no temp file is left behind. Reading such a file is unaffected. The fix for a
newer file is to update this checkout of the framework, never to edit the version by hand. **Limits:** the
guard covers only those two functions, i.e. `spreading_check.py` (the `/spread` and `/project` Bash steps) and
`orchestrator.py`; the slash commands that record their step by writing `state.json` directly with Claude's
file tools (`/triage`, `/research`, `/commercial`, `/collateral`, `/review`, `/assemble`) bypass it. Every CLI
shows the refusal as one `error:` line and exit status 1 (see the next paragraph; `orchestrator.py` refuses at its
first `write_state`, before any model call).
A caller that passes `schema_version=` explicitly still overrides the stamp (unchanged behaviour).
**Malformed state fails clearly (issue #171).** `StateError` (a `ValueError`) is the base for "this state.json's
content cannot be used": `SchemaVersionError` (above) and `StateShapeError` (corrupt JSON, a top level that is not
an object, or a key of the wrong type). `validate_state(state, keys=None, path=...)` checks the container types
in `STATE_SHAPES` -- `financials`, `ratios`, `multi_period_financials`, `analyst_supplied_financials` are objects
keyed by period whose values are objects; `inputs`, `stress_assumptions` are objects; `downside_case` is an object
whose optional `financials`/`ratios` are period objects; `collateral`, `covenants`, `security_package`,
`guarantees`, `steps_completed`, `review_trail` are lists (their elements are not inspected);
`financials_source` is `"framework-computed"` or `"analyst-supplied"` -- and raises one `StateShapeError` naming
every offending key, its required type and what was found, and the file. **Each consumer validates the keys it
reads**, via `read_state(..., keys=...)`: `spreading_check.STATE_KEYS_READ`, `policy_check.STATE_KEYS_READ`,
`deal_export.STATE_KEYS_READ`, `orchestrator.STATE_KEYS_READ`, `--check-steps` (`steps_completed`),
`append_review_trail` (`review_trail`); a deal is never rejected for a key that step does not use, and unknown
keys are never inspected (a newer framework's fields survive). The top level is checked on every read. **Deliberate
tolerances, kept:** a null value is "not recorded" for every key (a null period is empty); `deal_export` treats a
non-object period inside `financials`/`analyst_supplied_financials` as empty, a non-list `collateral` as no
collateral (one blank row) and a non-object `downside_case` as no downside case, as it always has, and so
validates only that those two raw-figure keys are objects, that an object `downside_case`'s inner
`financials`/`ratios` are objects, and that `financials_source` is a known value (it decides which raw store is
read). An *empty* value of the wrong container type (`financials: []`, `covenants: {}`) is rejected like any
other: earlier code silently read it as empty. `export_deal()` reads and validates state before it creates the
output folder or auto-saves a template, so a refusal leaves nothing behind; for a bare call with no `date_str`
this also means a deal whose state sits in an earlier dated folder is now found (before, the freshly created
empty folder was discovered first and the workbook came out blank); the CLI and the orchestrator always pass
the date, so they are unaffected. Every CLI that reads state (`spreading_check`,
`policy_check`, `deal_export`, `orchestrator`, `state_manager --check-steps`, `source_manifest --check-sources`)
catches `StateError` and prints one `error: ...` line to stderr with exit status 1, never a traceback; the "always
exits 0" of the two `--check-*` modes is about their *results* (a missing step or source is not an error), not
about a state.json they cannot read.

## Source manifest

**`scripts/source_manifest.py`** — no `anthropic` dependency, same testability pattern as
`state_manager.py` (reuses its date-resolution/locking/sanitization directly rather than
duplicating it). `save_source(company, proposal, *, step, claim, source_path, url=None,
filename=None)` copies an already-downloaded/saved file into
`deals/<Company>/<Proposal>_<Date>/sources/` and appends an entry to `sources/manifest.json`;
`read_manifest(company, proposal)` returns that manifest as a list (`[]` if nothing's been
saved yet). See "Source material persistence" in `CLAUDE.md`. Callable from a slash command's Bash step:
```
python scripts/source_manifest.py --company "Acme Corp" --proposal "Fleet Loan" --step triage \
    --claim "Legal identity and PSC filing" --file /path/to/downloaded.pdf --url https://...
```
`missing_saved_sources(state, manifest)` is the code-enforced check `/triage`, `/research`, and
`/commercial` each run at the end of their own "State: write" step (see issue #72): true when
`state`'s `triage`/`commercial` sections declare at least one citation but the manifest has
zero entries at all -- i.e. the step cited sources without ever actually saving any of the
material behind them, the exact gap that motivated this whole module. Deliberately deal-level
(not matched against an exact `triage`/`commercial`/`research` step-name tag -- `/research`'s
own combined step tags its entries `"research"`) and floor-level (at least one saved source,
not an exhaustive 1:1 match to every citation -- some sources are legitimately unsaveable, e.g.
a bot-blocked page). Wired into the same three commands via a `--check-sources` CLI mode
(mirrors `state_manager.py`'s own `--check-steps`: prints JSON and exits 0 whatever the result -- a
state.json it cannot read is an error, exit 1 -- and the calling command's prose decides what to do
with a `true` result).

## Persisted-convention stores

**`scripts/conventions.py`** — see "Persisted conventions" in `CLAUDE.md` for its full behavior; briefly,
`read_company_convention`/`write_company_convention`/`read_enterprise_convention`/
`write_enterprise_convention` back the borrower-specific and enterprise-wide spreading-convention
stores, callable from `/spread`'s own Bash steps (`--company`/`--enterprise` × `--read`/`--write`).
