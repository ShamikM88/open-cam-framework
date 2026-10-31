# Rules register

The code is authoritative; this page describes the behavioural rules the deterministic policy engine and its checks
must preserve. That is the same boundary as [D18](decisions.md#d18-code-defines-the-register-describes): the
register does not define behaviour and must not be used to justify a change that the code and its source do not
support. The scheme was approved on [issue #495](https://github.com/ShamikM88/open-cam-framework/issues/495) (the
design note
and the maintainer's sign-off with amendments A1 to A4); decision D33 ([issue
#496](https://github.com/ShamikM88/open-cam-framework/issues/496))
records it.

## Page conventions

- **One claim per rule.** Each Claim is one independently reviewable obligation, in one short sentence. Source
  points to the sentence or decision that states it; the Claim describes that obligation without copying the
  source paragraph. The implementation remains authoritative.
- **Stable IDs.** IDs use `R-<AREA>-NNN` , with three digits and a separate sequence per area, numbered consecutively
  from 001. The agreed areas are
  `POL` (policy engine and draft checks), `STATE` (state persistence and validation), `SPREAD` (financial
  calculations and spreading), and `EXPORT` (raw-value selection and rendered output). An ID belongs to the
  obligation, not its Python location. IDs are never reused or renumbered.
- **Splitting and withdrawal.** If a compound obligation is split, retain its old ID as Withdrawn, state the reason
  and list the successor IDs in Tracking, then allocate new IDs. A withdrawn entry is never deleted or reused.
  Merging obligations keeps one surviving ID and withdraws the others with the successor recorded.
- **IDs ever issued.** The list below is an append-only human-reviewed history. Every entry ID appears there once,
  and every listed ID has exactly one entry. The gate can detect inconsistencies within the current page; it cannot
  prove that a coordinated edit did not remove both an old entry and its history item. Reviewers must therefore
  treat this list as append-only.
- **No test inventory here.** There is no Citing tests field. The gate derives citations from literal `Rule: R-...`
  lines in pytest test-function docstrings. The citations are added and verified in the separate work tracked by
  [issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500); until #500, no test carries a citation.

## Status forms

Every rule has exactly one of these statuses:

- **Covered** — at least one test docstring cites the ID, and the test-to-claim check has verified that the cited
  assertion covers the whole Claim. Tracking is `—`.
- **Not covered** — the whole Claim has not yet been verified by a citing test. Tracking links the issue responsible
  for the gap or the temporary verification work. A Not covered row does not mean the implementation is absent.
- **Withdrawn** — the obligation has been retired or split. Tracking records why and, for a split, the successor
  IDs. Withdrawn IDs remain in this page and cannot be reused.

The active rules in this register are initially Not covered with
[issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500)
as the temporary citation and verification tracker. Candidate tests were inspected while preparing this page, but
a test name or a static reading is not a test citation and does not make a rule Covered. When #500 completes the
claim-to-test check, any actual gap is to receive its own issue.

**Inventory at this revision:** 35 IDs ever issued; 32 active Not covered; 3 Withdrawn; 0 Covered.

## The `Implemented in` field

The field has one of three forms, using repository-relative paths:

- `<file>` names a file only. Use this when responsibility sits in that module but no single function
  is the right reference.
- `<file>::<function>` names a module-level function. A method is written
  `<file>::<Class>.<method>`.
- `none` states that no implementation exists.

The #499 gate will check that a file path exists and, when a function or method suffix is present, that the named
definition exists in that file (by source inspection, without importing the module). A file-only reference is checked
for the file, not an invented function. `none` makes no file/function existence claim and is used only for a
Not covered rule with a Tracking issue. Where several functions implement a rule within one module and no single
function is the owner, use the file-only form.

## What the citation gate cannot establish

A citation declares which rule a test is responsible for; it is not proof that the test is strong or that its
assertions cover the Claim. The static gate cannot establish that a cited test executes: a test marked skipped is
still present in source, a test can be deselected at run time, and tests generated dynamically in a loop are not
reliably visible as ordinary test definitions to an AST scan. It also does not judge assertion quality, fixtures,
or whether the chosen test genuinely proves the behaviour. Those remain matters for source inspection and review.
A green gate therefore means the register and citations are structurally linked, not that every claim has been
independently proven by the gate.

## IDs ever issued

- `R-POL-001`
- `R-POL-002`
- `R-POL-003`
- `R-POL-004`
- `R-POL-005`
- `R-POL-006`
- `R-POL-007`
- `R-POL-008`
- `R-POL-009`
- `R-POL-010`
- `R-POL-011`
- `R-POL-012`
- `R-POL-013`
- `R-POL-014`
- `R-POL-015`
- `R-POL-016`
- `R-POL-017`
- `R-POL-018`
- `R-POL-019`
- `R-POL-020`
- `R-STATE-001`
- `R-STATE-002`
- `R-STATE-003`
- `R-STATE-004`
- `R-SPREAD-001`
- `R-SPREAD-002`
- `R-SPREAD-003`
- `R-SPREAD-004`
- `R-SPREAD-005`
- `R-EXPORT-001`
- `R-EXPORT-002`
- `R-EXPORT-003`
- `R-EXPORT-004`
- `R-EXPORT-005`
- `R-EXPORT-006`

## Policy engine and checks (POL)

### R-POL-001 — An N/A ratio cannot pass or fail a covenant

- **Claim:** A covenant on an N/A ratio is UNRESOLVABLE for both minimum and maximum covenants.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-002 — Current-year FAIL or UNRESOLVABLE rejects the draft

- **Claim:** A current-year covenant FAIL or UNRESOLVABLE is a code-enforced draft rejection.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Withdrawn
- **Tracking:** Split because the named test verified only the UNRESOLVABLE case: successor rules `R-POL-013` (FAIL)
  and `R-POL-014` (UNRESOLVABLE).

### R-POL-003 — Forward-year base-case results are disclosure only

- **Claim:** A forward-year base-case covenant FAIL or UNRESOLVABLE does not itself produce a code-enforced draft
  rejection.
- **Source:** [D6](decisions.md#d6-forward-year-covenant-results-are-disclosure-only).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-004 — A downside breach requires a passing base case

- **Claim:** A downside breach is recorded only when its base-case covenant passes and its stressed result is FAIL or
  UNRESOLVABLE.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_downside_covenants`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-005 — A downside breach must be acknowledged by its own ID

- **Claim:** Each downside breach must be acknowledged by its own `breach_id`, otherwise the draft is non-compliant.
- **Source:** [The downside case](financial-model.md#the-downside-case).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Withdrawn
- **Tracking:** Split into `R-POL-015` (an unacknowledged breach produces a rejection reason) and `R-POL-016`
  (acknowledgement is specific to each breach ID).

### R-POL-006 — A covenant must match the recorded financial basis

- **Claim:** A covenant whose basis differs from the recorded financial view is UNRESOLVABLE.
- **Source:**
  [D19](decisions.md#d19-group-accounts-case-a-first-and-a-credit-aggregation-is-never-called-a-consolidation) .
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-007 — An invalid covenant metric, type or threshold is UNRESOLVABLE

- **Claim:** An unknown metric/type or missing/non-numeric threshold yields UNRESOLVABLE with a reason, not a crash
  or silent skip.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Withdrawn
- **Tracking:** Split into `R-POL-017` (unknown metric), `R-POL-018` (unknown type) and `R-POL-019` (missing or
  non-numeric threshold) so each invalidity has its own rule.

### R-POL-008 — Equality to a covenant threshold passes

- **Claim:** An actual equal to its threshold passes for both minimum and maximum covenants.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-009 — A zero threshold has no percentage headroom

- **Claim:** A zero covenant threshold has no percentage headroom, but PASS or FAIL is still decided by direct
  comparison.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-010 — Percent-type ratios reject likely unit mismatches

- **Claim:** A percent-type covenant is UNRESOLVABLE when its recorded actual or threshold has an absolute value
  greater than 10.
- **Source:**
  [Percent-type ratios are stored as fractions](financial-model.md#percent-type-ratios-are-stored-as-fractions) .
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-011 — Non-positive aggregate exposure makes collateral cover N/A

- **Claim:** Aggregate collateral cover is N/A when total exposure is zero or negative.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable) (the “Collateral cover percentage”
  paragraph).
- **Implemented in:** `scripts/policy_checks.py::compute_collateral_cover_pct`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-012 — An existing rejection cannot be changed to approval

- **Claim:** The deterministic policy check never changes an existing REJECTED verdict to APPROVED.
- **Source:** [Execution scripts](../CLAUDE.md#execution-scripts).
- **Implemented in:** `scripts/orchestrator.py::_apply_deterministic_policy_checks`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-013 — A current-year FAIL rejects the draft

- **Claim:** A current-year covenant FAIL produces a code-enforced rejection reason.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-014 — A current-year UNRESOLVABLE result rejects the draft

- **Claim:** A current-year covenant UNRESOLVABLE produces a code-enforced rejection reason.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-015 — An unacknowledged downside breach is a rejection reason

- **Claim:** A downside breach without a matching acknowledgement produces an undisclosed-breach rejection reason.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-016 — One breach acknowledgement does not cover another

- **Claim:** Acknowledging one downside `breach_id` does not acknowledge a second breach with a different ID.
- **Source:** [The downside case](financial-model.md#the-downside-case).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-017 — An unknown covenant metric is not silently skipped

- **Claim:** An unrecognised covenant metric remains in the results as UNRESOLVABLE.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-018 — An unknown covenant type is UNRESOLVABLE

- **Claim:** A covenant type other than minimum or maximum is UNRESOLVABLE with a reason.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

### R-POL-019 — A missing or non-numeric threshold is UNRESOLVABLE

- **Claim:** A missing or non-numeric covenant threshold is UNRESOLVABLE with a reason.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).


### R-POL-020 — Negative or non-finite asset inputs make collateral cover N/A

- **Claim:** Aggregate collateral cover is N/A when any asset has a negative or non-finite exposure or collateral
  value.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable) (the “Collateral cover percentage”
  paragraph).
- **Implemented in:** `scripts/policy_checks.py::compute_collateral_cover_pct`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and
  verification tracker).

## State persistence and validation (STATE)

### R-STATE-001 — write_state refuses a newer schema version

- **Claim:** `write_state()` refuses to overwrite a state whose `schema_version` is newer than the framework's supported version.
- **Source:** [State manager and state.json](data-model.md#state-manager-and-statejson).
- **Implemented in:** `scripts/state_manager.py::write_state`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-STATE-002 — append_review_trail refuses a newer schema version

- **Claim:** `append_review_trail()` refuses to overwrite a state whose `schema_version` is newer than the framework's supported version.
- **Source:** [State manager and state.json](data-model.md#state-manager-and-statejson).
- **Implemented in:** `scripts/state_manager.py::append_review_trail`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-STATE-003 — write_state refuses a malformed schema version

- **Claim:** `write_state()` refuses to overwrite a state whose `schema_version` is not a valid `MAJOR.MINOR.PATCH` string.
- **Source:** [State manager and state.json](data-model.md#state-manager-and-statejson).
- **Implemented in:** `scripts/state_manager.py::write_state`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-STATE-004 — append_review_trail refuses a malformed schema version

- **Claim:** `append_review_trail()` refuses to overwrite a state whose `schema_version` is not a valid `MAJOR.MINOR.PATCH` string.
- **Source:** [State manager and state.json](data-model.md#state-manager-and-statejson).
- **Implemented in:** `scripts/state_manager.py::append_review_trail`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

## Spreading and financial calculations (SPREAD)

### R-SPREAD-001 — framework-computed figures derive from raw line items

- **Claim:** Framework-computed spreading derives subtotals and ratios from the period's raw line items.
- **Source:** [Context Window & State Management Protocol](../CLAUDE.md#context-window--state-management-protocol).
- **Implemented in:** `scripts/spreading_builder.py::evaluate_financial_model`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-SPREAD-002 — adding a period preserves previously recorded periods

- **Claim:** A spreading update for a new period preserves the other periods already recorded in the deal.
- **Source:** [Execution scripts](../CLAUDE.md#execution-scripts).
- **Implemented in:** `scripts/spreading_check.py::plan_fields`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-SPREAD-003 — a partial period correction merges raw fields

- **Claim:** A partial correction to a recorded period merges the supplied raw fields with that period's existing raw fields.
- **Source:** [Execution scripts](../CLAUDE.md#execution-scripts).
- **Implemented in:** `scripts/spreading_check.py::plan_fields`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-SPREAD-004 — /project does not relabel analyst-supplied history

- **Claim:** A forward-only `/project` update does not change an analyst-supplied deal's `financials_source` to `framework-computed`.
- **Source:** [Execution scripts](../CLAUDE.md#execution-scripts).
- **Implemented in:** `scripts/spreading_check.py::compute`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-SPREAD-005 — an analyst-supplied forecast is not recomputed

- **Claim:** A forward forecast marked analyst-supplied is recorded without recomputing its supplied figures through the framework formulas.
- **Source:** [state.json at a glance](data-model.md#statejson-at-a-glance).
- **Implemented in:** `scripts/supplied_forecast.py::record`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

## Export and rendered output (EXPORT)

### R-EXPORT-001 — historical raw figures follow financials_source

- **Claim:** For historical periods, the workbook exporter selects raw figures from `analyst_supplied_financials` when `financials_source` is `analyst-supplied`, and from `financials[period]["raw"]` otherwise.
- **Source:** [Two raw-figure stores, never blended within a period](data-model.md#two-raw-figure-stores-never-blended-within-a-period).
- **Implemented in:** `scripts/deal_export.py::_financial_data_from_state`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-EXPORT-002 — forward raw figures follow forecast_source

- **Claim:** For forward periods, the workbook exporter selects raw figures from `analyst_supplied_financials` when `forecast_source` is `analyst-supplied`, and from `financials[period]["raw"]` otherwise.
- **Source:** [Two raw-figure stores, never blended within a period](data-model.md#two-raw-figure-stores-never-blended-within-a-period).
- **Implemented in:** `scripts/deal_export.py::_financial_data_from_state`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-EXPORT-003 — a missing selected store is not replaced by the other store

- **Claim:** The workbook exporter does not fall back to the other raw-figure store when the selected store has no record for a period.
- **Source:** [Two raw-figure stores, never blended within a period](data-model.md#two-raw-figure-stores-never-blended-within-a-period).
- **Implemented in:** `scripts/deal_export.py::_financial_data_from_state`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-EXPORT-004 — raw stores are not blended within a period

- **Claim:** The workbook exporter never combines raw fields from both raw-figure stores within one period.
- **Source:** [Two raw-figure stores, never blended within a period](data-model.md#two-raw-figure-stores-never-blended-within-a-period).
- **Implemented in:** `scripts/deal_export.py::_financial_data_from_state`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-EXPORT-005 — export validates state before creating output

- **Claim:** `export_deal()` reads and validates deal state before creating the output folder or auto-saving a template.
- **Source:** [State manager and state.json](data-model.md#state-manager-and-statejson).
- **Implemented in:** `scripts/deal_export.py::export_deal`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-EXPORT-006 — JSON-tagged fences are omitted from DOCX output

- **Claim:** `docx_builder` omits fenced code blocks tagged `json` from the exported DOCX.
- **Source:** [Context Window & State Management Protocol](../CLAUDE.md#context-window--state-management-protocol).
- **Implemented in:** `scripts/docx_builder.py::export_to_docx`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

