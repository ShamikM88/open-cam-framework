# OpenCAM MVP v1 — product requirements document (reconstructed)

This document reconstructs the product definition behind OpenCAM MVP v1 from the repository's own pull requests,
[design decisions](decisions.md), and the project's delivery history. It was written after MVP v1 shipped, from
that evidence, and is not presented as a document that existed in this form before implementation began. Its
purpose is to make the product reasoning behind MVP v1 - the problem, the users, the scope, the requirements and
the acceptance criteria - explicit and traceable to the pull requests and decisions that actually produced it.

## Problem and hypothesis

Credit analysts prepare Credit Assessment Memorandums (CAMs) against hard committee deadlines: manual financial
spreading, then a narrative drafted under time pressure, with real reputational risk if a number is wrong. An LLM
can draft that narrative faster, but unconstrained generation risks an unsupported claim or a miscalculated ratio
reaching committee undetected. An audit of the early codebase confirmed this was not theoretical: a debt-free
company's DSCR was being silently computed as `0` instead of undefined, which would have wrongly flagged a
healthy borrower as a covenant breach ([PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20)).

The product question MVP v1 had to answer was not "can AI draft a CAM faster," but:

> If AI drafts and audits a CAM through two independent agents, with every financial figure and policy outcome
> computed deterministically rather than by the model, can an analyst reach a useful first draft materially
> faster without the model ever becoming the source of truth for a number?

MVP v1 needed to prove five things: the workflow removes real preparation work; financial calculations stay
outside the model; the audit step can genuinely challenge the draft, not just confirm it; policy and covenant
rules can be evaluated deterministically; and an analyst will actually trust and use the result.

## Who it's for

