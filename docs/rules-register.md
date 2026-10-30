# Rules register

The code is authoritative; this page describes the behavioural rules the deterministic policy engine and its checks
must preserve. That is the same boundary as [D18](decisions.md#d18-code-defines-the-register-describes): the
register does not define behaviour and must not be used to justify a change that the code and its source do not
support. The scheme was approved on [issue #496](https://github.com/ShamikM88/open-cam-framework/issues/496);
that issue is the decision record (D33 once its decision PR merges).

## Page conventions

- **One claim per rule.** Each Claim is one independently reviewable obligation, in one short sentence. Source
  points to the sentence or decision that states it; the Claim describes that obligation without copying the
  source paragraph. The implementation remains authoritative.
- **Stable IDs.** IDs use `R-<AREA>-NNN`, with three digits and a separate sequence per area. The agreed areas are
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
  [issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500); this PR adds no such lines.

## Status forms

Every rule has exactly one of these statuses:

- **Covered** — at least one test docstring cites the ID, and the test-to-claim check has verified that the cited
  assertion covers the whole Claim. Tracking is `—`.
- **Not covered** — the whole Claim has not yet been verified by a citing test. Tracking links the issue responsible
  for the gap or the temporary verification work. A Not covered row does not mean the implementation is absent.
- **Withdrawn** — the obligation has been retired or split. Tracking records why and, for a split, the successor
  IDs. Withdrawn IDs remain in this page and cannot be reused.

The active rules in this first register are initially Not covered with [issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500)
as the temporary citation and verification tracker. Candidate tests were inspected while preparing this page, but
a test name or a static reading is not a test citation and does not make a rule Covered. When #500 completes the
claim-to-test check, any actual gap is to receive its own issue.

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

## Policy engine and checks (POL)

### R-POL-001 — An N/A ratio cannot pass or fail a covenant

- **Claim:** A covenant on an N/A ratio is UNRESOLVABLE for both minimum and maximum covenants.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-002 — Current-year FAIL or UNRESOLVABLE rejects the draft

- **Claim:** A current-year covenant FAIL or UNRESOLVABLE is a code-enforced draft rejection.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Withdrawn
- **Tracking:** Split because the named test verified only the UNRESOLVABLE case: successor rules `R-POL-013` (FAIL) and `R-POL-014` (UNRESOLVABLE).

### R-POL-003 — Forward-year base-case results are disclosure only

- **Claim:** A forward-year covenant FAIL or UNRESOLVABLE is recorded but does not by itself reject the draft.
- **Source:** [D6 — forward-year covenant results are disclosure only](decisions.md#d6-forward-year-covenant-results-are-disclosure-only).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_forward_covenants`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-004 — A downside breach requires a passing base case

- **Claim:** A downside breach is recorded only when its base-case covenant passes and its stressed result is FAIL or UNRESOLVABLE.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_downside_covenants`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-005 — A downside breach must be acknowledged by its own ID

- **Claim:** Each downside breach must be acknowledged by its own `breach_id`, otherwise the draft is non-compliant.
- **Source:** [The downside case](financial-model.md#the-downside-case).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Withdrawn
- **Tracking:** Split into `R-POL-015` (an unacknowledged breach produces a rejection reason) and `R-POL-016` (acknowledgement is specific to each breach ID).

### R-POL-006 — A covenant must match the recorded financial basis

- **Claim:** A covenant whose basis differs from the recorded financial view is UNRESOLVABLE.
- **Source:** [D19 — group accounts and covenant basis](decisions.md#d19-group-accounts-case-a-first-and-a-credit-aggregation-is-never-called-a-consolidation).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-007 — An unknown covenant metric is UNRESOLVABLE

- **Claim:** A covenant metric absent from the computed ratios is UNRESOLVABLE with a reason.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Withdrawn
- **Tracking:** Split into `R-POL-017` (unknown metric), `R-POL-018` (unknown type) and `R-POL-019` (missing or non-numeric threshold) so each invalidity has its own rule.

### R-POL-008 — Equality to a covenant threshold passes

- **Claim:** An actual equal to its threshold passes for both minimum and maximum covenants.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-009 — A zero threshold has no percentage headroom

- **Claim:** A zero covenant threshold has no percentage headroom, but PASS or FAIL is still decided by direct comparison.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-010 — Percent-type ratios reject likely unit mismatches

- **Claim:** A percent-type covenant is UNRESOLVABLE when its recorded actual or threshold has an absolute value greater than 10.
- **Source:** [Percent-type covenant unit guard](https://github.com/ShamikM88/open-cam-framework/blob/main/docs/financial-model.md#L59-L64).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-011 — Non-positive aggregate exposure makes collateral cover N/A

- **Claim:** Aggregate collateral cover is N/A when total exposure is zero or negative.
- **Source:** [Collateral cover percentage](https://github.com/ShamikM88/open-cam-framework/blob/main/docs/financial-model.md#L119-L124).
- **Implemented in:** `scripts/policy_checks.py::compute_collateral_cover_pct`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-012 — An existing rejection cannot be changed to approval

- **Claim:** The deterministic policy check never changes an existing REJECTED verdict to APPROVED.
- **Source:** [Execution scripts](../CLAUDE.md#execution-scripts).
- **Implemented in:** `scripts/orchestrator.py::_apply_deterministic_policy_checks`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-013 — A current-year FAIL rejects the draft

- **Claim:** The deterministic policy overlay returns REJECTED when a current-year covenant has status FAIL.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/orchestrator.py::_apply_deterministic_policy_checks`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-014 — A current-year UNRESOLVABLE result rejects the draft

- **Claim:** The deterministic policy overlay returns REJECTED when a current-year covenant has status UNRESOLVABLE.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/orchestrator.py::_apply_deterministic_policy_checks`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-015 — An unacknowledged downside breach is a rejection reason

- **Claim:** A downside breach without a matching acknowledgement produces an undisclosed-breach rejection reason.
- **Source:** [Covenants](financial-model.md#covenants).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-016 — One breach acknowledgement does not cover another

- **Claim:** Acknowledging one downside `breach_id` does not acknowledge a second breach with a different ID.
- **Source:** [The downside case](financial-model.md#the-downside-case).
- **Implemented in:** `scripts/policy_checks.py::check_draft_compliance`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-017 — An unknown covenant metric is not silently skipped

- **Claim:** An unrecognised covenant metric remains in the results as UNRESOLVABLE.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-018 — An unknown covenant type is UNRESOLVABLE

- **Claim:** A covenant type other than minimum or maximum is UNRESOLVABLE with a reason.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).

### R-POL-019 — A missing or non-numeric threshold is UNRESOLVABLE

- **Claim:** A missing or non-numeric covenant threshold is UNRESOLVABLE with a reason.
- **Source:** [N/A and UNRESOLVABLE](financial-model.md#na-and-unresolvable).
- **Implemented in:** `scripts/policy_engine.py::_evaluate_covenant`
- **Status:** Not covered
- **Tracking:** [Issue #500](https://github.com/ShamikM88/open-cam-framework/issues/500) (temporary citation and verification tracker).
