# Outputs and templates

> The sections "Templates" and "Template resolution" were moved from the README and `CLAUDE.md` (unchanged apart from the fixes in the [move ledger](move-ledger.md)); the rest of the page is written for readers.

What the framework produces and how: the files in a deal's folder, how Markdown becomes the Word document, the layout of the workbook, the research brief, and the templates that drive drafting.

## What a run produces

For the synthetic deal, after `/assemble` (or the headless orchestrator) the deal's dated folder holds:

```text
deals/
  Synthetic Co/
    Synthetic Fleet Loan_2026-10-04/
      state.json
      Synthetic Co_Synthetic Fleet Loan_CAM.docx
      Synthetic Co_Synthetic Fleet Loan_Spreading.xlsx
      sources/                     (only if a step saved source documents)
        manifest.json
```

A `/research` deal produces `Synthetic Co_Synthetic Fleet Loan_Research_Brief.docx` in the same folder instead of the
two files above. Everything under `deals/` is git-ignored: a deal's output is confidential by nature and never
committed. The folder is the deal's existing dated folder, so the exports sit next to the state they were built from,
however many days the work took. Exporting never changes `state.json`'s figures; it only reads them.

## The CAM document (`.docx`)

The exporter replaces characters forbidden by XML 1.0 (for example NUL, form feed and vertical tab) with the visible replacement character U+FFFD before rendering. It emits one aggregate warning to standard error and continues, rather than losing the whole export.


The draft is Markdown; `scripts/docx_builder.py` turns it into a Word document you can edit. The rendering rules are
the ones that surprise authors:

Body rows wider than the header are preserved by widening the table and emit a warning on standard error. Escape a literal pipe within a cell as `\|` (a single backslash); the exported cell contains `|`.

| In the Markdown | In the Word document |
| :--- | :--- |
| `#`, `##`, `###` | Heading 1, 2, 3. Deeper levels (`####` to `######`) all become Heading 3 |
| `- item` | a bullet (List Bullet style) |
| `1. item` | an ordinary paragraph that begins `1.` (not an automatic numbered list) |
| a pipe table | a Word table; the header row is bold and `:---:` / `---:` alignment markers are honoured |
| `A \\| B` within a table cell | a literal `|` inside one cell |
| `**bold**`, `*italic*`, `` `code` `` | bold, italic, and monospace text |
| `[text](url)` | a real hyperlink only for `http`, `https` or `mailto`; other targets remain visible as plain text with a warning |
| a fenced block (no tag, or any tag except `json`) | monospace lines, whitespace kept exactly (the ownership-tree diagram uses this) |
| a fenced block tagged `json` | **removed**: it is reserved for the Underwriter's structured-output block, never shown to a client |
| `![caption](path.png)` on its own line | an embedded image, scaled down (never up) to the page's text width, with the caption in italics; see below |
| Markdown horizontal rules (`---`, `***`, or `___`) | **Dropped**: no paragraph border or visible rule is emitted |
| `* item` star bullets | Not treated as a list; consecutive lines merge into one Normal paragraph with literal asterisks |
| `  - nested item` | Not rendered as a nested list; the child marker and text are joined to the parent bullet (for example, `Top level - Nested level`) |
| `## Risk **Mitigants**` | Heading style is applied, but inline Markdown is not parsed in headings, so the `**` markers remain literal |

Two behaviours to remember:

- **A single newline is a soft wrap, not a paragraph break**, as in ordinary Markdown. A header block of distinct
  fields must be a bullet list (`- **Label:** value`) or separate paragraphs; a bare run of `**Label:** value` lines
  merges into one paragraph.
- **Images are never silently dropped.** Only a local file of type png, jpg, gif or bmp is embedded. A remote URL (the
  exporter never touches the network), a missing file, an unsupported type such as SVG, or a corrupt file becomes a
  visible `[Image not embedded: <caption> -- <reason>]` paragraph and a warning on standard error, because the Risk
  Reviewer audits the Markdown, where the image line still looks fine. An image reference must be a line of its own,
  and its path is resolved against the working directory.

The exact output of these rules for a synthetic draft is pinned in `tests/snapshots/cam_draft.docx.txt` (and for
images in `cam_images.docx.txt`), so an unintended change shows up as a readable diff. See [Testing](testing.md).

## The spreading workbook (`.xlsx`)

`scripts/spreading_builder.py` writes two sheets.

