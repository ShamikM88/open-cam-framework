# Security and confidentiality

> The sections "Confidentiality — what never belongs in this repo" and "PII scan" were moved from the README and `CLAUDE.md` (unchanged apart from the fixes in the [move ledger](move-ledger.md)); the sections before them were written for readers. The source of truth for behaviour is the code, the workflows and the repository settings; see the [index](README.md).

The operating rule for adding a confidential location, and the rule for promoting a local template upstream, stay in `CLAUDE.md` under "Confidentiality rule for new features"; CI's security job, the advisory-ignore procedure and the workflow rules are in [testing and CI](testing.md). Credentials: [configuration](configuration.md#credentials).

## Reporting a vulnerability or confidential exposure

This repository's issue tracker is public. Read the root [`SECURITY.md`](../SECURITY.md) before reporting a vulnerability, suspected prompt-injection path, or possible disclosure of real data or credentials. Never put the vulnerability details, real material, API keys or other secrets in a public issue, pull request, discussion, screenshot, or CI log.

The intended channel is GitHub private vulnerability reporting (`Security` → `Report a vulnerability`). This is a repository setting that the maintainer must enable; a pull request cannot enable it. If the private-reporting option is unavailable, do not disclose details publicly. Use a private maintainer contact already provided to you, or ask the maintainer—without any sensitive details—to enable the private channel.

## Where the protection comes from

Security here is a few layers, and they are not equally visible. Know which ones are files you can review and which are settings you have to check.

| Layer | What it does | Where it lives | Enforced by |
| :--- | :--- | :--- | :--- |
| **Keep real material out of git** | Protected paths are ignored and never tracked | `.gitignore`, `tests/test_confidential_paths.py` | A test in the required `test` check |
| **Keep the pipeline small and local** | Deterministic scripts with no network use; model calls only in the three scripts that need a key | `scripts/`, `--disable-socket` in the tests | Tests and review |
| **Keep CI least-privilege** | Read-only tokens everywhere except two documented exceptions (the freshness report's `issues: write` and the wiki publisher's one-job `contents: write`), SHA-pinned actions, no `pull_request_target`, no credential left in the checkout | `.github/workflows/`, `tests/test_ci_workflow.py` | A test, and `zizmor` in the `security` job |
| **Know the dependencies** | Weekly Dependabot pull requests; `pip-audit` on every requirements file | `.github/dependabot.yml`, the `security` job | CI |
| **Review every change** | A pull request, the `test`, `test-windows` and `runtime-smoke` checks passing and up to date, no force-push or deletion on `main` | GitHub repository ruleset | GitHub, not a file |
| **Catch secrets and known bug patterns** | Secret scanning with push protection; CodeQL code scanning for Python and for the workflows; Dependabot alerts | GitHub repository settings | GitHub, not a file |

The last two rows are **settings on GitHub, not files in the repository**. No test can see them and a pull request cannot change them. They are the maintainer's to configure and to re-check from time to time; a fork starts without them and should turn on the equivalents.

A green check is evidence for the layer that produced it and nothing else. For example, a passing code-scanning check on a pull request does not prove an alert elsewhere in the code is closed; confirm an alert's state on `main` after the merge ([operations](operations.md#diagnose-a-red-ci-run)).

## What crosses a boundary

| Data or content | Stays local | Leaves the machine | Notes |
| :--- | :--- | :--- | :--- |
| Deal state, sources, generated documents | Yes (`deals/`, git-ignored) | Only what the session or script sends to the model | The model sees what it is given: documents it reads, the figures, the prompts |
| Slash-command sessions | The deterministic scripts and files | What the Claude Code session sends to its model, and the public pages a research step is told to fetch | Covered by your Claude Code plan; no API key |
| Headless scripts | The deterministic scripts and files | The prompts and documents the script sends to the Anthropic API | Billed per token; needs `ANTHROPIC_API_KEY` |
| Tests, CI | Everything | Nothing: no key, no model call, no network in the tests | `--disable-socket` makes a stray connection a failure |
| Evaluation harness | Everything except live calls | Synthetic prompts, only when you run `--live` with your own key | Results stay in git-ignored `evals/results/` |