"Maker" and "Checker" name the two AI agents, not people - [D1](decisions.md#d1-the-maker-and-the-checker-are-independent-prompts)
states this plainly: *"the Maker and the Checker are independent prompts."*

- **Maker - the Underwriter agent.** Drafts the CAM narrative from structured financial data and research evidence.
- **Checker - the Risk Reviewer agent.** Independently audits the Maker's draft, with no shared reasoning
  context, and can only downgrade a verdict, never upgrade one.
- **The human user - the corporate credit analyst.** The one person in the loop. Supplies the source material,
  runs the workflow, and is accountable for the credit judgement the CAM ultimately supports - neither agent makes
  or is permitted to make that decision.

This separation is the point: a single agent auditing its own draft agrees with itself, so the product gives the
analyst two independent AI opinions to work from, not one agent wearing two hats.

## Product principles

The principles below are the subset of [design decisions](decisions.md) that were in force by MVP v1's close
([PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20), merged 2026-09-13). Two further decisions in
that document, D8 and D9, were made weeks later during subsequent hardening and are described under
[What changed after MVP](#what-changed-after-mvp) instead of here; D6, D7 and D10 through D14 are later still and
are out of scope for this document.

- **The Maker and the Checker are independent prompts** ([D1](decisions.md#d1-the-maker-and-the-checker-are-independent-prompts)).
  They never import each other or share the Maker's reasoning, and can optionally run on different underlying
  models. The Checker's value comes from auditing cold.
- **Anything that can be computed is computed by code, and enforced by code**
  ([D2](decisions.md#d2-anything-that-can-be-computed-is-computed-by-code-and-enforced-by-code)). Ratios and
  covenant results come from `spreading_builder.py` and `policy_engine.py`, never from the model. A code-enforced
  rejection reason overrides the Checker even if it approved the draft, never the reverse.
- **Every step checkpoints to a file; the conversation is never the record**
  ([D3](decisions.md#d3-every-step-checkpoints-to-a-file-the-conversation-is-never-the-record)). State lives on
  disk so a compacted or resumed session loses at most the step in progress.
- **Analyst-supplied figures are recorded as given and disclosed, never silently reconciled**
  ([D4](decisions.md#d4-analyst-supplied-figures-are-recorded-as-given-and-disclosed-never-silently-reconciled)).
  When an analyst's own spread can't be recomputed from the raw schema, the CAM carries an explicit caveat about
  the reduced audit guarantee.
- **A ratio with a non-positive denominator is N/A, and its covenant is UNRESOLVABLE**
  ([D5](decisions.md#d5-a-ratio-with-a-non-positive-denominator-is-na-and-its-covenant-is-unresolvable)). Never
  PASS, never a false FAIL - the rule the debt-free DSCR case forced into existence, now codified for every ratio.

Keeping confidential material out of version control was already a practice at MVP v1's close - a `.gitignore`
entry for calibration inputs and deal outputs was added 2026-09-12, the same day as the project's first merged
pull request - but it was not yet the CI-enforced guarantee D9 later codified; see
[What changed after MVP](#what-changed-after-mvp).

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
| Could | Forward-year projections, stress testing, Conditions Subsequent, source-citation hyperlinking | shipped post-MVP |
| Won't | Multi-currency and FX - the desk is GBP-only | deliberately deferred |
| Won't | Full covenant step-down and cure-period modelling - scoped down to Conditions Subsequent tracking | deliberately deferred |
| Won't | AML, sanctions and PEP screening, ESG scoring | a separate AML team's system supplies this |

A second hardening pass followed MVP v1's close - see [What changed after MVP](#what-changed-after-mvp) below.

## Functional and non-functional requirements

**FR1 - Deterministic financial calculation.** Ratios and subtotals are computed by `spreading_builder.py` and
`policy_engine.py`, never drafted by the model. Same inputs, same result, regardless of which model drafted the
narrative.

**FR2 - Explicit treatment of undefined results.** A zero or negative denominator produces N/A, not a false
zero; its covenant resolves to UNRESOLVABLE, never a silent PASS or FAIL.

**FR3 - Independent review.** The Checker audits the Maker's draft without inheriting its reasoning context, and
can only downgrade a verdict.

**FR4 - Ground-truth verification.** Every figure the Maker reports in the narrative is checked against the
deterministically computed financials, within a 0.5% tolerance, before the Checker reviews it.

**FR5 - Deterministic policy evaluation.** Covenant and compliance checks are evaluated PASS, FAIL or
UNRESOLVABLE by `policy_engine.py` against raw financials, never by asking the model to judge compliance in
prose.

**FR6 - Persistent workflow state.** Each step checkpoints to `state.json`; nothing exists only in the
conversation.

**FR7 - Editable, auditable export.** A `.docx` CAM and an `.xlsx` spreading workbook, the latter built with real
formulas so the numbers stay auditable rather than a drafted table.

**NFR1 - Correctness.** Financial and policy outputs are deterministic and regression-tested.

**NFR2 - Independence.** Maker and Checker share no reasoning context, and this is validated rather than merely
stated - see the Issue #31 reopening under [What changed after MVP](#what-changed-after-mvp).

**NFR3 - Confidentiality.** Anything derived from real deal material stays out of version control, via
`.gitignore` from the project's earliest commits. The CI test that fails the build if such material is ever
tracked anyway was added after MVP v1 - see [What changed after MVP](#what-changed-after-mvp).

**NFR4 - Fail closed.** Where the system cannot establish a valid result, it exposes the uncertainty (N/A,
UNRESOLVABLE) rather than guessing.

## Acceptance criteria

**AC1 - Undefined DSCR.** Given a debt-free company with zero debt service, DSCR resolves to N/A, not `0`, and
its covenant resolves to UNRESOLVABLE, never a false FAIL. Verified: [PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20)
fixed exactly this case and added regression coverage.

**AC2 - Deterministic calculation.** Given identical financial inputs, the calculation engine returns the same
result regardless of which model drafted the surrounding narrative.

**AC3 - Maker/Checker independence.** Given a Maker-drafted CAM, the Checker audits it without inheriting the
Maker's reasoning context, and can downgrade but never upgrade the verdict. This was validated in practice, not
just asserted in code: Issue #31 was marked resolved once the two agents *could* run on different models, then
reopened after a later review found the setting had never actually been switched on in production, and was
re-closed only once verified end to end.

**AC4 - Ground-truth tolerance.** Every reported figure in the Maker's narrative is within 0.5% of the
deterministically computed ground-truth financials before the Checker reviews it. A mismatch beyond that
tolerance is a Checker-visible finding, not a silent pass.

**AC5 - Policy determinism.** Given a defined covenant and valid inputs, the result comes from
`policy_engine.py`, never from the model's own judgement of compliance.

## Architecture

The layer boundaries below are the product's own controls, not an implementation detail:

1. The analyst supplies source material (borrower financials, research, prior CAMs where available).
2. Deterministic computation runs first: `spreading_builder.py` produces ratios and subtotals, `policy_engine.py`
   evaluates covenants and policy rules against them.
3. The Maker (Underwriter) drafts the narrative. It receives the figures step 2 already computed deterministically
   as input, and never recalculates them itself.
4. A ground-truth check compares every figure the Maker's draft *reports* in its narrative against those same
   deterministically computed figures, to within 0.5% tolerance, before the Checker ever sees the draft.
5. The Checker (Risk Reviewer) audits the draft cold, with no shared reasoning context with the Maker, and can
   only downgrade its verdict.
6. The analyst reviews the audited draft and makes the credit decision; neither agent is permitted to make it.

Each transition in that sequence is a trust boundary. The Maker receives deterministically computed ground-truth
figures as input; what it writes back is checked against those same figures before the Checker ever reviews the
draft; the Checker only ever reviews narrative it can still downgrade; the analyst is the only party who decides.

## What changed after MVP

[PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20) (merged 2026-09-13) closed MVP v1 with a
holistic audit of the unreviewed foundation (`state_manager.py`, `spreading_builder.py`): the debt-free DSCR
defect, a near-identical bug dropping Provisions and Other Long-Term Liabilities from total liabilities, a
path-traversal risk in `state_manager.py` and `deal_export.py`, and non-atomic `state.json` writes.

That same audit, on the same day, opened a tracked backlog of 19 further issues (#21 through #39). Four pull
requests closed the highest-priority tranche of that backlog over the following day, 2026-09-14:
[PR #41](https://github.com/ShamikM88/open-cam-framework/pull/41) (#27, #21, #34),
[PR #42](https://github.com/ShamikM88/open-cam-framework/pull/42) (#22, #23),
[PR #43](https://github.com/ShamikM88/open-cam-framework/pull/43) (#31, #32, #26), and
[PR #45](https://github.com/ShamikM88/open-cam-framework/pull/45) (#36, #37) - ten of the nineteen, in order of
severity rather than issue number. The remainder of that backlog, and every issue surfaced by a later audit pass,
is tracked under the repository's [`gap-analysis` label](https://github.com/ShamikM88/open-cam-framework/labels/gap-analysis),
which has grown past the original 19 as further passes have been run; it is not a count this document repeats,
since it changes independently of this document and is one click away from the source.

The product lesson is the same across both the closing audit and the backlog it opened: a defect that looks like
an edge case in testing (a ratio with no meaningful value) is, in production, the difference between a healthy
borrower and a flagged one. [D5](decisions.md#d5-a-ratio-with-a-non-positive-denominator-is-na-and-its-covenant-is-unresolvable)
exists because this was found the hard way, not reasoned out in advance.

Two further decisions in [design decisions](decisions.md) came later still, as the project moved into broader
hardening. [D8](decisions.md#d8-persisted-conventions-are-deterministic-local-memory-confirmed-by-a-person) -
persisting analyst-confirmed conventions as local memory - traces to
[issue #86](https://github.com/ShamikM88/open-cam-framework/issues/86) and
[issue #89](https://github.com/ShamikM88/open-cam-framework/issues/89), both opened 2026-09-25.
[D9](decisions.md#d9-anything-derived-from-real-material-is-git-ignored-and-a-test-enforces-it) - the CI-enforced
guard against confidential material being tracked - traces to
[issue #149](https://github.com/ShamikM88/open-cam-framework/issues/149), opened 2026-10-02. The underlying
practice predates the guarantee: a `.gitignore` entry excluding calibration inputs and deal outputs was already
in place from 2026-09-12. D9 is what turned that practice into a tested rule rather than a documented one.

## Success measures

Two test-count checkpoints are fixed, historical facts, each tied to an immutable merge:

| Event | Tests passing | Source |
| :--- | :--- | :--- |
| MVP v1 closes ([PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20) merges, 2026-09-13) | 188 | PR #20's own test checklist |
| `badges/test-count.json` introduced, CI-enforced ([PR #118](https://github.com/ShamikM88/open-cam-framework/pull/118)) | 439 | the PR that added it |

The count has grown continuously since; this document does not repeat a current figure, since doing so would
make it wrong the next time someone reads it. [`badges/test-count.json`](../badges/test-count.json) on the
default branch is the CI-enforced source, per [D14](decisions.md#d14-the-test-count-badge-is-checked-by-ci-and-never-corrected-by-it).

Other measures, kept to what was actually validated rather than inflated into a claim of scale:

- **Time to first useful draft.** 15-30 minutes, down from roughly a business day - a working target validated
  with one analyst, not a measured production SLA.
- **Silent financial errors.** Zero tolerated by design; the debt-free DSCR case is the concrete instance where
  this was tested and held.
- **Confidentiality.** No real deal data in source control since the project's earliest commits; the CI test
  that fails the build over it
  ([D9](decisions.md#d9-anything-derived-from-real-material-is-git-ignored-and-a-test-enforces-it)) followed as
  part of later hardening, not at MVP v1.
- **Adoption.** One analyst to date. Early-stage, not yet evidence of scale.

## What MVP v1 did and didn't prove

**It proved:** a governed AI workflow can draft a credit memo while keeping financial computation and policy
decisions deterministic; two independent agents can meaningfully audit each other when they share no reasoning
context; a real correctness defect (the DSCR case) can be caught by a codebase audit rather than by an analyst
catching it downstream, which is the more expensive and higher-risk place to catch it; and a single analyst found
the resulting workflow useful enough to keep using it.

**It didn't prove:** enterprise-scale adoption, production-grade reliability under multiple analysts or
institutions, regulatory readiness, universal financial-spreading coverage, or that this class of defect is now
exhaustively closed - the backlog opened the same day PR #20 merged held several more, and the test-count badge
exists precisely because prose claims about system state have drifted before.

The next product question is not "what else can we automate" but what evidence would justify moving this from a
validated single-analyst MVP to something a wider credit organisation could trust.

## Traceability

| Claim in this document | Evidence |
| :--- | :--- |
| MVP v1 boundary | [PR #1](https://github.com/ShamikM88/open-cam-framework/pull/1) (foundation) through [PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20) (closing audit) |
| Fast-follow: policy checks in the primary interface | [PR #24](https://github.com/ShamikM88/open-cam-framework/pull/24) |
| The debt-free DSCR defect and its fix | [PR #20](https://github.com/ShamikM88/open-cam-framework/pull/20) |
| The 19-issue backlog opened at MVP close, and its first four closing pull requests | [PR #41](https://github.com/ShamikM88/open-cam-framework/pull/41), [PR #42](https://github.com/ShamikM88/open-cam-framework/pull/42), [PR #43](https://github.com/ShamikM88/open-cam-framework/pull/43), [PR #45](https://github.com/ShamikM88/open-cam-framework/pull/45) |
| Maker/Checker independence, deterministic computation and the MVP v1 principles above | [Design decisions](decisions.md), D1 through D5 |
| Persisted conventions (D8) and the CI-enforced confidentiality guard (D9) - both post-MVP | [issue #86](https://github.com/ShamikM88/open-cam-framework/issues/86)/[#89](https://github.com/ShamikM88/open-cam-framework/issues/89) (D8), [issue #149](https://github.com/ShamikM88/open-cam-framework/issues/149) (D9) |
| Test-count checkpoints | PR #20's own checklist (188), [PR #118](https://github.com/ShamikM88/open-cam-framework/pull/118) (439) |
| 0.5% ground-truth tolerance, one-analyst validation, 15-30 minute target | the published OpenCAM case study (linked from the project README) |
