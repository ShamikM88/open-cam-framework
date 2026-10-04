# Security and confidentiality

> **Status.** This page was assembled in the documentation tranche of issue #117 from material that used to live in `README.md` and `CLAUDE.md`, moved with only the textual fixes recorded in the [move ledger](move-ledger.md). A later tranche adds worked examples and explanation around it. The source of truth for behaviour is the code and tests; see the [index](README.md).

The operating rule for adding a confidential location, and the rule for promoting a local template upstream, stay in `CLAUDE.md` under "Confidentiality rule for new features"; CI's security job, the advisory-ignore procedure and the workflow rules are in [testing and CI](testing.md). Credentials: [configuration](configuration.md#credentials).

## Confidentiality — what never belongs in this repo

This is a **public, forkable framework repo**, not a place to store real deal data. Everything
under the following paths is git-ignored and must stay that way:

- `inputs/` — your calibration sample PDFs and credit policy documents (real historical CAMs and
  house policy — confidential by nature)

- `config/style_guide.md` — derived from those samples, so treat it the same way

- `config/credit_policy.md` — your calibrated institutional credit policy (`/calibrate-policy`)

- `config/credit_policy_notes.md` — analyst-confirmed corrections to how specific policy clauses
  have been interpreted

- `config/spreading_conventions.json` — analyst-confirmed enterprise-wide spreading conventions

- `config/deal_learnings.md` — analyst-confirmed enterprise-wide end-of-deal takeaways

- `templates/local/` — calibration-derived or auto-saved template overrides; may reflect real
  deal structure even though the shipped defaults in `templates/cam/` never do

- `deals/` — generated output for real borrowers (names, financials, PII) — also holds, one level
  above each dated deal folder, any borrower-specific persisted spreading convention
  (`_conventions.json`) or deal learnings (`_learnings.md`) for that company

- `evals/results/` — output of the local live-model evaluation harness (review packs, full model
  output), kept out of git even though the evaluation data itself is synthetic

This is checked automatically: `tests/test_confidential_paths.py` fails CI if any of these is tracked
by git or loses its `.gitignore` rule.

Only the framework itself (agent prompts, scripts, blank templates, config, docs) should ever
be committed. If you're contributing a template change, make sure every field is a generic
`[bracketed placeholder]` — never a real company name, person's name, or figure.

**Adding a new feature that touches real reference material?** Put its storage location in
`.gitignore` *before* writing anything there — never under a git-tracked path like
`templates/cam/` or `templates/spreading/`. The shipped defaults must stay generic and safe to
share across every fork; anything derived from one user's real documents belongs only in that
fork — *unless* it's a genuinely useful, generalized structure (not just one deal's content) that
other forks would benefit from. Once it's fully scrubbed of real data, that's worth contributing
back: open a PR to add it under `templates/cam/` as a new shared default.

## PII scan

**`scripts/pii_scan.py`** — no `anthropic` dependency. Heuristic (currently UK-shaped: Companies
House-style registration numbers, UK phone formats, `Limited`/`Ltd`/`PLC`/`LLP`/`Inc` company
suffixes) pattern scan for likely-real currency figures, emails, phone numbers, dates, and
company names left over in a calibrated `templates/local/cam/*.md` override -- the second line
of defense (after a by-hand review) before promoting one upstream to the shared `templates/cam/`
defaults; see the "Promoting a local override upstream" rule in `CLAUDE.md`'s confidentiality section.