**Financial Spreading** has one row per line item and one column per period: `Metric`, `FY-2`, `FY-1`, `FY-Current`,
`FY+1`, `FY+2`, `FY+3`, then `FY+1 (Downside)`, `FY+2 (Downside)`, `FY+3 (Downside)`. It runs from Profit & Loss
(Revenue down to Net Profit), through the Balance Sheet, to the key ratios and the working-capital days. Raw inputs
are plain numbers; every subtotal and ratio is an **Excel formula** over those cells, never a pre-baked value, so a
reviewer can click a cell and see how the figure was derived, and change an input to see the effect. A ratio formula
returns `N/A` when its denominator is not positive, matching `state.json`'s `null` (see the
[financial model](financial-model.md#na-and-unresolvable)). A period you did not supply is simply blank.

**Collateral & Exposure** has one row per asset: `Asset Class`, `Exposure (Rental + RV)`, `Number of Units`,
`Cap / Model Value`, `Non-Recovery`, `Costs`, `Collateral Value`, `CV % of Exposure` (a formula) and `Perfection
Status`, filled from the deal's `collateral` list.

For an analyst-supplied historical basis, the historical raw-input cells are filled from
`analyst_supplied_financials`. Forward raw inputs are selected per period: when `forecast_source` is
`analyst-supplied`, the exporter keeps that supplied-forecast path; otherwise the forward raw lines come from
`financials[period]["raw"]`, as recorded by `spreading_check.py`, even if `financials_source` remains
`analyst-supplied` for the historical years. Each period uses one raw store; the exporter never blends the two
stores within a period. This keeps computed `/project` forward columns populated without changing the deal's
historical basis or the analyst-supplied forecast behavior.

When `financials_source` is `analyst-supplied`, recorded historical subtotals and ratios are also written as
values, not recomputed by workbook formulas from potentially incomplete raw lines; the header is labelled
`FY-Current (analyst-supplied)`. A computed row whose figure was not recorded stays blank, while a recorded null ratio
is `N/A`. This preserves the figures in `state.json` and avoids a formula producing a different-looking value.

Separately, the forward years are the exception when the analyst supplied the forecast (`forecast_source`, issue #124):
those columns hold the **supplied** subtotals and ratios as values, blank where none was given, never a formula that
would recompute them from raw cells that were not their basis, and the header says `FY+1 (analyst-supplied)`. A downside
the analyst supplied is labelled `FY+1 (Downside, analyst-supplied)`; one that is unavailable is blank and labelled
`FY+1 (Downside, unavailable)`; one the framework derived from reconciled lines keeps its ordinary header and its
formulas, except that a computed row that reads a raw line the analyst did not supply (following the formulas through
other computed rows) is left blank, never calculated from an empty cell, matching the figures `state.json` omits for it
(no profit before tax without exceptional costs, no total assets without the fixed-asset lines). `total_debt` has no
workbook row. Every other deal's workbook is unchanged; the CAM carries the caveat that the figures were not independently
recomputed. `templates/spreading/default_spreading_template.xlsx` is a blank, formula-only reference copy of the layout.

### Group basis sheet (case A)

When the deal's state contains a valid `group_basis` object, the spreading workbook adds one
`Group basis` sheet. Its first row is exactly:

> Credit aggregation of consolidated accounts supplied by the borrower; not a statutory consolidation prepared by this framework.

The rows below it pair labels with the reporting entity, entities covered, source document and
accounting basis, source document date, common period end, covered period labels, supplied/recomputed
figures basis, and analyst perimeter-confirmation state/date. It discloses metadata only: there are
no consolidated amounts or new calculations on this sheet. A deal without `group_basis` receives
no extra sheet, and the existing Financial Spreading and Collateral & Exposure sheets are unchanged.

## The research brief

`/research` exports a standalone `.docx` from a Markdown brief (a short header as a bullet list, the Go/No-Go verdict
with its rationale, then the company and sector sections), using the same rendering rules as the CAM. It has no
workbook, no structured JSON block and no template side effects.

The export requires a populated final `Sources` or `References` section. For `/research`, keep this as a flat `## Sources` list of every document the brief relies on, include URLs where available, and define short-form citation keys used in the body. The exporter checks for at least one list item or HTTP(S) URL and refuses a missing or empty section before it creates output. A nested list is not suitable because the DOCX builder does not render nested bullets as intended.

Only after the analyst explicitly authorises export without sources in chat and supplies a non-empty reason may the session pass `--analyst-override-no-sources "<reason>"`. That exception appears as a dated final paragraph in the DOCX and is not written to `state.json`; a source-complete brief gets no override footnote.

## Templates in practice

The sections below describe the shipped and calibrated templates and how one is chosen.

## Templates

All reference templates live under [`templates/`](../templates/) — see
[`templates/README.md`](../templates/README.md) for the full breakdown. In short:

- [`templates/cam/corporate_credit_cam.md`](../templates/cam/corporate_credit_cam.md) — general
  corporate lending (RCF/term loan/overdraft): facility terms, covenants, security package, full
  financial spreading, industry/competitive analysis, risks & mitigants.
- [`templates/cam/asset_finance_cam.md`](../templates/cam/asset_finance_cam.md) — asset-backed /
  equipment or fleet finance: adds per-asset LGD/RV/collateral-cover tables and asset-specific
  facility conditions (max asset age, sublet terms, residual value treatment) on top of the same
  financial-analysis and risk sections.
- [`templates/spreading/default_spreading_template.xlsx`](../templates/spreading/default_spreading_template.xlsx) —
  reference copy of the financial spreading workbook layout (see below), blank/formula-only.

The CAM templates are plain Markdown with bracketed `[placeholder]` fields — safe to fork and
edit directly, and readable as a diff in git. New deal types are added the same way (manually,
under `templates/cam/`, or auto-generated by the orchestrator the first time that `--type` is
used).

### Calibrated overrides (`templates/local/`)

`templates/local/cam/<deal_type>_cam.md` — written by `scripts/calibrate.py` (from your own
samples) or auto-saved by `orchestrator.py` (from a genuinely new deal type's first draft) —
always takes precedence over the matching file in `templates/cam/`. It's git-ignored: unlike
the shipped defaults, anything under `templates/local/` may contain structure derived from your
own real, confidential deal history, so it stays local to your fork rather than getting
committed. Delete a file there to fall back to the shipped default for that deal type.

### Financial spreading workbook

[`scripts/spreading_builder.py`](../scripts/spreading_builder.py) builds the `.xlsx` output as a
full P&L → Balance Sheet → key-ratio spread (Revenue down to Working Capital Cycle), with the
subtotals and ratios (Gross Profit, EBITDA, TNW, Gearing, Current Ratio, etc.) written as **Excel
formulas** referencing the raw input rows — not pre-baked numbers — so anyone reviewing the
workbook can see exactly how each figure was derived and re-check it. A second sheet,
`Collateral & Exposure`, gives the same treatment to asset-backed exposure/coverage figures.
`templates/spreading/default_spreading_template.xlsx` is a checked-in, blank reference copy of
that exact layout, so you can inspect the format without running any code.

The Financial Spreading sheet also carries **native Excel charts** below the table: a
leverage chart (Gross Leverage and Net Debt / EBITDA) and a revenue chart, both
clustered column charts. They are
anchored at a cell derived from the table's last row (plus a margin), and every series, legend
title and category label references the workbook's own cells, so the charts always show what
the sheet shows -- nothing is copied in. These two charts cover the historical and forward
base-case columns (B to G) only; they never extend into the downside columns, because a
downside year is a stress of a forward year, not a later period, and joining it to the
base-case line would read as a time path it is not.

When downside figures exist (framework-derived or analyst-supplied, for a period the workbook
does not mark unavailable), a third, separate **downside leverage chart** is added below the
other two. It shows the same two metrics (Gross Leverage and Net Debt / EBITDA), reads the
downside cells (columns H to J) directly, and uses the downside header cells as its own
category labels. There is no downside revenue chart. A downside that is not contiguous (for
example FY+1 and FY+3 but no FY+2) is not truncated: the chart still spans H to J and the
period without figures is left as a gap.

`displayBlanksAs="gap"` is set explicitly on every chart, so a truly blank cell is
configured to render as a gap rather than a zero. A cell whose formula evaluates to the
workbook's `"N/A"` marker (e.g. a zero-EBITDA period's leverage ratios) is not a blank:
in a column chart it draws as an empty slot, a zero-height column, and it is never plotted
as a zero-valued point on a curve. Both axes of every chart are explicitly kept visible
and `varyColors` is off, so the legend names the series rather than one colour per period
(how Excel actually draws these settings is the maintainer's check, re-run after this
change; the configuration is asserted in tests, the drawing is not).

> **Custom spreading templates:** if your organization already has a standard spreading Excel
> template, that's on the roadmap to support directly (see the roadmap in the README) — it would live alongside the
> default under `templates/spreading/`. For now, `spreading_builder.py`'s layout is the only
> format produced.

## Template resolution

**`scripts/template_resolver.py`** — pure path-resolution logic shared across the scripts and `/assemble` (no `anthropic`/`docx`/`openpyxl` imports, so it's cheap to unit test):
`cam_template_path(deal_type)` checks `templates/local/cam/` first, falls back to
`templates/cam/`, returns `None` if neither exists; `local_cam_template_path(deal_type)`
is always the write target for a new override or auto-saved template.
