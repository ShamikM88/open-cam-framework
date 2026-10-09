# OpenCAM MVP v1 — product requirements document (reconstructed)

## Product summary and status

OpenCAM pairs two AI agents - an Underwriter that drafts a Credit Assessment Memorandum (CAM) and a Risk Reviewer
that audits the draft - with deterministic code that computes every financial figure and policy outcome the
agents work from. MVP v1 shipped in September 2026; the product is early-stage, validated so far with one
corporate credit analyst.

This document reconstructs the product definition behind MVP v1 from the repository's own pull requests,
[design decisions](decisions.md), and the project's delivery history. It was written after MVP v1 shipped, from
that evidence, and is not presented as a document that existed in this form before implementation began. Where a
claim below is a reconstruction of intent rather than a fact observed in the repository, it is labelled as such.
The full pull-request chronology, test-count checkpoints and traceability table that support the claims in this
document are in the [Evidence appendix](#evidence-appendix) rather than the sections below, so that this document
reads as a product specification first and an engineering record second.

## Problem and user context

Credit analysts prepare CAMs against hard committee deadlines: manual financial spreading, then a narrative
drafted under time pressure, with real reputational risk if a number is wrong. An LLM can draft that narrative
faster, but unconstrained generation risks an unsupported claim or a miscalculated ratio reaching committee
undetected. An audit of the early codebase confirmed this was not theoretical: a debt-free company's DSCR was
being silently computed as `0` instead of undefined, which would have wrongly flagged a healthy borrower as a
covenant breach ([PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20); detail in the
[Evidence appendix](#evidence-appendix)).

**The user.** For MVP v1, the corporate credit analyst is the only user role in scope. They supply the source
material, run the workflow, and are accountable for the credit judgement the CAM ultimately supports. A separate
administrative step - calibrating an institution's own CAM templates and credit-policy documents via
`/calibrate` and `/calibrate-policy` - happens once before any deal is worked, and is not part of the per-deal
workflow below; MVP v1 does not define a separate calibration-owner role.

**The primary workflow (reconstructed from the shipped commands and their order):**

1. Gather borrower information and financial statements for the deal.
2. Establish which figures the framework can independently compute from raw line items, and which the analyst
   must supply directly because the borrower's own reporting convention doesn't fit the raw schema.
3. Prepare the financial spread and conduct qualitative research.
4. Draft the CAM: the Underwriter drafts, every framework-computed figure the draft reports is checked against
   its computed value, and the Risk Reviewer audits the result.
5. Review, edit and export the final draft - an editable `.docx` CAM and an `.xlsx` spreading workbook.
6. Decide whether the output is good enough to use, and what needs manual correction before it does.

**When source material is incomplete, conflicting, or not suitable for deterministic validation,** the product's
answer is to mark the affected figure or covenant N/A or UNRESOLVABLE rather than guess at it, and leave the
analyst to resolve it manually - see requirement R9 below.

## Product objectives, hypothesis and principles

**Reconstructed product objectives.** These are inferred from the shipped scope and the principles below, not a
record of goals set down before implementation:

- Reduce the time needed to produce a useful first CAM draft.
- Prevent unsupported or incorrectly calculated financial figures from silently reaching the draft.
- Preserve an inspectable record of inputs, calculations, review findings and the human decision.
- Let the analyst review and edit the exported CAM without losing the ability to audit its numbers.

**Reconstructed product hypothesis:**

> If AI drafts and audits a CAM through two independent agents, with every financial figure and policy outcome
> computed deterministically rather than by the model, can an analyst reach a useful first draft materially
> faster without the model ever becoming the source of truth for a number?

Reconstructed from the shipped scope, MVP v1's implicit bar for success was five things: the workflow removes
real preparation work; financial calculations stay outside the model; the audit step can genuinely challenge the
draft, not just confirm it; policy and covenant rules can be evaluated deterministically; and an analyst will
actually trust and use the result. [Success measures and validation status](#success-measures-and-validation-status)
below states which of these have actual evidence behind them today, and which remain estimated or unmeasured.

**Principles in force by MVP v1's close** ([PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20),
merged 2026-09-13), drawn from [design decisions](decisions.md). Three further decisions in that document - D4,
D8 and D9 - were made after that close, during subsequent hardening, and are described in the
[Evidence appendix](#evidence-appendix) instead of here; D6, D7 and D10 through D14 are later still and are out
of scope for this document.

- **The Maker and the Checker are independent prompts** ([D1](decisions.md#d1-the-maker-and-the-checker-are-independent-prompts)).
  They never import each other or share the Maker's reasoning. This was true from the start of MVP v1; the
  ability to configure the two to run on different underlying models came later and in two steps - see the
  [Evidence appendix](#evidence-appendix) for the dates. Before that second step, independence meant separate
  prompts, not yet separate models; the Checker's value comes from auditing cold either way.
- **Anything that can be computed is computed by code, and enforced by code**
  ([D2](decisions.md#d2-anything-that-can-be-computed-is-computed-by-code-and-enforced-by-code)). Ratios and
  covenant results come from deterministic calculation code, never from the model. A code-enforced rejection
  reason overrides the Checker even if it approved the draft, never the reverse.
- **Every step checkpoints to a file; the conversation is never the record**
  ([D3](decisions.md#d3-every-step-checkpoints-to-a-file-the-conversation-is-never-the-record)). State lives on
  disk so a compacted or resumed session loses at most the step in progress.
- **A ratio with a non-positive denominator is N/A, and its covenant is UNRESOLVABLE**
  ([D5](decisions.md#d5-a-ratio-with-a-non-positive-denominator-is-na-and-its-covenant-is-unresolvable)). Never
  PASS, never a false FAIL - the rule the debt-free DSCR case forced into existence, now codified for every
  ratio.

Keeping confidential material out of version control was already a practice at MVP v1's close - a `.gitignore`
entry for calibration inputs and deal outputs was added 2026-09-12, the same day as the project's first merged
pull request - but it was not yet the CI-enforced guarantee D9 later codified; see the
[Evidence appendix](#evidence-appendix).

## Core workflow and product boundaries

**Maker and Checker name the two AI agents, not people** - [D1](decisions.md#d1-the-maker-and-the-checker-are-independent-prompts)
states this plainly: *"the Maker and the Checker are independent prompts."* The Maker is the Underwriter agent;
the Checker is the Risk Reviewer agent. This separation is the point: a single agent auditing its own draft
agrees with itself, so the product gives the analyst two independent AI opinions to work from rather than one
agent wearing two hats. Neither agent is permitted to make the credit decision - that stays with the analyst.

The pipeline below is the system-internal version of the user journey above. The layer boundaries are the
product's own controls, not an implementation detail:

1. The analyst supplies source material (borrower financials, research, prior CAMs where available).
2. Deterministic computation runs first, producing ratios, subtotals, and covenant/policy results.
3. The Maker drafts the narrative. It receives those deterministically computed figures as input, and never
   recalculates them itself.
4. A ground-truth check compares every figure the Maker's draft *reports* in its narrative against those same
   deterministically computed figures, within a 0.5% tolerance, before the Checker ever sees the draft.
5. The Checker audits the draft cold, with no shared reasoning context with the Maker, and can only downgrade
   its verdict.
6. The analyst reviews the audited draft and makes the credit decision.

Each transition in that sequence is a trust boundary. The Maker receives deterministically computed ground-truth
figures as input; what it writes back is checked against those same figures before the Checker ever reviews the
draft; the Checker only ever reviews narrative it can still downgrade; the analyst is the only party who decides.

What is explicitly outside this boundary for MVP v1 - AML/sanctions/PEP screening, multi-currency support, full
covenant step-down modelling - is listed in [MVP v1 scope](#mvp-v1-scope) below.

## MVP v1 scope

| Priority | Item | Evidence |
| :--- | :--- | :--- |
| Must | CAM template system | PRs #1-20 |
| Must | Financial spreading engine with formula validation | PRs #1-20 |
| Must | `.docx` CAM export | PRs #1-20 |
| Must | Auditable `.xlsx` financial-spreading workbook export, built with real formulas rather than drafted values | PRs #1-20 |
| Must | The Maker-Checker governance loop itself | PRs #1-20 |
| Must | Deterministic policy engine layered on LLM narrative | PRs #1-20 |
| Must | Critical correctness and security fixes before wider use | [PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20) |
| Should | Policy checks wired into the primary slash-command interface, not just the headless script | [PR #24](https://github.com/ShamikM88/open-cam-framework/pull/24) |
| Won't | Multi-currency and FX - the desk is GBP-only | deliberately deferred |
| Won't | Full covenant step-down and cure-period modelling - scoped down to Conditions Subsequent tracking | deliberately deferred |
| Won't | AML, sanctions and PEP screening, ESG scoring | a separate AML team's system supplies this |

MVP v1 shipped with no "Could" items - everything not Must or Should was deferred outright (Won't, above).
Forward-year projections, stress testing, Conditions Subsequent tracking and source-citation hyperlinking were
not part of MVP v1's own scope call; they were delivered later, in the hardening pass described in the
[Evidence appendix](#evidence-appendix).

## Requirements and acceptance criteria

Each requirement below carries the acceptance condition that would show it is met - scenario, expected behaviour,
and evidence where it exists.

**R1 - Deterministic financial calculation.** Ratios and subtotals are computed by code, never drafted by the
model. *Acceptance:* given identical financial inputs, the calculation engine returns the same result regardless
of which model drafted the surrounding narrative.

**R2 - Explicit treatment of undefined results.** A zero or negative denominator produces N/A, not a false zero;
its covenant resolves to UNRESOLVABLE, never a silent PASS or FAIL. *Acceptance:* given a debt-free company with
zero debt service, DSCR resolves to N/A and its covenant resolves to UNRESOLVABLE, never a false FAIL. Verified:
[PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20) fixed exactly this case and added regression
coverage.

**R3 - Independent review.** The Checker audits the Maker's draft without inheriting its reasoning context, and
can only downgrade a verdict. *Acceptance:* given a Maker-drafted CAM, the Checker audits it without inheriting
the Maker's reasoning context, and can downgrade but never upgrade the verdict. This was validated as a real
production gap, not just asserted in code - see the issue #31 reopening in the
[Evidence appendix](#evidence-appendix).

**R4 - Ground-truth verification.** Every figure the Maker reports in the narrative that the framework itself
computed is checked against that computed value, within a 0.5% tolerance, before the Checker reviews it.
Analyst-supplied figures are outside this check by definition - the framework has no independently computed
value to check them against - and carry their own explicit caveat instead (D4, [Evidence appendix](#evidence-appendix)).
*Acceptance:* for every figure the framework itself computed, the value the Maker's narrative reports is within
0.5% of that computed ground truth before the Checker reviews it; a mismatch beyond that tolerance is a
Checker-visible finding, not a silent pass.

**R5 - Deterministic policy evaluation.** Covenant and compliance checks are evaluated PASS, FAIL or UNRESOLVABLE
against raw financials, never by asking the model to judge compliance in prose. *Acceptance:* given a defined
covenant and valid inputs, the result comes from the deterministic policy engine, never from the model's own
judgement of compliance.

**R6 - Persistent workflow state.** Each step checkpoints to disk; nothing exists only in the conversation.
*Acceptance:* given a workflow interrupted mid-step (a compacted or resumed session), resuming it re-hydrates the
last checkpointed state rather than restarting from scratch.

**R7 - Editable, auditable export.** A `.docx` CAM and an `.xlsx` spreading workbook, the latter built with real
formulas so the numbers stay auditable rather than a drafted table. *Acceptance:* given a completed deal, the
system produces a `.docx` CAM editable as a normal Word document and an `.xlsx` workbook whose ratio cells are
live formulas, not hardcoded values.

**R8 - Confidentiality.** Anything derived from real deal material stays out of version control.
*Acceptance:* given any file derived from real deal material, it is excluded from git by `.gitignore` from the
project's earliest commits (2026-09-12); from 2026-10-02 a CI test (D9) fails the build if such a file is tracked
anyway - see the [Evidence appendix](#evidence-appendix) for why that date is after MVP v1's close.

**R9 - Fail closed on missing or invalid data.** Where the system cannot establish a valid result, it exposes the
uncertainty rather than guessing. *Acceptance:* given a required input the analyst has not supplied, or a value
that cannot be meaningfully computed, the affected figure or covenant is marked N/A or UNRESOLVABLE rather than
guessed or silently omitted.

## Success measures and validation status

What is actually known today, kept separate from what is estimated or not yet measured at all:

| Measure | Status | Detail |
| :--- | :--- | :--- |
| Time to first useful draft | Estimated | 15-30 minutes, down from roughly a business day, is the one analyst's working target. No documented timing method or baseline exists for this figure - treat it as early single-user feedback, not a measured result. |
| Ground-truth match on framework-computed figures | Design guarantee, not aggregated | The 0.5% tolerance (R4) is enforced on every run; no measured match-rate across multiple deals has been collected. |
| Checker catch rate on Maker errors | Not yet measured | The DSCR defect was caught by a codebase audit, not observed as a live Checker catch on a real draft. No evaluation measures how often the Checker actually flags an error the Maker introduced. |
| Silent financial errors | Zero, by design | The debt-free DSCR case is the concrete instance that was tested and held. |
| Analyst can use and edit output without material rework | Observed qualitatively | The one analyst continues using the workflow; no measured rework rate exists. |
| Confidentiality | Measured | Zero real deal data in source control: `.gitignore`-enforced since 2026-09-12, CI-enforced since 2026-10-02 (D9). |
| Adoption | Measured | One analyst, ongoing, early-stage. |

Regression-suite growth (test counts at specific checkpoints, and why no current figure is quoted here) is in the
[Evidence appendix](#evidence-appendix).

## Assumptions, risks and limitations

**Assumptions.** Borrower financial statements and supporting documents are available to the analyst in a form
either `/spread` can compute from or the analyst can supply directly. Either a Claude Code session (slash
commands) or an `ANTHROPIC_API_KEY` (headless scripts) is available to run the workflow. An institution's CAM
templates and credit-policy documents have already been calibrated before a deal is worked. The analyst reviews
every draft before it is used - the product is decision support, not autonomous approval.

**Risks.**

- Incorrect extraction of figures from source documents - ground-truth checking and analyst review reduce this
  risk but do not eliminate it.
- Unsupported narrative claims in qualitative (non-financial) sections, which the deterministic ground-truth
  check does not cover.
- Policy misclassification on a covenant or condition the policy engine doesn't yet model.
- Incomplete source evidence producing an UNRESOLVABLE result the analyst must resolve by hand.
- Over-reliance on a plausible-looking draft despite Checker review, given no measured Checker catch rate exists
  yet (see [Success measures](#success-measures-and-validation-status) above).
- Confidential material exposure if the `.gitignore` or CI protections are bypassed, or a new confidential path
  is added without updating them.

**Limitations.** MVP v1 does not establish enterprise-scale or multi-institution reliability, regulatory
readiness, or reliability beyond a single analyst's use. It does not replace the analyst's credit judgement - the
analyst makes the decision; neither agent is permitted to.

## Retrospective learning and next validation gate

What delivery revealed, condensed to its product consequence - the full chronology is in the
[Evidence appendix](#evidence-appendix):

- [D5](decisions.md#d5-a-ratio-with-a-non-positive-denominator-is-na-and-its-covenant-is-unresolvable) exists
  because the debt-free DSCR defect was found the hard way, in a codebase audit, not reasoned out in advance.
- D1's model-independence rule shipped in two steps: a capability, then the configuration that actually activated
  it. "The capability exists" and "the capability is active" turned out to be different claims worth testing
  separately - a lesson this document tries to apply to its own acceptance criteria.
- D4 exists because MVP v1's single rigid spreading schema didn't survive contact with a real borrower's
  reporting convention.
- D8 and D9 both arrived as the project moved past its first two weeks. Confidentiality was a practice from day
  one, but it took until early October to become a CI-tested guarantee rather than a documented one.

**Evidence needed before this moves beyond a single-analyst MVP:**

1. A documented timing method and baseline for the 15-30 minute target, not one analyst's impression.
2. A measured Checker catch rate - how often the Checker actually flags an error the Maker introduced, across
   more than one analyst and more than one deal.
3. Confirmation that the 0.5%-tolerance and UNRESOLVABLE design holds against the issues already tracked under
   the repository's `gap-analysis` label, not only the ones closed so far.
4. An explicit decision, outside this document, on regulatory and compliance readiness before any institution
   beyond the first relies on it.

## Evidence appendix

**The closing audit and the backlog it opened.** [PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20)
(merged 2026-09-13) closed MVP v1 with a holistic audit of the unreviewed foundation: the debt-free DSCR defect, a
near-identical bug dropping Provisions and Other Long-Term Liabilities from total liabilities, a path-traversal
risk, and non-atomic state writes. That same audit, on the same day, opened a tracked backlog of 19 further
issues (#21 through #39). Four pull requests closed the highest-priority tranche of that backlog over the
following day, 2026-09-14: [PR #41](https://github.com/ShamikM88/open-cam-framework/pull/41) (#27, #21, #34),
[PR #42](https://github.com/ShamikM88/open-cam-framework/pull/42) (#22, #23),
[PR #43](https://github.com/ShamikM88/open-cam-framework/pull/43) (#31 - reopened days later, see below; #32,
#26), and [PR #45](https://github.com/ShamikM88/open-cam-framework/pull/45) (#36, #37) - ten of the nineteen, in
order of severity rather than issue number. The remainder, and every issue surfaced by a later audit pass, is
tracked under the repository's [`gap-analysis` label](https://github.com/ShamikM88/open-cam-framework/labels/gap-analysis),
which has grown past the original 19 as further passes have run; it is not a count this document repeats, since
it changes independently of this document and is one click away from the source.

**D1's two-step model independence.** [PR #43](https://github.com/ShamikM88/open-cam-framework/pull/43) (merged
2026-09-14) introduced the `checker_model` capability and closed issue #31 on it, but `config/settings.json` did
not yet set one, so the Checker still fell back to the Maker's own model in practice.
[PR #53](https://github.com/ShamikM88/open-cam-framework/pull/53) (merged 2026-09-17) actually configured a
separate `checker_model`, completing the change and properly re-closing issue #31 (reopened in the meantime once
the gap was found).

**D4 - analyst-supplied figures.** Traces to
[issue #55](https://github.com/ShamikM88/open-cam-framework/issues/55), opened 2026-09-16 and closed 2026-09-17.
MVP v1 itself only supported `/spread` computing every ratio from raw line items; the analyst-supplied
alternative, and the explicit audit-guarantee caveat it carries, came after.

**D8 and D9 - later hardening.** [D8](decisions.md#d8-persisted-conventions-are-deterministic-local-memory-confirmed-by-a-person) -
persisting analyst-confirmed conventions as local memory - traces to
[issue #86](https://github.com/ShamikM88/open-cam-framework/issues/86) and
[issue #89](https://github.com/ShamikM88/open-cam-framework/issues/89), both opened 2026-09-25.
[D9](decisions.md#d9-anything-derived-from-real-material-is-git-ignored-and-a-test-enforces-it) - the CI-enforced
guard against confidential material being tracked - traces to
[issue #149](https://github.com/ShamikM88/open-cam-framework/issues/149), opened 2026-10-02.

**Test-count checkpoints.** Two fixed, historical facts, each tied to an immutable merge:

| Event | Tests passing | Source |
| :--- | :--- | :--- |
| MVP v1 closes (PR #20 merges, 2026-09-13) | 188 | PR #20's own test checklist |
| `badges/test-count.json` introduced, CI-enforced ([PR #118](https://github.com/ShamikM88/open-cam-framework/pull/118)) | 439 | the PR that added it |

The count has grown continuously since; this document does not repeat a current figure, since doing so would
make it wrong the next time someone reads it. [`badges/test-count.json`](../badges/test-count.json) on the
default branch is the CI-enforced source, per [D14](decisions.md#d14-the-test-count-badge-is-checked-by-ci-and-never-corrected-by-it).

**Traceability.**

| Claim in this document | Evidence |
| :--- | :--- |
| MVP v1 boundary | [PR #1](https://github.com/ShamikM88/open-cam-framework/pull/1) (foundation) through [PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20) (closing audit) |
| Fast-follow: policy checks in the primary interface | [PR #24](https://github.com/ShamikM88/open-cam-framework/pull/24) |
| The debt-free DSCR defect and its fix | [PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20) |
| The 19-issue backlog opened at MVP close, and its first four closing pull requests | [PR #41](https://github.com/ShamikM88/open-cam-framework/pull/41), [PR #42](https://github.com/ShamikM88/open-cam-framework/pull/42), [PR #43](https://github.com/ShamikM88/open-cam-framework/pull/43), [PR #45](https://github.com/ShamikM88/open-cam-framework/pull/45) |
| Independent prompts, deterministic computation, checkpointed state and the UNRESOLVABLE rule - the MVP v1 principles | [Design decisions](decisions.md), D1 through D3, D5 |
| Independent Maker/Checker models - capability, then actual configuration, closing issue #31 | [PR #43](https://github.com/ShamikM88/open-cam-framework/pull/43) (capability), [PR #53](https://github.com/ShamikM88/open-cam-framework/pull/53) (configured, closes #31) |
| Analyst-supplied figures (D4), persisted conventions (D8) and the CI-enforced confidentiality guard (D9) - all post-MVP | [issue #55](https://github.com/ShamikM88/open-cam-framework/issues/55) (D4), [issue #86](https://github.com/ShamikM88/open-cam-framework/issues/86)/[#89](https://github.com/ShamikM88/open-cam-framework/issues/89) (D8), [issue #149](https://github.com/ShamikM88/open-cam-framework/issues/149) (D9) |
| Test-count checkpoints | PR #20's own checklist (188), [PR #118](https://github.com/ShamikM88/open-cam-framework/pull/118) (439) |
| 0.5% ground-truth tolerance, one-analyst validation, 15-30 minute target | the published OpenCAM case study (linked from the project README) |
