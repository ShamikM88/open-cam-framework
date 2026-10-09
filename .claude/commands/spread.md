---
description: Extract and spread 3-5 years of P&L/Balance Sheet data into the core credit ratios, or record an analyst-supplied pre-spread snapshot as-is (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [P&L and Balance Sheet figures, or a path to the source document] [OR a pre-spread financials/ratios snapshot]"
disable-model-invocation: true
---

## Task: /spread

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

**Arguments:** `--company "<Name>" --proposal "<Proposal name>"`, followed by whatever inputs
you have below (this deal identity is needed so results can be checkpointed to its state file).

$ARGUMENTS

## State: read

Glob `deals/<company>/<proposal>_*/state.json` (there should be at most one, regardless of
date). If found, read it and treat every figure already recorded there as the source of truth —
never re-derive a number from a summarized/compacted conversation when state.json already has
it. If none exists, this is the first step run for this deal.

### Check for a persisted convention

Before asking which mode applies, check whether a spreading convention is already on file for
this borrower or this institution (see issue #58) -- neither is ever auto-applied without an
explicit response, but surfacing what's on file avoids re-deriving/re-asking from scratch every
time:
```
python scripts/conventions.py --enterprise --read
python scripts/conventions.py --company "<company>" --read
```
If either returns `"found": true`, tell the analyst what's on file (the confirmed date, the
mode, and the free-text note) before asking which mode applies to this deal -- e.g. "On file:
enterprise-wide default is analyst-supplied ('this institution always supplies its own pre-spread
figures'); no borrower-specific override for `<company>`. Apply this, or does this deal differ?"
Always require an explicit response before setting anything, even when both are absent or both
agree -- never auto-apply silently, and never merge or auto-pick between the two scopes yourself
if they conflict (that's the analyst's call, not something to infer -- mirrors #55's own explicit
"never infer an institution's proprietary accounting treatment" principle).

**Primary inputs** (ask the user for whatever's missing and isn't already in state): 3-5 years
of Profit & Loss and Balance Sheet data. **Or**, if this analyst already spreads against their
own company-approved template (organizations often treat certain line items differently than
this framework's own raw schema -- e.g. Depreciation charged within Cost of Goods Sold rather
than as its own line, common for asset-hire businesses), they may instead supply that template's
own already-computed figures and ratios directly -- see "Analyst-supplied pre-spread figures"
below. Ask which applies before assuming which mode this is.

### Figures read from an image (either mode)

This applies whenever you read financial figures out of pixels: a screenshot, a photo, a scanned page, or a PDF page
with no text layer that you have to read visually (issue #132). It does **not** apply to figures read from text
(Excel, CSV, a PDF with a text layer, figures the analyst typed); those follow the two sections below unchanged.

A misread digit, a dropped decimal, a flipped sign or a misaligned column would otherwise become the deal's ground
truth: in the analyst-supplied mode nothing recomputes the figures at all, and in the default mode the ratios are
recomputed from whatever lines you transcribed. So for image figures, **do not run `spreading_check.py`, do not edit
`state.json` and do not append `"spread"` to `steps_completed` yourself**. `scripts/transcription_check.py` does
all of that, only after the analyst has explicitly confirmed a read-back.

1. **Stage the transcription.** Write `deals/<company>/<proposal>_transcription.json`, every figure as text exactly as
   it appears in the image: thousands commas, decimals including trailing zeros, parentheses or a minus sign for a
   negative. Never round, rescale, convert, flip the sign of or "correct" a figure, and keep each period in its own
   column of the image:
   ```json
   {"mode": "framework-computed",
    "source": {"file": "<path to the image>", "kind": "image", "description": "<what it is>", "unit": "GBP thousands",
               "cost_sign": "negative", "outflow_sign": "negative", "liability_sign": "positive"},
    "periods": {"FY-Current": {"lines": {"revenue": "1,200.0", "cost_of_sales": "(700.0)"},
                               "subtotals": {"gross_profit": "500.0"}, "ratios": {}}},
    "acknowledged": []}
   ```
   `mode` is `framework-computed` or `analyst-supplied`, whichever you agreed with the analyst. `kind` is `image` or
   `scanned-document`. `unit` is read from the image (ask if it does not say). `lines` use the raw field names listed
   under "Default" below; `subtotals` are only the subtotals the source itself states, using the names listed under
   "Analyst-supplied" below (in the default mode they are used to cross-foot and are never recorded; in the
   analyst-supplied mode they are the figures recorded); `ratios` are analyst-supplied mode only. Leave out anything
   the image does not show; never derive one.

   **Signs.** The framework is given costs, cash outflows and liabilities as positive amounts, and many statements show
   them in brackets. So declare, for each of `cost_sign` (cost of sales, admin expenses, depreciation, amortisation,
   interest paid, exceptional costs, tax), `outflow_sign` (scheduled principal, capex) and `liability_sign` (every
   liability line) whose lines you transcribe, how the source writes an amount of that kind: `"positive"` if as a
   positive number (then a bracketed one is a credit or reversal), `"negative"` if in brackets or with a minus sign
   (then a plain positive one is a credit or reversal). The script records the written figure and, for `"negative"`,
   its reverse, and shows both. Read the convention off the image; if it does not make it plain, **ask the analyst,
   never guess**. Subtotals and ratios keep their own sign as written.
2. **Read it back.** Run the read-back and show its output to the analyst verbatim, without shortening or re-sorting
   it; it reads and writes no deal:
   ```
   python scripts/transcription_check.py --transcription "deals/<company>/<proposal>_transcription.json"
   ```
   It shows the image's SHA-256 fingerprint and each sign convention in words; groups the figures by period and
   statement with each as written beside the value that would be recorded and the interpretation; lists every figure
   recorded as negative; and cross-foots every subtotal the source states against the sum of the recorded lines that
   feed it (the framework's own formulas, within the rounding the written figures allow), plus the balance sheet
   identity. A check shown as NOT ASSESSED was **not** made (a feeding line was not transcribed); say so plainly and
   never describe those figures as verified. Ratios are never cross-footed.
3. **Resolve every MISMATCH before asking for confirmation.** Show the discrepancy, ask the analyst to compare it with
   the image, and fix the transcription only where the image shows it is wrong. Never change a figure to make a check
   pass, and never amend a figure on your own. A mismatch marked "matches if costs were read with the opposite sign"
   is a doubt about a sign declaration: look at the image again and correct the declaration or the figures; it cannot
   be acknowledged. If the source's subtotal legitimately differs from the framework's definition (for example an
   equity line the raw schema has no field for), record the analyst's reason in their own words under `acknowledged`
   (`{"period": ..., "check": ..., "reason": ...}`); an acknowledgement appears in the read-back and is part of what the
   analyst confirms. Then read back again.
4. **Ask for explicit confirmation of exactly what was shown,** including the sign conventions: "Do these figures match
   the image exactly, and is this how it writes its signs?" Only a clear affirmative about these figures is
   confirmation. Silence, a question, a partial answer ("mostly", "looks fine, continue"), moving on to the next step,
   or your own confidence in the transcription is never confirmation. If the analyst corrects any value or a
   convention, edit the staged file, read back again, show the new read-back and ask again; a confirmation never
   carries over to changed figures (the digest changes, and the commit refuses an old one). Replacing or editing the
   image file also changes the digest. If the analyst declines or cancels, stop: run nothing further, delete the
   staged file, and leave the deal exactly as it was.
5. **Commit, only after that explicit confirmation,** quoting the digest of the read-back the analyst confirmed:
   ```
   python scripts/transcription_check.py --transcription "deals/<company>/<proposal>_transcription.json" \
       --commit --confirm <digest> --company "<company>" --proposal "<proposal>" [--source-note "<convention>"]
   ```
   It refuses, with nothing written, if the digest is stale, a discrepancy is unresolved, the image is missing or
   changed, or the deal's existing `financials_source` is the other mode (one deal never mixes the two, and nothing
   here converts it; tell the analyst and stop); report its `error:` line as printed and do not retry with another
   digest unless a new read-back was confirmed. On success it saves the image as the source of record, verified against
   the confirmed fingerprint (do not also run `source_manifest.py` for it), then in the default mode recomputes exactly
   as `spreading_check.py` does, and in the analyst-supplied mode records the subtotals as `financials`, the ratios as
   `ratios` and any lines as `analyst_supplied_financials` exactly as given, sets `financials_source` to
   `"analyst-supplied"` and requires `--source-note` (the confirmed convention description from "Analyst-supplied"
   below), to which it appends a sentence saying the figures were transcribed from an image and stores the result as
   `financials_source_note`, so the CAM's caveat (Guideline 9) carries it. In both modes it appends `"spread"` to
   `steps_completed` and a record of the transcription (with the image's fingerprint) to `financials_transcriptions`.
6. Delete the staged file. In the analyst-supplied mode, still ask the scope question and persist the convention with
   `conventions.py` below, using the plain convention note.

The script cannot see the image: it enforces the order of events, the arithmetic and the refusal, and the analyst's
comparison of the read-back with the image is what makes the figures trustworthy.

### Default: spread from raw P&L/Balance Sheet data

For figures read from an image, follow "Figures read from an image" above instead of the steps below.

Extract the raw line items for each period you have (`"FY-2"`, `"FY-1"`, `"FY-Current"` --
whichever you were given), using these exact field names (omit any field the source data doesn't
have -- it defaults to `0`, never invented): `revenue`, `cost_of_sales`, `admin_expenses`,
`depreciation`, `amortisation`, `other_income`, `interest_paid`, `interest_received`,
`scheduled_principal`, `capex`, `exceptional_costs`, `tax_paid`, `cash`, `trade_debtors`, `stock`,
`other_current_assets`, `tangible_assets`, `intangible_assets`, `other_fixed_assets`,
`trade_creditors`, `other_current_liabilities`, `overdraft`, `current_debt`, `long_term_debt`,
`loan_notes`, `other_long_term_liabilities`, `provisions`, `share_capital`, `retained_profit`.

Write them to a temporary JSON file, e.g. `deals/<company>/<proposal>_financials_input.json`:
```json
{"FY-Current": {"revenue": 0, "cost_of_sales": 0, "...": "..."}}
```
Then run:
```
python scripts/spreading_check.py --company "<company>" --proposal "<proposal>" \
    --financials "deals/<company>/<proposal>_financials_input.json"
```
This computes Tangible Net Worth (TNW), EBITDA, Debt Service Coverage Ratio (DSCR),
EBIT/Interest, Gross Leverage, Net Debt / EBITDA, Gearing %, Current Ratio, FCF Conversion %, and
Working Capital Days the same deterministic way `scripts/orchestrator.py`'s headless pipeline
does (see issue #98), and checkpoints `financials`/`ratios`/`multi_period_financials` straight to
this deal's `state.json` -- never recalculate or adjust any of these figures yourself, and report
exactly what the script's printed output (or a re-read of `state.json`) shows. Delete the
temporary input file afterward -- its content is already preserved in `state.json`'s
`multi_period_financials`.

### Alternative: analyst-supplied pre-spread figures

For figures read from an image, follow "Figures read from an image" above for recording them; the rules below on
recording exactly as given still apply.

If the analyst supplies already-computed subtotals and ratios from their own template rather
than raw line items, **record them exactly as given -- never recompute, adjust, or reconcile
them against this framework's own raw schema.** Don't try to map their line items onto the raw
schema below if their own conventions differ (e.g. Depreciation embedded in Cost of Goods Sold);
trying to force a fit is how a real figure gets silently altered. If they also give you a raw
line-item breakdown, still record it (useful context, and it can still populate the exported
workbook's raw-input cells for whichever labels happen to match), but the subtotals/ratios you
write below always come from what they supplied, not from recalculating those raw lines
yourself. This deliberately skips the audit guarantee the default mode provides (every ratio
independently recomputed from raw inputs) in exchange for respecting the analyst's own
house-approved methodology -- see the `financials_source` flag below, which is what tells
`/assemble` to disclose this tradeoff in the CAM.

Once confirmed, also capture a short free-text description of the convention itself (e.g.
"Depreciation embedded in Cost of Goods Sold") and ask explicitly: **"Is this specific to
`<company>`, or an enterprise-wide convention that applies across every borrower?"** This decides
which store to persist it to in "State: write" below -- never guess or infer the scope.

## Source material

Whenever you're given a source document directly — the P&L/Balance Sheet document itself, or (in
the alternative mode) the analyst's own pre-spread template — save the actual document, not just
a citation to it, to this deal's `sources/` folder:
```
python scripts/source_manifest.py --company "<company>" --proposal "<proposal>" --step spread \
    --claim "<short description, e.g. 'FY2023-2025 P&L and Balance Sheet' or 'Analyst pre-spread template'>" \
    --file <path to the document>
```
This preserves the source of record for later audit (see issue #67) -- so the analyst can spot-
check a figure or re-run a step's numbers by hand against the exact document that was spread,
not just a description of it. (For figures read from an image, `transcription_check.py --commit` saves the image
itself; do not save it twice.) Don't save incidental scratch/intermediate artifacts (e.g. an OCR
page render used only to extract a figure) -- only the source document itself.

## State: write

For figures read from an image, `scripts/transcription_check.py --commit` (see "Figures read from an image") has
already written the figures, the source record and the `spread` step once the analyst confirmed; do not repeat them
below.

**Default mode:** `scripts/spreading_check.py` (run above) already checkpointed `financials`,
`ratios`, `multi_period_financials`, and `financials_source: "framework-computed"` straight to
this deal's `state.json` — nothing left to write for those fields. Just make sure `company`,
`proposal`, `date`, and `deal_type` (if known) are set (`write_state()` keeps `company`/
`proposal`/`date` in sync automatically on any write, including the script's own) and append
`"spread"` to `steps_completed` — see the bottom of this section.

**Analyst-supplied mode:** update this deal's state file with this step's results yourself
(merge with whatever you read above — never drop a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to the file you found.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known), and a `financials` object
  keyed by period (`"FY-2"`, `"FY-1"`, `"FY-Current"` — whichever periods you were given), each
  holding exactly the subtotals the analyst's own template supplied — **use these exact field
  names**, since `ground_truth_figures()` reads this shape as this deal's ground truth for
  whatever the Underwriter later cites (see `agents/underwriter_agent.md`'s Guideline 5):
  ```json
  {
    "gross_profit": 0, "operating_profit": 0, "ebitda": 0, "profit_before_tax": 0,
    "net_profit": 0, "fcf": 0, "current_assets": 0, "current_liabilities": 0, "total_assets": 0,
    "total_liabilities": 0, "total_equity": 0, "total_debt": 0, "tangible_net_worth": 0
  }
  ```
  Omit any subtotal the analyst's own template doesn't separately break out — never back-derive
  one yourself from their other figures.
- If the analyst **also** gave you a raw line-item breakdown alongside their pre-spread figures
  (useful context, even though it's not required): set/update a separate top-level
  `analyst_supplied_financials` object, keyed by the same periods, each holding just the raw
  figures given — **not** nested inside `financials` above, and never back-derived from the
  analyst's own subtotals/ratios:
  ```json
  {
    "revenue": 0, "cost_of_sales": 0, "admin_expenses": 0, "depreciation": 0,
    "amortisation": 0, "other_income": 0, "interest_paid": 0, "interest_received": 0,
    "scheduled_principal": 0, "capex": 0, "exceptional_costs": 0, "tax_paid": 0,
    "cash": 0, "trade_debtors": 0, "stock": 0, "other_current_assets": 0,
    "tangible_assets": 0, "intangible_assets": 0, "other_fixed_assets": 0,
    "trade_creditors": 0, "other_current_liabilities": 0, "overdraft": 0,
    "current_debt": 0, "long_term_debt": 0, "loan_notes": 0,
    "share_capital": 0, "retained_profit": 0
  }
  ```
  `scripts/deal_export.py` reads this (rather than `financials`'s own raw figures, which this mode
  never populates) to fill the exported workbook's raw-input cells for whichever labels happen to
  match, whenever this deal's `financials_source` is `"analyst-supplied"` (see issue #121) — kept
  as its own separate store rather than nested alongside the framework-computed path's own raw
  figures, so the two can never be blended or mistaken for each other.
- Also set a `ratios` object, keyed by the same periods, each holding exactly the values the
  analyst gave you: `dscr`, `gross_leverage`, `net_debt_to_ebitda`, `current_ratio`, `gearing`,
  `ebit_interest_cover`, `ebitda_interest_cover`, `fcf_conversion_pct`.
- Set `financials_source` to `"analyst-supplied"` — the Underwriter reads this during
  `/assemble`'s drafting step to decide whether the CAM needs the analyst-supplied caveat (see
  `agents/underwriter_agent.md`'s Guideline 9). Applies to the whole deal, not per-period — if any
  period's figures were analyst-supplied, set it to `"analyst-supplied"` even if others in this
  same deal were framework-computed via the default mode above.
- Set a new `financials_source_note` field to the confirmed convention description, phrased
  ready for the CAM's caveat — e.g. "Depreciation embedded in Cost of Goods Sold, per prior
  confirmation for this borrower on 2026-01-15" (borrower-specific) or "Depreciation embedded in
  Cost of Goods Sold, per this institution's standing convention" (enterprise-wide) — see
  `agents/underwriter_agent.md`'s Guideline 9.
- Then persist/refresh the confirmed convention so future deals benefit automatically.
  Borrower-specific:
  ```
  python scripts/conventions.py --company "<company>" --write --financials-source analyst-supplied \
      --note "<the confirmed convention description>" --proposal "<proposal>"
  ```
  or, if the analyst said this is an enterprise-wide convention:
  ```
  python scripts/conventions.py --enterprise --write --financials-source analyst-supplied \
      --note "<the confirmed convention description>"
  ```
  Run this every time analyst-supplied mode is confirmed for this deal (new, reconfirmed
  unchanged, or corrected) — `conventions.py` always overwrites the current fields and appends to
  history, so there's no need to first check whether anything actually changed.
- Append `"spread"` to `steps_completed` if it isn't already there.
