# CLI reference

> The sections "Calibrate", "Orchestrator" and "Export" were moved from `CLAUDE.md` (unchanged apart from the fixes in the [move ledger](move-ledger.md)); the rest of the page is written for readers.

Usage reference for every script in `scripts/` that has a command line: what it does, its flags, what it prints, how it exits and whether it needs `ANTHROPIC_API_KEY`. The slash commands are described in the [command reference](commands.md); how the scripts fit together in the [workflows](workflows.md) and the [architecture](architecture.md). `python scripts/<name>.py --help` is authoritative for flags.

## The scripts at a glance

| Script | Does | Needs `ANTHROPIC_API_KEY` | Detail |
| :--- | :--- | :--- | :--- |
| `calibrate.py` | Derives a style guide and a template from your sample PDFs | Yes, unless `--mock` (automatic without a key) | [Calibrate](#calibrate) |
| `orchestrator.py` | The whole deal pipeline in one process: draft, audit, checks, export | Yes | [Orchestrator](#orchestrator) |
| `deal_export.py` | Exports a drafted CAM to `.docx` and `.xlsx` | No | [Export](#export) |
| `research_export.py` | Exports a research brief to `.docx` | No | [Research export](#research-export) |
| `spreading_check.py` | Computes subtotals, ratios and the downside case; checkpoints them | No | [Spreading check](#spreading-check) |
| `transcription_check.py` | Reads back figures transcribed from an image, cross-foots them, and records them only after a confirmed digest | No | [Transcription check](#transcription-check) |
| `policy_check.py` | Computes `policy_state`; audits a draft | No | [Policy check](#policy-check) |
| `state_manager.py` | Checks which steps a deal has completed | No | [State manager](#state-manager) |
| `source_manifest.py` | Saves a source document; checks sources were saved | No | [Source manifest](#source-manifest) |
| `conventions.py` | Reads and writes persisted spreading conventions | No | [Conventions](#conventions) |
| `pii_scan.py` | Scans a template for likely-real data | No | [PII scan](#pii-scan) |
| `check_coverage.py`, `check_test_count.py`, `mutation_report.py` | Test and CI tooling | No | [Test tooling](#test-tooling) |
| `run_evals.py` | The local live-model evaluation harness | Only for `--live` | [Evaluation harness](#evaluation-harness) |

Only the first, second and last scripts can reach the Anthropic API; every other script is deterministic, makes no
network call, and is what the slash commands run in their Bash steps.

## Conventions common to the scripts

- **Run from the repository root.** Paths such as `deals/`, `config/`, `templates/` and `agents/` are relative to the
  working directory.
- **A deal is named by `--company` and `--proposal`.** Together they select `deals/<Company>/<Proposal>_<Date>/`; the
  dated folder is found automatically (the most recent one), so a deal resumed on another day finds its own state.
- **Output.** Scripts that return data print JSON to standard output; export scripts print one `Done!` line with the
  path. Progress and warnings go to standard error.
- **Exit status.** `0` on success. `1` for a failure the script reports itself, including an unusable `state.json`
  (printed as one `error: ...` line, never a traceback; see [Troubleshooting](troubleshooting.md#state-errors)).
  `2` for a command-line usage error (an unknown flag or a missing required one). A few checks have their own rules,
  noted per script; `--check-steps` and `--check-sources` exit `0` whatever their *result*, because the calling command
  decides what a missing step or source means.
- **Text.** Everything is read and written as UTF-8 and the output streams are made UTF-8 even when redirected, so a
  non-ASCII name survives a pipe on Windows.
- **`--help` is authoritative.** The flags below are checked against it by the test suite.

## Calibrate

**`scripts/calibrate.py --type <deal_type>`** (headless; needs `ANTHROPIC_API_KEY`) — reads historical CAM PDFs from
`inputs/calibration_samples/` (in sorted filename order) and makes two Claude calls against that
text (more when the samples overflow one call's limit -- see "Sample length" below): one extracts
writing style/tone into `config/style_guide.md`; the other derives a genericized CAM template
(explicitly instructed to strip all real data to `[placeholder]`s) written to
`templates/local/cam/<deal_type>_cam.md`. `<deal_type>` defaults to `corporate_credit` and
should match the value later passed to `orchestrator.py --type`.
```
python scripts/calibrate.py --type asset_finance
```
**`--mock`**: skips both Anthropic calls and writes deterministic placeholder output instead
(clearly labeled `(MOCK)` in the file content) -- for smoke-testing the PDF-reading and
file-writing plumbing without an API key or network access. It never fabricates anything
domain-specific (no invented PD/LGD/rating figures -- that would contradict the grounding rule
and isn't this script's job in the first place); it only proves the pipeline runs end to end.
Triggers automatically, with a printed notice, whenever `ANTHROPIC_API_KEY` isn't set:
```
python scripts/calibrate.py --mock --type asset_finance
```
**Sample length (issue #109):** each Claude call takes at most `CHUNK_CHAR_LIMIT` (12,000)
characters of sample text, and the combined text of every PDF in `inputs/calibration_samples/`
routinely exceeds that. The length check is the run's first decision point -- after the PDFs
are read (local only) but before any API call or file write -- and offers two choices: *ignore*
(use only the first 12,000 characters, discarding the rest, now stated explicitly) or *split*
(split the text in memory at paragraph/line boundaries into parts, run the style/template prompt
on each, then merge the per-part results with one consolidation call, hierarchically if there
are very many parts; the merge prompts restate the "zero real data, bracketed placeholders"
and editorial-judgment rules). `--on-overflow {ask,split,ignore}` (default `ask`) pre-answers it;
`ask` prompts on a terminal and falls back to `split` when there's no TTY, since silently losing
material is the failure this exists to prevent and splitting only costs extra API calls.
`--mock` makes no calls, so it only reports how many parts a real run would use (it ignores
`--on-overflow`). Whitespace-only parts (e.g. a lone trailing newline) are dropped before
counting or calling, and if only one non-blank part remains nothing is lost, so there's no
prompt. The original sample PDFs are only ever read, never written or split on disk. Both result
files are computed in memory first, with no API call between the two writes at the end, so an API
failure partway through a long split run leaves any existing `config/style_guide.md`/template
untouched. A response cut off at its output-token limit prints a `[WARN]` rather than flowing on
silently. `/calibrate` has no such cap (native PDF reading) and is unaffected.

## Orchestrator

**`scripts/orchestrator.py`** (headless; needs `ANTHROPIC_API_KEY`) — the main pipeline. For a
given `--company`, `--proposal`, `--pd`, `--lgd` and `--type`, it: loads the Maker/Checker
prompts and style guide, resolves the CAM template for `--type` via
`template_resolver.cam_template_path` and includes it in the drafting prompt if one exists,
runs the Underwriter draft and then the Risk Reviewer audit, checkpointing to `state.json` via
`state_manager.write_state` after each of those two agent calls (see the state management protocol in `CLAUDE.md`), then calls `deal_export.export_deal` (see below) to do the
folder-creation/template-auto-save/export work:
```
python scripts/orchestrator.py --company "Acme Corp" --proposal "Fleet Loan" --type "asset_finance"
```

## Orchestrator flags and defaults

`scripts/orchestrator.py --company <name> --proposal <name> [--pd <PD>] [--lgd <LGD>] [--type <deal_type>]
[--financials <json>] [--spread <json>] [--collateral <json>] [--stress-assumptions <json>] [--new-review]`

- `--company` and `--proposal` are required and name the deal.
- `--pd` (default `0.20%`) and `--lgd` (default `LGD 3 (15%)`) are **user-supplied risk inputs** and are used exactly as
  given. The defaults are example values: if you omit the flags they flow into the CAM as if you had supplied them,
  so always pass your own.
- `--type` selects the template and defaults to `corporate_credit`.
- `--financials` is a JSON file of raw line items per period (`FY-2`, `FY-1`, `FY-Current`, `FY+1`, `FY+2`, `FY+3`,
  any subset); `--spread` has the same shape and takes precedence if both are given. Forward years are management's
  own forecast, never derived by the tool.
- `--collateral` is a JSON file holding a flat list of asset objects.
- `--stress-assumptions` is a JSON file of the downside shocks (`revenue_haircut_pct`, `opex_increase_pct`,
  `interest_rate_bump_bps`), applied to the forward years only and ignored if there are none.
- `--new-review` starts a fresh dated folder for a new annual review instead of resuming the most recent one, so it
  never inherits last year's inputs.

It needs `ANTHROPIC_API_KEY`; without one it fails at its first model call (after checkpointing the grounded figures)
with a missing-credentials error and sends nothing. See [Troubleshooting](troubleshooting.md#orchestratorpy-ends-in-a-traceback-ending-could-not-resolve-authentication-method).

## Export

**`scripts/deal_export.py`** — no `anthropic` dependency, so it's callable two ways: imported
directly by `orchestrator.py` (`export_deal(...)`), or run standalone as
`python scripts/deal_export.py --company ... --proposal ... --type ... --draft <path>` from
`/assemble`'s Bash step, since Claude has already produced the draft itself by that point and
just needs it turned into deal output files. `export_deal()` creates the dated
`deals/[Company]/[Proposal]_[Date]/` folder, auto-saves the draft as a new
`templates/local/cam/<type>_cam.md` if that deal type had no template at all yet (never into
the git-tracked `templates/cam/` -- see the confidentiality rule in `CLAUDE.md`), and exports
`.docx`/`.xlsx`. **Which dated folder:** `export_deal()` itself still defaults to today when
`date_str` isn't given (a bare library call has nothing else to go on), so every caller that
has a deal in progress must resolve the date itself: `orchestrator.py` passes the `date_str`
it already resolved at the start of its run, and the CLI takes an optional `--date-str` that
otherwise falls back to `state_manager.resolve_date_str()`'s auto-discovery (the deal's
existing dated folder, most recent wins, else today for a brand-new deal; only a folder named
exactly `<proposal>_<YYYY-MM-DD>` counts, so proposal `Fleet` never claims another deal's
`Fleet_Q2_2026-05-01`, and `--date-str` must itself be a `YYYY-MM-DD` date). Without this a deal
finished days after it started exported into a new folder, disconnected from its own
`state.json`/`sources/`/`draft_v*.md` (issue #97) -- `/assemble` no longer needs a prose
patch to re-home `state.json`.

## Research export

`scripts/research_export.py --company ... --proposal ... --brief <path>` exports a drafted research brief (Markdown)
to `<Company>_<Proposal>_Research_Brief.docx` in the deal's dated folder, reusing the folder that exists or creating
one. It is deliberately separate from `deal_export.py`, so a research-only deal never triggers a CAM's side effects
(no workbook, no template auto-save). It has no Anthropic dependency.

```bash
python scripts/research_export.py --company "Synthetic Co" --proposal "Synthetic Fleet Loan" \
    --brief "deals/Synthetic Co/Synthetic Fleet Loan_brief.md"
```

```text
Done! Research brief exported to deals/Synthetic Co/Synthetic Fleet Loan_2026-10-04/Synthetic Co_Synthetic Fleet Loan_Research_Brief.docx
```

## Spreading check

`scripts/spreading_check.py --company ... --proposal ... [--financials <json>] [--stress-assumptions <json>]
[--no-update-financials-source]` recomputes `financials` and `ratios` from raw line items, derives the downside case
when stress assumptions and at least one forward year exist, checkpoints everything to `state.json` and prints the
updated state (`financials`, `ratios`, `downside_case`, `financials_source`). It merges what it is given into what
the deal already has, two levels deep: a correction to one period's `revenue` keeps that period's other fields, and
re-confirming one stress shock keeps the others. Give `--financials` for new periods and omit it to recompute from
what is already on file. `--no-update-financials-source` stops the call stamping `framework-computed`; `/project`
always passes it. The slash commands `/spread` and `/project` run it; see [Workflows](workflows.md#spreading-the-financial-figures).

```bash
python scripts/spreading_check.py --company "Synthetic Co" --proposal "Synthetic Fleet Loan" \
    --financials "deals/Synthetic Co/Synthetic Fleet Loan_financials_input.json"
```

## Transcription check

`scripts/transcription_check.py --transcription <json> [--json]` reads back figures that `/spread` transcribed from
an image (a screenshot, a photo, a scanned page) and writes nothing: no deal is read or changed. It prints the image's
SHA-256 fingerprint; the sign conventions in words; the figures grouped by period and statement, each as written
beside the value that would be recorded and how one was read as the other; every figure that will be recorded as
negative; a cross-foot of every subtotal the source states against the sum of the recorded lines that feed it; and a
**digest** of exactly what it showed (including the declarations and the image's bytes). `--json` prints the same as
`{digest, source_sha256, report, text}`.

`scripts/transcription_check.py --transcription <json> --commit --confirm <digest> --company ... --proposal ...
[--source-note <text>]` records the transcription, but only if `--confirm` is the digest of these figures and this
image as they are now, no cross-foot discrepancy is unresolved, the deal's existing `financials_source` is the
same mode and the deal's state can be written by this checkout (readable, correctly shaped, not a newer schema
version, a readable `sources/manifest.json`, and in `framework-computed` mode a computation that succeeds against what
is stored); otherwise it exits `1` with one `error: refused: ...` line and writes nothing (no source copied, no state
changed). On success it saves the image as the source of record after checking it against the confirmed fingerprint
(the manifest claim says the figures were transcribed from an image and carries the digest and a fingerprint prefix),
records the figures, appends `spread` to `steps_completed`, and appends a record to `financials_transcriptions`
(holding the fingerprint, never the image). In `framework-computed` mode it recomputes exactly as `spreading_check.py`
does; in `analyst-supplied` mode it records subtotals, ratios and any lines exactly as given and needs `--source-note`,
the confirmed convention, to which it appends a sentence saying the figures were transcribed from an image (stored as
`financials_source_note`, which the CAM caveat quotes). What it enforces and what it cannot is in
[Figures read from an image](financial-model.md#figures-read-from-an-image) and
[AI assurance](ai-assurance.md#what-the-code-guarantees).

The staged file holds every figure as text exactly as it appears, plus the unit and how the source writes the signs
of costs, cash outflows and liabilities; a worked example is in
[Workflows](workflows.md#figures-read-from-an-image).

```bash
python scripts/transcription_check.py --transcription "deals/Synthetic Co/Synthetic Fleet Loan_transcription.json"
```

## Policy check

`scripts/policy_check.py --company ... --proposal ... [--draft <path>]` prints JSON: `policy_state` (required
conditions precedent and subsequent, covenant results, security gaps, downside breaches, forward covenant results),
`reasons` (every reason the draft is code-enforced rejected, empty without `--draft` unless the recorded structure
itself forces one), `compliant`, and `cam_data_present` (false for a deal with no financials, ratios, covenants or
security recorded, i.e. nothing was actually checked, as for a research-only deal). With `--draft` it audits that
file's trailing structured JSON block. It exits `0` whether or not `reasons` is empty (an unusable `state.json` is the exception: one `error:` line, exit `1`); the caller decides what a non-empty `reasons` means.

```bash
python scripts/policy_check.py --company "Synthetic Co" --proposal "Synthetic Fleet Loan" \
    --draft "deals/Synthetic Co/Synthetic Fleet Loan_draft.md"
```

## State manager

`scripts/state_manager.py --check-steps --company ... --proposal ... --required <step,step>` checks that every named
step appears in the deal's `steps_completed` and prints `{"missing_steps": [...], "ok": true|false}`. It exits `0`
whatever the result (an unreadable `state.json` is an error: one `error:` line, exit `1`). `--check-steps` is the only
mode.

```bash
python scripts/state_manager.py --check-steps --company "Synthetic Co" --proposal "Synthetic Fleet Loan" --required spread,collateral
```

## Source manifest

`scripts/source_manifest.py --company ... --proposal ... --step <step> --claim <text> --file <path> [--url <url>]
[--filename <name>]` copies an already-downloaded document into the deal's `sources/` folder and appends an entry
(`filename`, `url`, `step`, `claim`, `fetched_date`) to `sources/manifest.json`; it never fetches anything. With
`--check-sources` instead it prints `{"missing_saved_sources": true|false}`: true when the deal declared citations but
saved no material. It exits `0` whatever the result.

```bash
python scripts/source_manifest.py --company "Synthetic Co" --proposal "Synthetic Fleet Loan" --step research \
    --claim "Synthetic legal identity extract" --file registry_extract.txt --url https://example.invalid/registry
python scripts/source_manifest.py --check-sources --company "Synthetic Co" --proposal "Synthetic Fleet Loan"
```

## Conventions

`scripts/conventions.py (--company <name> | --enterprise) (--read | --write) [--financials-source <value>] [--note
<text>] [--confirmed-date <date>] [--proposal <name>]` reads or writes a persisted, analyst-confirmed spreading
convention: borrower-specific (`--company`, stored at `deals/<Company>/_conventions.json`) or enterprise-wide
(`--enterprise`, `config/spreading_conventions.json`). `--write` requires `--financials-source`; it overwrites the
current fields and appends to a history. `--read` prints `{"found": true|false, "convention": ...}`. It is run by
`/spread` only; the headless pipeline never calls it, because nothing may be persisted without a person's explicit
confirmation. See the [data model](data-model.md).

```bash
python scripts/conventions.py --enterprise --read
```

## PII scan

`scripts/pii_scan.py <path>` scans one template file for likely-real data (currency figures, emails, phone numbers,
dates, company names, registration numbers) and prints JSON findings. An empty list means only that nothing obvious
was found; it is not a guarantee, and a human review is still required before a local template is promoted into
`templates/cam/`. See [Security](security.md).

```bash
python scripts/pii_scan.py templates/local/cam/asset_finance_cam.md
```

## Test tooling

- `scripts/check_coverage.py <coverage.json> [--config <pyproject.toml>] [--report-only]` compares coverage.py's JSON
  report with the floors in `pyproject.toml`; exit `0` when met, `1` when not, `2` for an unreadable report or
  configuration; `--report-only` prints the table and always exits `0`.
- `scripts/check_test_count.py <pytest_output> [--badge-path <path>] [--write]` compares pytest's passed count with
  `badges/test-count.json` (exit `1` on a mismatch); `--write` updates the file from a complete green run (never used
  in CI).
- `scripts/mutation_report.py <results> [--config <pyproject.toml>] [--top <n>] [--json <path>]` prints the
  per-module mutation score from `mutmut results --all true`; exit `0` whenever a report is produced, `2` for an
  unreadable file.

Their rules and the reasoning are in [Testing](testing.md).

## Evaluation harness

`scripts/run_evals.py` validates the synthetic dataset (`--validate`, `--list`), plans and self-checks with zero model
calls (`--dry-run`), and, only with `--live` and your own `ANTHROPIC_API_KEY`, runs the cases under a hard call cap
(`--max-calls`, default 80, never above 250; `--repeats`, `--cases`, `--yes`, `--keep-work`, `--out`), exporting and
comparing baselines (`--export-baseline`, `--allow-partial`, `--compare`). See [Evaluation](evaluation.md).

```bash
python scripts/run_evals.py --dry-run
```
