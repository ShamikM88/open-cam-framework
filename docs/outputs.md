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

The draft is Markdown; `scripts/docx_builder.py` turns it into a Word document you can edit. The rendering rules are
the ones that surprise authors:

| In the Markdown | In the Word document |
| :--- | :--- |
| `#`, `##`, `###` | Heading 1, 2, 3. Deeper levels (`####` to `######`) all become Heading 3 |
| `- item` | a bullet (List Bullet style) |
| `1. item` | an ordinary paragraph that begins `1.` (not an automatic numbered list) |
| a pipe table | a Word table; the header row is bold and `:---:` / `---:` alignment markers are honoured |
| `**bold**`, `*italic*`, `` `code` `` | bold, italic, and monospace text |
| `[text](url)` | a real hyperlink |
| a fenced block (no tag, or any tag except `json`) | monospace lines, whitespace kept exactly (the ownership-tree diagram uses this) |
| a fenced block tagged `json` | **removed**: it is reserved for the Underwriter's structured-output block, never shown to a client |
| `![caption](path.png)` on its own line | an embedded image, scaled down (never up) to the page's text width, with the caption in italics; see below |
| a horizontal rule | a rule |

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

For an analyst-supplied deal the raw-input cells are filled from `analyst_supplied_financials` for whichever labels
match (the subtotals still come from the workbook's own formulas); the CAM carries the caveat that the figures were
not independently recomputed. `templates/spreading/default_spreading_template.xlsx` is a blank, formula-only reference
copy of the layout.

## The research brief

`/research` exports a standalone `.docx` from a Markdown brief (a short header as a bullet list, the Go/No-Go verdict
with its rationale, then the company and sector sections), using the same rendering rules as the CAM. It has no
workbook, no structured JSON block and no template side effects.

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

> **Custom spreading templates:** if your organization already has a standard spreading Excel
> template, that's on the roadmap to support directly (see the roadmap in the README) — it would live alongside the
> default under `templates/spreading/`. For now, `spreading_builder.py`'s layout is the only
> format produced.

## Template resolution

**`scripts/template_resolver.py`** — pure path-resolution logic shared across the scripts and `/assemble` (no `anthropic`/`docx`/`openpyxl` imports, so it's cheap to unit test):
`cam_template_path(deal_type)` checks `templates/local/cam/` first, falls back to
`templates/cam/`, returns `None` if neither exists; `local_cam_template_path(deal_type)`
is always the write target for a new override or auto-saved template.
