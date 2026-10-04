# CLI reference

> **Status.** This page was assembled in the documentation tranche of issue #117 from material that used to live in `CLAUDE.md`, moved with only the textual fixes recorded in the [move ledger](move-ledger.md). A later tranche adds worked examples and explanation around it. The source of truth for behaviour is the code and tests; see the [index](README.md).

The headless Python scripts, which call the Anthropic API directly and need `ANTHROPIC_API_KEY` (see [Configuration](configuration.md#credentials)). The slash commands are described in the [README](../README.md) and, as rules, in `CLAUDE.md`; the scripts that do deterministic work (`spreading_check.py`, `policy_check.py`, `state_manager.py`, ...) are described in the [financial model](financial-model.md) and the [data model](data-model.md). For any flag, `python scripts/<name>.py --help` is authoritative.

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