The two execution modes are compared in full in [Architecture](architecture.md#two-ways-to-run-it); credentials are in [Configuration](configuration.md#credentials).

## Untrusted content

Three kinds of input are written by someone other than the maintainer and reach the model as text: **web pages and filings** a research step fetches, **documents an analyst supplies**, and **free text** the pipeline carries (collateral descriptions, persisted learnings and policy notes, the style guide). Any of them can contain words that look like instructions.

What the design does about that, and what it does not:

- The deterministic checks verify figures, conditions and coverage. They **cannot** tell that a narrative or a rating was manipulated, so a clever source can change the prose without tripping a rule.
- The Underwriter must cite a source for every qualitative claim, and the source document of record is saved beside the deal (`sources/manifest.json`), so a reviewer can read what a claim rested on rather than trust the citation.
- The Risk Reviewer is a separate pass in a context of its own (a separate model call headless; a fresh, read-only sub-agent started by `/review` in a session), so a planted instruction has to get past two passes rather than one. Its context is isolated from the Maker's conversation, not from the documents: it reads the same saved sources and draft, so an instruction embedded in one reaches both passes. This is a design intent, not a measured property.
- The three commands that run Bash through a pre-approved call (`/assemble`, `/review`, `/research`) pre-approve only the named scripts in `allowed-tools` (`/review` also pre-approves starting the `risk-reviewer` sub-agent, whose tools are Read, Glob and Grep only); nothing broader is pre-approved by the command files.
- Several skills also pre-approve narrowly scoped Bash calls: the report skills `information-gaps`, `evidence-discipline`, `financial-analysis` and `policy-fit`, plus the maintainer procedures `cam-change-verification`, `docs-maintenance` and `prompt-change-gate`. Their `allowed-tools` entries pre-approve only the listed script or command forms; `allowed-tools` is not an allowlist and does not sandbox other Bash use.
- A person reads the memo and the Reviewer's notes. This is the control that does not depend on the model.

Whether the agent prompts instruct the model to treat source content as *data, not instructions* is tracked in #150, and whether the models obey is not yet measurable until a baseline exists; check the prompts and the [project status](README.md#project-status), not this page, for the current position. The reasoning is in [AI assurance](ai-assurance.md#why-prompt-hardening-150-waits-for-a-baseline). Until then, treat every external source as untrusted, read the draft against the saved sources, and be suspicious of a memo whose tone or rating departs from its figures.

Model-written Markdown is also untrusted input to the export: an image line can name any local path, so the document builder embeds only a real local png/jpg/gif/bmp, never touches the network, and turns anything else into a visible placeholder; an image path outside the deal's `sources/` folder deserves a second look in review ([outputs](outputs.md)).

The same builder sanitizes XML-invalid control characters in Markdown before writing WordprocessingML, replaces each with U+FFFD, and emits one aggregate warning. This is resilience against malformed text, not a guarantee that the narrative itself is trustworthy. It also creates clickable Word hyperlinks only for `http`, `https` and `mailto` targets. File paths, UNC shares, script/protocol-handler URLs and relative targets stay plain text with a warning to avoid turning untrusted Markdown into an active local or application link.

## If something confidential was committed

Treat a public repository as already read by someone.

1. **Stop and tell the maintainer privately** before doing anything else; follow [`SECURITY.md`](../SECURITY.md). Do not paste the material or the key into an issue, a pull request or a chat.
2. **A key or token:** revoke and rotate it at once. Removing it from git does not make it safe again.
3. **Real data or a document:** the maintainer removes it from the branch and, because forks and clones keep history, decides whether history must be rewritten and whether the people affected must be told.
4. **Close the gap:** add the missing location to `.gitignore` and to `PROTECTED_PATHS` in the same pull request ([contributing](contributing.md#confidentiality-and-credentials)) so a test enforces it from now on.

## Reviewing a change for security

The checklist a contributor and a reviewer use is in [contributing](contributing.md#security-review). The short form: new network, subprocess or path handling; a new dependency; a workflow edit; anything that treats external content as instructions; any new place real material could be written.

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

**`scripts/pii_scan.py`** — no `anthropic` dependency. Heuristic (UK-shaped first: Companies
House-style registration numbers, UK phone formats, `Limited`/`Ltd`/`PLC`/`LLP`/`Inc` company
suffixes; with a short list of other company suffixes -- `LLC`, `Corp`, `Corporation`, `GmbH`, `AG`, `S.A.`,
`S.p.A.`, `B.V.`, `N.V.`, `SARL`, `Pty`, `Pte` -- international (`+` prefix) and North American phone formats, the
US EIN, IBANs, German `HRA`/`HRB` register numbers and the VAT numbers of a few countries (AT, BE, DE, ES, FR, GB, IE,
IT, NL), by shape only, added for issue #111) pattern scan for likely-real currency figures, emails, phone numbers, dates, and
company names left over in a calibrated `templates/local/cam/*.md` override -- the second line
of defense (after a by-hand review) before promoting one upstream to the shared `templates/cam/`
defaults; see the "Promoting a local override upstream" rule in `CLAUDE.md`'s confidentiality section.

Currency and date patterns include the selected ISO codes GBP, USD, EUR, CHF, JPY, CAD and AUD adjacent to amount-shaped numbers; common trailing currency symbols after dot- or space-grouped amounts; the yen symbol before an amount; numeric dates in slash-separated day/month or month/day order and dot-separated day/month order; and full or abbreviated month names in day-first or month-first dates. These checks are intentionally shape-based and narrow. **This is not an international detector**: unsupported currencies, date conventions and other national identifiers may not be recognized.
