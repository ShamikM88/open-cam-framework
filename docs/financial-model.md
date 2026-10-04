# Financial model and policy engine

> **Status.** This page was assembled in the documentation tranche of issue #117 from material that used to live in `CLAUDE.md`, moved with only the textual fixes recorded in the [move ledger](move-ledger.md). A later tranche adds worked examples and explanation around it. The source of truth for behaviour is the code and tests; see the [index](README.md).

The deterministic core that computes the figures a CAM reports and decides what a draft must contain: spreading and ratios, covenant results, required conditions, security gaps and the code-enforced audit of a draft. Why it is built this way: [design decisions](decisions.md) (D2, D4, D5, D6).

## Policy engine

**`scripts/policy_engine.py`** — no `anthropic` dependency. `evaluate_deal_policy(state_dict)`
is the deterministic governance core: covenant PASS/FAIL/UNRESOLVABLE evaluation against
`covenants`, security perfection/ranking gap detection against `security_package`, Condition
Precedent (`cp_id`) and Condition Subsequent (`cs_id`) generation from the deal's own structure
(KYC/AML and facility-execution CPs always required; security-mapping/perfection/priority CPs
and guarantee CPs only when the underlying gap/guarantee actually exists), and downside covenant
breach detection (a covenant that PASSes in the base case but FAILs, or becomes UNRESOLVABLE, under stress).
Returns `policy_state`: `{"required_conditions_precedent", "required_conditions_subsequent",
"covenant_results", "security_gaps", "downside_covenant_breaches", "forward_covenant_results"}`. **A ratio with a zero or negative
denominator is N/A, and its covenant is UNRESOLVABLE (issue #169).** `spreading_builder` returns `None` for any
ratio whose denominator (EBITDA, total equity, interest paid, debt service, current liabilities, revenue, cost of
sales) is not positive -- before this, negative EBITDA or equity gave a finite negative leverage/gearing that sat
below every maximum threshold, so a loss-making borrower PASSED "leverage <= 3.5x". Only the denominator is tested
(a negative numerator over a positive denominator, e.g. DSCR below zero, is a real figure and a minimum-DSCR
covenant correctly FAILs on it). A covenant on an N/A ratio is UNRESOLVABLE for `minimum` and `maximum` alike --
never PASS, never a false FAIL -- and every `covenant_results` entry now has a `reason` (None when resolved; for
an N/A ratio one sentence naming the metric and denominator, e.g. "gross leverage not meaningful: EBITDA is
negative" / "gearing not defined: total equity is zero"; `spreading_builder.describe_undefined_ratio()`, which
only speaks about a RECORDED denominator: a subtotal that is absent or not a number, or an analyst-supplied
deal's missing `raw` block, gives the generic "has no computed value for this period", never "is zero"). **Defence
in depth:** if the period's recorded denominator is zero or negative, the covenant is UNRESOLVABLE even when a
number is stored next to it (analyst-supplied ratios are recorded as given, and a state checkpointed before this
fix may hold a negative leverage); the stored value is ignored and the reason says so. What it cannot catch: a
stored ratio whose denominator is not recorded in `financials` (e.g. an analyst-supplied DSCR, which has no `raw`
block to check against), and a state checkpointed before the fix whose stored negative ratio still reaches the
grounding figures (re-run `/spread` to refresh it; only the covenant status is protected). The
reason reaches the Maker in `covenant_results` and the code-enforced rejection text (UNRESOLVABLE has always been
a code-enforced reject reason). A covenant that PASSes in the base case but is FAIL or UNRESOLVABLE in a downside
year is a `downside_covenant_breaches` entry (new keys `downside_status` and `reason`), so a stress that wipes out
EBITDA is disclosed, not silently dropped (the code-enforced rejection text for one reads "cannot be tested in
FY+1 under stress (reason)", not "breaches threshold"). The exported workbook's ratio cells use the same rule
(`IF(denominator>0, ..., "N/A")`, not just `IFERROR`; the working capital cycle row wraps its sum in `IFERROR`
so an "N/A" day row gives "N/A", not `#VALUE!`), so it agrees with `state.json` cell for cell. The margin rows use the same guard but have no `state.json`
counterpart, and the collateral cover cells are `IFERROR`-only; neither is a covenant metric. **Forward years
(issue #176):** a covenant applies to every forward base-case year (`FY+1`..`FY+3`) that has a recorded,
non-empty ratio set; a year with nothing recorded is not evaluated, and state.json carries no covenant tenor, so
a covenant that really ends earlier is still reported for later projected years. `forward_covenant_results` is
the complete record -- one entry per year and covenant, PASS included -- each being `_evaluate_covenant()`'s
result (same #169 rules, `reason` for UNRESOLVABLE) plus `year` and a stable `forward_id`
(`FORWARD-FY-1-GROSS-LEVERAGE`, numeric suffix for two covenants on one metric). It is a separate list rather
than a year dimension on `covenant_results` because every consumer of that list (the reject reasons, the
orchestrator context, the eval contexts) assumes one FY-Current entry per covenant, and `policy_checks.py` turns
any FAIL/UNRESOLVABLE in it into a rejection. **Disclosure only:** no code-enforced reason reads
`forward_covenant_results`, so a forward-year FAIL or UNRESOLVABLE never rejects a deal by itself, and it cannot
go unrecorded. `orchestrator.py` shows the model only the non-PASS entries, as neutral data (no instruction; the
wording for the Maker and Reviewer is post-baseline prompt work); the full list is persisted with `policy_state`
in state.json. It never repeats `downside_covenant_breaches` (which still reports only base PASS -> stressed
non-PASS): a year already failing in the base case appears only in the forward list. This is
what both
`orchestrator.py` (headless) and `scripts/policy_check.py` (slash-command interface, below) call
to get the exact same code-enforced structural facts regardless of which interface a deal runs
through -- `agents/risk_reviewer_agent.md`'s own preamble treats `policy_state`'s presence in a
prompt as the signal that this deterministic layer is active for that run.

## Draft audit

**`scripts/policy_checks.py`** — no `anthropic` dependency. `parse_underwriter_output(draft_text)`
extracts the Underwriter's trailing structured JSON block (`cp_ids_included`,
`risk_categories_covered`, `reported_figures`, `sources`, `financials_source_disclosed`,
`credit_policy_considered`, etc. -- see `agents/underwriter_agent.md`'s Structured Output
guideline); `ground_truth_figures(financials, ratios, collateral, downside_case)` flattens the
deal's actual computed figures into one lookup dict; `check_draft_compliance(draft_text,
policy_state, ground_truth_figures_dict, financials_source=None, credit_policy_present=None)` is
the single source of truth for "why would this draft be code-enforced-REJECTED" -- covenant/
security/CP/CS completeness, risk-taxonomy coverage (`REQUIRED_RISK_TAXONOMY`), narrative-vs-
ground-truth figure mismatches, and the self-declared-field presence checks (analyst-supplied
disclosure, credit-policy consideration). Returns a plain list of human-readable reason strings,
empty if compliant -- callers (`orchestrator.py`'s `_apply_deterministic_policy_checks()`,
`.claude/commands/review.md`'s prose) decide what a non-empty list means for their own verdict.

## Combined policy check

**`scripts/policy_check.py`** — no `anthropic` dependency, same "importable or standalone" duality
as `deal_export.py`. `compute(company, proposal, draft_path=None)` wraps `policy_engine.py`/
`policy_checks.py` into one call: without `draft_path`, returns just `policy_state` (e.g. for
`/assemble` building its own drafting context, before a draft exists); with `draft_path`, also
runs the full `check_draft_compliance()` audit against that file's trailing structured JSON
block. This is what gives the slash-command interface (`/assemble`'s and `/review`'s own Bash
steps) the identical code-enforced governance `orchestrator.py`'s headless pipeline already has,
without either interface reimplementing the logic in prose:
```
python scripts/policy_check.py --company "Acme Corp" --proposal "Fleet Loan" --draft "deals/Acme Corp/Fleet Loan_draft.md"
```

## Spreading check

**`scripts/spreading_check.py`** — no `anthropic` dependency, same "importable or standalone"
duality as `policy_check.py`/`deal_export.py`. `compute(company, proposal,
multi_period_financials=None, stress_assumptions=None, update_financials_source=True)` wraps
`spreading_builder.py`'s `evaluate_financial_model()`/`evaluate_downside_case()`: merges
freshly-given raw periods into whatever this deal's `state.json` already has on file (never a
blind replace -- `/spread` and `/project` each supply only the periods they're responsible for,
in separate calls), recomputes `financials`/`ratios` for the affected periods, and -- when
stress assumptions (freshly given or already on file) and at least one forward period exist --
derives `downside_case` too. The merge is two levels deep for both inputs: a period's raw
figures are merged field-by-field into whatever that period already had on file (a correction to
just `revenue` doesn't discard the period's other already-recorded fields), and fresh
`stress_assumptions` are merged key-by-key the same way (re-confirming one shock doesn't drop
another already-confirmed one left unmentioned) -- caught in post-merge review of the PR that
introduced this script, where the first version replaced each wholesale instead. This is what
gives the slash-command interface (`/spread`'s and
`/project`'s own Bash steps) the identical code-enforced formula evaluation `orchestrator.py`'s
headless pipeline already has, instead of Claude recalculating the same subtotals/ratios by
hand in prose (see issue #98). Deliberately not used by `/spread`'s analyst-supplied mode, which
skips independent recomputation entirely (see issue #55):
```
python scripts/spreading_check.py --company "Acme Corp" --proposal "Fleet Loan" --financials "deals/Acme Corp/Fleet Loan_financials_input.json"
```
`update_financials_source` (CLI: `--no-update-financials-source` to disable) gates whether a
fresh computation stamps `financials_source: "framework-computed"` -- `financials_source` is a
whole-deal flag `/spread` owns the decision for, not per-period, so `/project`'s own call always
passes this flag: without it, `/project` supplying forward-year figures on a deal whose
historicals were recorded via `/spread`'s analyst-supplied mode would silently flip the deal's
flag back to `"framework-computed"`, dropping the Guideline 9 caveat requirement for figures
that were never actually independently recomputed.
