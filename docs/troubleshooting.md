# Troubleshooting

Symptoms, what they mean and what to do. Every message quoted here is what the scripts print today; if a message
differs from your screen, the script is right and this page needs updating. The examples use the synthetic deal from
[Workflows](workflows.md).

## First checks

- **Run scripts from the repository root.** Every path (`deals/`, `config/`, `templates/`, `agents/`, `inputs/`) is
  resolved against the working directory. Run from elsewhere and you get file-not-found errors, or, worse, a second
  empty `deals/` folder. Example: `orchestrator.py` started in the wrong directory fails with `FileNotFoundError: ...
  'agents/underwriter_agent.md'`.
- **Use the same `--company` and `--proposal` text every time.** They name the deal's folder; a different spelling is
  a different deal.
- **Read `state.json`.** It is the record of the deal (see the [data model](data-model.md)); if something looks
  wrong, look at what it actually holds before re-running a step.
- **Ask the script.** `python scripts/<name>.py --help` is authoritative for flags.

## Credentials and cost

### `ANTHROPIC_API_KEY not found. Running calibrate.py in --mock mode.`

Expected, not an error. `calibrate.py` has no key, so it wrote placeholder files labelled `(MOCK)` and made no API
call. To calibrate for real, set the key in your own shell (see [Configuration](configuration.md#credentials)) and
run again without `--mock`.

### `orchestrator.py` ends in a traceback ending `Could not resolve authentication method`

The headless pipeline needs `ANTHROPIC_API_KEY` in the environment of the process. With none set it prints
`[1/3] Underwriter Agent drafting CAM for ...` and then fails with a `TypeError` from the Anthropic SDK:

```text
TypeError: "Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set. ..."
```

Nothing was sent: the SDK refuses before making a request, so nothing was billed. Set the variable in the shell you
run the script from (never in a file in this repository), or use the slash commands, which need no key. The
traceback rather than a plain message is a known rough edge.

### A headless run is costing more than expected

Only `calibrate.py` (without `--mock`), `orchestrator.py` and `run_evals.py --live` call the API. The orchestrator
makes one draft call, then an audit call per iteration and a revision call after each rejection; `run_evals.py --live` prints its plan
and refuses to exceed `--max-calls` before it reads your key. The slash commands are covered by your Claude Code plan,
not by API billing.

## State errors

The scripts stop with one `error:` line and exit status 1 rather than guess at a damaged or foreign state file.
Fix the file (or the checkout) and run again; nothing was modified.

| Message begins | Meaning | Fix |
| :--- | :--- | :--- |
| `error: Cannot use deals/.../state.json: "ratios" must be an object keyed by period whose values are objects ..., found list.` | A key the step reads has the wrong type (here a list). The message names every offending key and the type it needs | Correct the key by hand, or restore the file from a backup. Re-running `/spread` rewrites `financials` and `ratios` |
| `error: state.json at ... is corrupted and could not be parsed (...)` | The file is not valid JSON, for instance because a write was interrupted | Restore from a backup or repair the JSON by hand |
| `error: Refusing to write ...: it was written by a newer framework (schema_version 9.0.0) than this one supports (1.1.0); writing would downgrade it. Update this checkout of the framework instead; nothing was changed.` | The deal was written by a newer version of the framework | Update this checkout. Do not edit `schema_version` by hand. Reading such a file still works |

A `schema_version` that is not `MAJOR.MINOR.PATCH` digits (`null`, `1.2`, `v1.1.0`) is refused the same way. The
slash commands that write `state.json` directly with Claude's file tools (`/triage`, `/commercial`, `/collateral`,
`/review`, `/assemble`, `/research`) bypass the downgrade guard, so keep the framework checkout current across the
people and sessions that share a deal.

### `/assemble` stops: `"ok": false`, `missing_steps: ["spread"]`

`state_manager.py --check-steps` found no `spread` in the deal's `steps_completed`. Run `/spread` first. `/triage` and
`/collateral` are deliberately not required.

### A second dated folder appeared for the same deal

A dated folder is found automatically (the most recent one for that company and proposal), so one normally does not.
It is created for a brand-new deal, or on purpose by `orchestrator.py --new-review` for a new annual review. If a
differently spelled company or proposal name created it, move the files together or re-run with the original
spelling.

## Figures

### A ratio shows `null` in `state.json`, or `N/A` in the workbook

Its denominator was zero or negative (negative EBITDA, negative equity, no interest paid, ...). That is a result, not
an error: the ratio is not meaningful, so it is recorded as `null` rather than a misleading number. `policy_check.py`
explains it in the covenant's `reason`, for example `gross leverage not meaningful: EBITDA is negative`. See the
[financial model](financial-model.md#na-and-unresolvable).

### A covenant is `UNRESOLVABLE`

The covenant cannot be tested: its ratio is N/A, its metric is not a computed ratio, its type is not `minimum` or
`maximum`, or its threshold is missing or not a number. The `reason` says which. For the current year this makes the
draft `REJECTED` by code until the figures or the covenant record change. For a forward year it is disclosed but does
not reject (see "Forward results" below).

### A figure in the exported workbook differs from what I see in Excel

The workbook's subtotals and ratios are Excel formulas over the raw input rows, so editing a raw cell changes them,
while `state.json` keeps the figures the framework computed. Compare against `state.json` (or re-run `/spread`), not
against an edited copy.

### Figures read from an image

The checks behind `transcription_check.py` (see [Workflows](workflows.md#figures-read-from-an-image)). Each message is one `error:` line and exit status `1`, and nothing has been written.

| Message starts with | Meaning | Fix |
| :--- | :--- | :--- |
| `error: refused: the confirmation does not match these figures` | `--confirm` is not the digest of the staged figures as they are now: the figures, unit, source or an acknowledgement changed since the read-back that was confirmed, or the digest was mistyped | Read back again, show the analyst the new read-back, and commit only the digest of the one they confirmed |
| `error: refused: unresolved cross-foot discrepancies (FY-Current/gross_profit, ...)` | A subtotal the image states does not equal the sum of the transcribed lines that feed it, within rounding, and no acknowledgement covers it | Compare the discrepancy with the image and correct the staged file where it is wrong; if the source defines the subtotal differently, record the analyst's reason under `acknowledged` for that period and check, then read back again |
| `error: FY-Current.lines.revenue: cannot read '1,5' as a figure` | The text is not a plain number: a comma decimal, a currency symbol, `n/a`, a dash or unbalanced brackets are refused rather than guessed | Write the number as it appears with commas for thousands and a point for decimals; ask the analyst what a dash or `n/a` means; put the currency in `source.unit` |
| `error: ... unknown name(s) [...]` or `unknown key(s) [...]` | A misspelt field in the staged file | Use the raw field names listed in `/spread`, the subtotal and ratio names it lists, and the keys in the example |
| `error: refused: this deal's financials_source is 'analyst-supplied' and this transcription is 'framework-computed'` (or the reverse) | `financials_source` is a whole-deal flag and one deal never mixes the two bases; nothing converts a deal from one to the other. The same refusal, worded "records financial figures but no financials_source", applies to a state whose basis is unknown | Record this under a different proposal, or ask the analyst whether to redo the deal's spreading in one mode; nothing was saved or changed |
| `error: refused: the confirmation does not match these figures ... or the image file itself` | The digest also covers the image's bytes, so replacing or editing the screenshot after the read-back makes the confirmation stale | Read back again and confirm the new read-back |
| `error: source.cost_sign is required because [...] are transcribed` (or `outflow_sign`, `liability_sign`) | A line whose sign the source may present the opposite way to the framework's input convention was transcribed with no declaration of how the source writes it | Read it off the image (`"positive"` or `"negative"`); if it is not plain, ask the analyst |
| A `MISMATCH` marked `matches if costs were read with the opposite sign` | A sign declaration is probably wrong: reading that class the other way makes the subtotal match. An acknowledgement of it is refused | Look at the image again and correct the declaration (or the figure) |
| `error: Refusing to write ...: it was written by a newer framework (schema_version ...)` from `transcription_check.py` | The deal's state is a newer schema than this checkout supports; `state_manager` refuses to downgrade it, and the commit is refused before the image is saved | Update this checkout; nothing was saved or changed |
| `error: refused: the figures cannot be computed against this deal's stored state (TypeError: ...)` | In framework-computed mode the stored state (for example a `stress_assumptions` value applied to a forward year on file) cannot be combined with the new figures | Fix the stored value by hand; nothing was saved or changed |
| `error: refused: this deal's sources/manifest.json cannot be read` / `is not a list` | The deal's source manifest is corrupt, so the image could not be recorded | Fix or restore the file; nothing was saved |
| `error: refused: the saved copy ... does not match the fingerprint` | The copy written into `sources/` differs from the image that was confirmed (a disk problem); no figures were recorded | Delete that file and its entry in `sources/manifest.json`, then read back and commit again |
| `error: refused: analyst-supplied mode needs --source-note` | Analyst-supplied mode records the confirmed convention as `financials_source_note`, and the transcription disclosure is added to it | Pass the convention description the analyst confirmed |
| `error: refused: the source file ... does not exist` | `source.file` does not point at the image | Correct the path; the image is saved from there |

A cross-foot row that reads `NOT ASSESSED: not transcribed: stock, ...` is not a failure and not a pass: the check was not made because those lines were not written down. Transcribe them (an explicit `0` counts) if you want the check, or accept that those figures are not cross-footed. A `MISMATCH` on `balance_sheet_balances` with every other check matching usually means a balance sheet line the framework has no field for (for example other reserves) or a line that was not transcribed as zero.

## Review and rejection

A `REJECTED` verdict has two possible sources: the **code-enforced check** (mandatory, quoted verbatim in the notes)
and the **Risk Reviewer's judgement**. Reasons from the code have fixed wording:

| Reason starts with | Meaning | Fix |
| :--- | :--- | :--- |
| `Missing Required CP <id>: ...` / `Missing Required Condition Subsequent <id>: ...` | The deal's structure requires this condition and the draft's `cp_ids_included` / `cs_ids_included` does not list it | Render the condition in the draft and add its id |
| `Missing or malformed Risk Category: <name>` | A category of the required taxonomy is absent from `risk_categories_covered`, has a status other than `covered` / `not_applicable`, or is `not_applicable` with no justification | Cover it, or mark it `not_applicable` with a justification |
| `Undisclosed Downside Breach <id>: ...` | A covenant that passes in the base case fails or cannot be tested under stress and the draft's `downside_breaches_acknowledged` omits it | Address it in Projections & Sensitivities and list the id |
| `Covenant FAIL: ...` / `Covenant UNRESOLVABLE: ...` | A current-year covenant fails or cannot be tested | Change the draft's facts only if the record is wrong; otherwise the structure (covenant or figures) must change |
| `Unperfected Security: ...`, `Subordinate Ranking: ...`, `Uncharged Asset: ...` | A charge is not `"Perfected"`, does not rank `"First"`, or an asset has no charge | Fix the security record (`/collateral`), or accept the rejection until the security is perfected |
| `Narrative/Ground-Truth Mismatch: reported dscr 2.5 vs computed 2.0` | A figure in `reported_figures` differs from the computed one | Report the computed figure, or omit the metric |
| `UNRESOLVABLE_REPORTED_FIGURE: '<metric>' is not a recognized computed figure` | A reported figure has no computed counterpart (including an N/A ratio) | Omit it or describe it in prose |
| `Missing Narrative Sources: ...` | The draft declares no `sources` | List the sources the narrative rests on |
| `Missing Analyst-Supplied Spreading Disclosure: ...` | The deal's `financials_source` is `analyst-supplied` and the draft does not say so | Add the caveat and set `financials_source_disclosed` |
| `Missing Credit Policy Consideration: ...` | A calibrated policy exists and the draft does not declare it considered it | Draft with the policy in mind and set `credit_policy_considered` |

The block that carries these declarations is the draft's trailing fenced `json` block; if it is missing or malformed
you get a full list of "Missing" reasons at once. The review loop keeps going until `APPROVED`; every iteration is
kept in `review_trail`.

### Forward results

A forward-year covenant `FAIL` or `UNRESOLVABLE` never rejects a draft by itself. It appears in
`policy_state["forward_covenant_results"]` (and the non-`PASS` entries are shown to the model as data). Seeing one in the
output of `policy_check.py` while `compliant` is `true` is correct.

### A loss-making deal is rejected on every iteration

If a current-year covenant is `UNRESOLVABLE` (for example leverage with negative EBITDA), no draft can satisfy the
code-enforced check, and the loop will keep rejecting until the covenant record, the figures or the facility structure
change. Stop the loop and resolve the structure with the analyst.

## Sources and exports

### The step stops about saved sources

`source_manifest.py --check-sources` printed `{"missing_saved_sources": true}`: the step declared citations but saved
no material. Save at least one source document with `source_manifest.py ... --file <path>`, or say explicitly why
none could be saved (for instance a page that blocks downloads).

### The `.docx` is missing a chart, or contains `[Image not embedded: ...]`

A standalone `![caption](path)` line becomes an embedded image only for an existing local PNG, JPEG, GIF or BMP file;
anything else becomes a visible placeholder (`file not found`, `remote URLs aren't supported`, `not a supported image
type`, `could not be read as an image`) and a warning on stderr. Fix the path or format and export again. See
[Outputs](outputs.md).

### A line of header fields runs together in the `.docx`

A single newline is a soft wrap, as in ordinary Markdown. Write distinct fields as a bullet list
(`- **Label:** value`) or as separate paragraphs.

### Part of my draft is missing from the `.docx`

A fenced block tagged `json` is stripped (it is reserved for the structured-output block). Any other fenced block, or
one with another tag, is kept as monospace text.

## Tests and tooling

- **`pytest` rejects its own options.** Install the development requirements first: `pip install -r
  requirements-dev.txt`.
- **A test fails after editing a document.** Documents are checked: relative links and heading anchors resolve, every
  `--flag` shown for a script exists in its `--help`, `Guideline N` references point at real items, and the cited
  `CLAUDE.md` headings exist. The failure names the file and line. See [Testing](testing.md).
- **`check_test_count.py` fails.** The passing-test count in `badges/test-count.json` is out of date: run the whole
  suite, capture the output, and run `python scripts/check_test_count.py pytest_output.txt --write` (never in CI).
- **`mutmut` fails on Windows.** It forks and runs on Linux and macOS only; the weekly mutation workflow runs it on
  Ubuntu.
