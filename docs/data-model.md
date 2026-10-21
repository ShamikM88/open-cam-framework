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
| `triage`, `commercial` | `/triage`, `/commercial`, `/research` | The Go/No-Go verdict and rationale; the company and sector sections. Each section's `sources` field is a list of citation strings, including `[]` when no citations were used |
| `financials`, `ratios`, `multi_period_financials`, `financials_source` | `spreading_check.py` for computed periods; `supplied_forecast.py` for analyst-supplied forward values | Computed per-period subtotals/ratios with raw inputs under `financials[period]["raw"]`; `multi_period_financials` records computed raw input lines, while `financials_source` remains the deal-wide historical-basis label |
| `analyst_supplied_financials`, `financials_source_note` | `/spread`, analyst-supplied mode; `supplied_forecast.py` for forward raw breakdowns when provided | The analyst's raw breakdown (if given); the convention note the CAM caveat quotes (for figures read from an image, with a sentence saying so) |
| `financials_transcriptions` | `transcription_check.py --commit` (`/spread`, figures read from an image) | A list with one record per confirmed transcription: the kind of image, the mode, the saved source file and its SHA-256 fingerprint (never the image), the unit, the declared sign conventions, the periods, the confirmed digest and date, and the cross-foot outcome (matched, acknowledged with the analyst's reason, not assessed). Absent for figures read from text. One deal holds only one basis: a transcription in the other `/spread` mode is refused |
| `stress_assumptions`, `downside_case` | `spreading_check.py` (run by `/project`) | The three stress shocks; the stressed forward-year figures and ratios |
| `forecast_source` | `supplied_forecast.py` (`/project`, analyst-supplied mode) | `"analyst-supplied"` when the forward years were supplied by the analyst and are recorded as given; absent means framework-computed (every deal that predates the mode). Such a deal also has `financials_source: "analyst-supplied"`, and its `downside_case` carries `basis`, `unavailable` and `description` besides `financials` and `ratios` (one `description` for all of its analyst-supplied years) |
| `collateral`, `security_package` | `/collateral` | One entry per asset; one per charge |
| `covenants`, `guarantees` | `/project` | `{"metric", "type", "threshold"}` entries; `{"provider", "type", "amount"}` entries |
| `policy_state` | `/assemble`, the orchestrator | The computed conditions, covenant results, security gaps, downside breaches and forward covenant results, stored whole |
| `review_trail`, `review_verdict` | `/review`, the orchestrator's audit | The append-only history of verdicts and notes; the latest verdict |
| `deal_structure_notes.review_cap` | `/research` review checkpoint, when the analyst sets a lower maximum | Optional lower per-deal REJECTED-pass threshold; it takes precedence over the default third-pass checkpoint and is recorded when set. This is an optional nested deal note, not a schema-version bump |
| `review_stopped_by_analyst` | `research_export.py --analyst-release-unapproved` (`/research`, after explicit analyst choice) | An append-only list of `{"date": "YYYY-MM-DD", "passes": N, "reason": "..."}` records, one per release: the date, the trailing consecutive REJECTED review count and the analyst's reason for releasing the brief for manual review. No record is cleared or overwritten, and a later approved export leaves the list as it is; `review_verdict` carries the current verdict |
| `analyst_terms` | `/research` and `/assemble`, when the analyst defines a term | A list of `{term, meaning, date}` records checkpointed immediately so the definition is not lost if the conversation is compacted; entries become learning candidates only on explicit confirmation |
| `draft_path`, `model_provenance` | `/assemble`; the orchestrator | The exported `.docx` path; which models and which prompt versions (content hashes) drafted and audited this deal |
| `approved_draft_path` | `/assemble` | Repository-relative path to the approved Markdown draft retained as `draft_approved.md` in the same dated deal folder |

`financials_source` is a deal-wide label and audit caveat, not the exporter's routing key for every
period. When it is `"analyst-supplied"`, framework-computed mode refuses historical-period recomputation and
`/project` leaves the label unchanged. The one allowed exception to the normal one-raw-store-per-deal rule is
an interactive, forward-only `/project` call: its forward-year raw lines are framework-computed, recorded under
`financials[period]["raw"]` and exported from that store, while the historical lines remain in
`analyst_supplied_financials`. No historical period is recomputed, `financials_source` stays
`"analyst-supplied"`, and the two stores are never blended within a period. A forecast explicitly marked
analyst-supplied is not recomputed. The headless `orchestrator.py` does not get this exception; it refuses a
run that would recompute an analyst-supplied deal, even if the supplied input is forward-only.

Ratios that cannot be computed are `null`, never `0`. The units for `gearing`, `fcf_conversion_pct` and their
covenant thresholds follow the [percent-type ratio fraction convention](financial-model.md#percent-type-ratios-are-stored-as-fractions). A step may add keys of its own; this table is a
floor.

`analyst_terms` is an additive step-specific key, not a closed schema field. Its entries use the analyst's wording and a date; `state_manager.py` leaves unknown keys alone, so this addition does not require a `schema_version` bump. The commands still own the prompt-level rule to merge entries without dropping existing ones. The fresh Reviewer is not given `analyst_terms`; if a definition matters to the audit, write it plainly in the brief.

### Reviewer view files

For an interactive `/review`, `state_manager.py --export-reviewer-view` derives two files under
`deals/<Company>/<Proposal>_<Date>/review_view/`: `state_for_review.json` is a copy of the deal record without
`review_trail` and `review_verdict`, while `review_trail.json` contains the history list (or `[]` when absent).
Each successful export overwrites both derived copies. They are **not records**; `state.json` remains the source of
truth and is the file `/review` updates in its `State: write` stage. No other script may consume these view files as
a deal record. This removes the default path to earlier verdicts during the Reviewer's first pass, but it does not
prevent a determined Reviewer from globbing to the combined `state.json`; see [AI assurance](ai-assurance.md#the-interactive-checker-is-a-separate-context-204).

### Two raw-figure stores, never blended within a period

Raw figures have two separate stores. The correct source is selected for each period; having both
stores in the same deal is permitted when history was analyst-supplied and `/project` later computed
forward years.

| Period | Raw figures the exporter reads | Written by |
| :--- | :--- | :--- |
| Historical period when `financials_source` is `"framework-computed"` | `financials[period]["raw"]` (the raw lines are also tracked in `multi_period_financials`) | `spreading_check.py` |
| Historical period when `financials_source` is `"analyst-supplied"` | `analyst_supplied_financials[period]` | `/spread`, analyst-supplied mode |
| Forward period when `forecast_source` is `"analyst-supplied"` | `analyst_supplied_financials[period]`, when a raw breakdown was supplied; recorded subtotals/ratios remain in `financials`/`ratios` as given | `supplied_forecast.py` (`/project`) |
| Forward period when the forecast is framework-computed (`forecast_source` absent or not `"analyst-supplied"`) | `financials[period]["raw"]`, including when historical `financials_source` remains `"analyst-supplied"` | `spreading_check.py` (`/project`) |

The workbook exporter, `scripts/deal_export.py`, follows these per-period rules. It never assumes a single
store is universal, never falls back to the other store when a period's selected store is missing, and
never blends raw fields from both stores within one period. An analyst-supplied period can have no raw
breakdown at all; its recorded subtotals and ratios are still preserved rather than reconstructed from
incomplete raw figures.
### Resuming a deal

A step begins by finding the deal's state (the most recent dated folder for that company and proposal; a bare library
call or a brand-new deal gets today's date) and treats everything recorded there as true. With
`orchestrator.py --new-review`, today's dated folder is created through the shared fresh-review state manager when it
does not already contain a `state.json`. That minimal state links to the nearest earlier state-bearing review for the
exact same company and proposal using `prior_review_date`, or starts unlinked when no eligible earlier review exists.
The link is only a date; prior inputs and results are never inherited. Later pipeline checkpoints merge into the state
and preserve `prior_review_date`. If today's dated folder already has a `state.json`, `--new-review` resumes it
unchanged as a rerun after failure: it does not create another state or change the existing link. A refusal (for
example, when today's date is not later than the newest state-bearing review) exits with one `error:` line before a
model call or write. The orchestrator checkpoints its current grounded figures and `policy_state` before it calls
either agent, so a failure at the first model call can still leave the current review's figures on disk. A run with no
credentials at all fails before any model call; if a fresh-review refusal applies, it is reported first.

### Starting a new review

Use `python scripts/state_manager.py --review-candidates` to inspect valid earlier state-bearing folders for the exact
company and proposal; the nearest earlier review is the default. `--start-review` creates a new date folder containing
only `company`, `proposal`, `date`, and the current `schema_version`, plus the optional scalar
`prior_review_date` when linked. This value is the exact `YYYY-MM-DD` of a previous review for the same company and
proposal. With no eligible prior, the new review is explicitly unlinked; `--no-prior` can request this even when
candidates exist, and `--prior-date` can select any eligible earlier candidate. The start refuses an existing dated
folder and, to keep the newest-folder resume rule reliable, refuses dates not strictly later than the newest existing
state-bearing folder.

A started review inherits **nothing** from its prior: no inputs, financials, ratios, policy state, completed steps,
review trail, covenants, downside, collateral, security package, facility terms, guarantees, drafts, or source manifest.
The prior review is linked by date only. Company-level conventions and learnings stay in their existing stores and are
neither copied into the new review nor modified by starting it. No schema-version bump is required for this optional key.

### Comparing two reviews

`scripts/review_compare.py` compares two exact dated `state.json` snapshots read-only. Both must identify the exact
same `company` and `proposal`; the explicit `--prior-date` must be earlier than `--current-date`, or the current
state must record that prior date in its optional `prior_review_date` field. The comparator does not search other
dated folders to guess a predecessor. It accepts legacy unversioned states and reads newer schema versions without
writing either file or normalizing persisted data.

The by-label comparison covers recorded financials, ratios, multi-period and analyst-supplied figures, forecast and
stress inputs, downside, the financial-source label and note, covenants and the recorded policy results, collateral,
security package, guarantees, and optional structured `ownership` / `facility_terms`. Missing optional keys mean
"not recorded", not zero. Changes are ordered deterministically. Decimal values are compared exactly; a `delta` is
reported only where both sides are numeric. Covenants and covenant results are keyed by metric, type, and
occurrence; forward covenant results use `forward_id`, downside breaches `breach_id`, conditions precedent/subsequent
`cp_id`/`cs_id`, collateral `asset_id`, and guarantees `guarantee_id` when every record has a non-blank explicit ID.
Lists without a reliable identifier (including security packages, security gaps, and guarantees without `guarantee_id`)
are compared as exact ordered values.
Changes in `financials_source` carry a
clear comparability warning. This output reports stored facts and transitions, not an explanation or credit verdict.

The opt-in `--periods-rolled-one-year` check is a separate section after the by-label result. It compares historical
periods in the prior review with their next-older labels in the current review, reports exact recorded values and
differences, marks pairs `consistent`, `restated`, or `not_comparable`, and never includes forward periods. The
flag records the analyst's confirmation; period roll-forward is never inferred.

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
`step`, `claim` (what it backs) and `fetched_date`. The document itself sits beside it. `triage.sources` and
`commercial.sources` are lists of citation strings (`list[str]`); use an empty list when a step used no citations rather
than omitting the key. Nulls, mappings, scalars and lists containing non-strings are invalid declarations; arbitrary
elements are never converted into citation strings. `steps_completed`, if present, must be a list of strings; an absent
field remains valid for new/incomplete historical states. Malformed consumed completion metadata, any invalid source
declaration, or a completed source-bearing step without a declaration reports `sources_declared: "unknown"` and
`missing_saved_sources: true`, even with a nonempty manifest.

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
`template_resolver.py`. Its mutually exclusive `--find` mode calls `find_deal(company, proposal)` and prints one JSON
object without creating files or directories. An exact match returns `record_found: true`, the resolved `state_path`
and newest date for the exact proposal's valid dated folder containing `state.json`; a corrupt-but-present state file
still counts as found so the regular reader can report the established corruption error. If no exact record exists,
the result has `record_found: false`, `company_folder_exists` and `near_misses`. The latter is advisory only and
contains only valid dated folders with `state.json` whose proposal names exactly match after case-folding and collapsing
runs of whitespace/hyphens. No fuzzy/prefix selection is performed. The `--check-steps` mode keeps its existing output;
`--export-reviewer-view` validates state and writes the history-free first-pass copy and separate history copy described above,
refusing invalid, newer-schema or malformed-schema state before writing. `state_path(company, proposal, date_str=None, base_dir=None)` resolves
`deals/<Company>/<Proposal>_<Date>/state.json`; when `date_str` isn't given, it auto-discovers
an existing dated folder for that company/proposal (most recent wins) instead of defaulting to
today, so a deal resumed on a later calendar day still finds its original file. An explicit
`date_str` supplied to `state_path()`, `read_state()`, `write_state()` or
`append_review_trail()` must be a real calendar date in exact `YYYY-MM-DD` form;
malformed or impossible values raise `StateError` before any deal folder is created.
Passing `date_str=None` retains the existing auto-discovery/today behavior. (This is
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
A read-only `state_manager.py --find` lookup is advisory. At the headless orchestrator's single state-resolution
point, when no exact record exists and normalized candidates are present, it prints one stderr warning naming the
candidate proposal/date and carries on with the existing resolution behavior. A missing record with no normalized near
match produces no warning; the lookup does not change `policy_check.py` or `source_manifest.py` JSON output.

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
`financials`/`ratios` are objects, and that `financials_source` is a known value (it routes historical raw-input
selection; forward-period workbook selection also follows `forecast_source`, as described above). An *empty* value of the wrong container type (`financials: []`, `covenants: {}`) is rejected like any
other: earlier code silently read it as empty. `export_deal()` reads and validates state before it creates the
output folder or auto-saves a template, so a refusal leaves nothing behind; for a bare call with no `date_str`
this also means a bare call still finds the deal's existing state before creating today's output folder.
When an explicit date is supplied, that same date selects the state read as well as the output folder; this is how
the CLI and orchestrator keep export figures tied to the selected review. Every CLI that reads state (`spreading_check`,
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
`sources_declared(state)` reports the declaration shape as `"declared"`, `"not_declared"`, or `"unknown"`.
A completed `triage` or `commercial` step without a `list[str]` declaration is `"unknown"`, as is any malformed declared
list or malformed consumed `steps_completed`; saved material cannot make that unknown shape valid. Absent completion
metadata alone is not invalid, and an explicit empty list is a valid declaration of no citations.
`missing_saved_sources(state, manifest)` remains a boolean check: it returns true when any declared non-empty citation
has no saved manifest material, or when the consumed declaration/completion shape is unknown.
It remains deal-level (not matched against an exact `triage`/`commercial`/`research`
manifest step tag — `/research` tags its entries `"research"`) and floor-level (at least one source saved, not an
exhaustive 1:1 match to every citation because some sources are legitimately unsaveable, e.g. a bot-blocked page).
The `--check-sources` CLI prints both `missing_saved_sources` and `sources_declared` and exits 0 for a check result;
a state or manifest it cannot read is still an error, exit 1. The calling command must not silently continue when
`sources_declared` is `"unknown"`.

## Persisted-convention stores

**`scripts/conventions.py`** — see "Persisted conventions" in `CLAUDE.md` for its full behavior; briefly,
`read_company_convention`/`write_company_convention`/`read_enterprise_convention`/
`write_enterprise_convention` back the borrower-specific and enterprise-wide spreading-convention
stores, callable from `/spread`'s own Bash steps (`--company`/`--enterprise` × `--read`/`--write`).
