# Data model

> **Status.** This page was assembled in the documentation tranche of issue #117 from material that used to live in `CLAUDE.md`, moved with only the textual fixes recorded in the [move ledger](move-ledger.md). A later tranche adds worked examples and explanation around it. The source of truth for behaviour is the code and tests; see the [index](README.md).

How a deal's state is stored and guarded. The normative protocol (checkpoint after every step, re-hydrate first, no in-memory-only state) and the state-file schema example stay in `CLAUDE.md`, under "Context Window & State Management Protocol", "Source material persistence" and "Persisted conventions"; this page holds the script-level reference for the modules that implement them.

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
