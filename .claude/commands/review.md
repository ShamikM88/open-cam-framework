---
description: Run the Risk Reviewer ("Checker") agent against a drafted CAM, auditing it before it's finalized (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [paste the draft to review, or leave blank to review the most recent draft in this conversation]"
allowed-tools: Bash(python scripts/policy_check.py *)
---

## Task: /review

Read `agents/risk_reviewer_agent.md` and act according to that role for the rest of this task.

**Arguments:** `--company "<Name>" --proposal "<Proposal name>"` (needed to checkpoint the
verdict to this deal's state file), followed by the draft to review.

$ARGUMENTS

## State: read

If `--company`/`--proposal` were given, glob `deals/<company>/<proposal>_*/state.json`. If
found, read it — the draft being reviewed should already be consistent with the figures recorded
there; flag it as a finding if it isn't.

Review the CAM draft given above. If none was given as an argument, review the most recently
drafted CAM content earlier in this conversation.

## Code-enforced check (run this before forming your verdict)

If `--company`/`--proposal` were given, run the deterministic check:
1. Save the draft (Markdown and its trailing structured JSON block together) to
   `deals/<company>/<proposal>_draft.md` (create the folder if needed). Skip the write only when
   a file already exists at that path *and* its content already matches the draft being reviewed
   right now (e.g. `/assemble` just wrote this exact draft in the same step) — on a revision
   cycle the draft has changed since it was last saved, so overwrite the file with the current
   content rather than reusing what's there; `policy_check.py` below reads only the file on disk,
   so a stale file means it silently re-checks the previous, already-rejected draft.
2. Run:
   ```
   python scripts/policy_check.py --company "<company>" --proposal "<proposal>" --draft "deals/<company>/<proposal>_draft.md"
   ```
3. This is what `agents/risk_reviewer_agent.md` means by "a `policy_state` block is present" —
   having actually run this script satisfies that condition even though nothing was injected
   into this prompt as text. Do **not** separately re-verify ratios, collateral figures,
   Condition Precedent completeness, or risk-category coverage by hand; the script already did.
4. If its `reasons` list is non-empty: your final verdict **must** be `REJECTED`, regardless of
   your own qualitative read, and your revision notes **must** include every one of those
   reasons verbatim, in addition to your own qualitative findings (do not drop or paraphrase
   them — the Underwriter's revision prompt depends on the exact wording, e.g. `SEC-PERFECT-...`
   `cp_id`s).
5. If `reasons` is empty, your own qualitative judgment governs the verdict entirely — proceed to
   the checklist below.

If `--company`/`--proposal` weren't given (a standalone review with no deal identity), skip this
section entirely: there's nothing to checkpoint or read `state.json` from, so fall back to full
manual verification per `agents/risk_reviewer_agent.md`'s "no `policy_state` block present" path
— re-verify ratios/collateral figures against whatever was supplied in this conversation, and
flag any Condition Precedent or covenant gap you can see yourself.

## Qualitative checklist (always applies)

Flag any ungrounded assertion or missing source reference, and challenge any risk mitigant that
isn't a concrete, enforceable policy condition — per `agents/risk_reviewer_agent.md`'s Audit
Checklist. End with a clear verdict, exactly as specified by the role: **APPROVED** or
**REJECTED**, with specific, actionable revision notes for anything rejected (folding in the
code-enforced reasons above, if any, per step 4).

## Persisting a policy-interpretation correction (optional — only on explicit confirmation)

If your verdict is **REJECTED** specifically because of a credit-policy violation you flagged
under Audit Checklist item 4, and the analyst — in the conversation that follows — corrects your
interpretation of the policy clause rather than agreeing the draft itself needs to change, ask
explicitly: *"Persist this as a standing interpretation note for future deals?"* Only on a clear
**yes**, append an entry to `config/credit_policy_notes.md` (create it with a `# Credit Policy
Interpretation Notes` heading if it doesn't exist yet) recording the date, the company/proposal,
the specific policy point, and the confirmed interpretation, e.g.:
```markdown
## 2026-01-15 — Acme Corp/Fleet Loan
**Policy point:** Key Man risk criterion
**Correction:** Does not apply to committee-managed borrowers.
```
Never write to this file without an explicit yes, and never infer consent from the analyst
simply continuing the conversation or moving on to a revision — see issue #58's "deterministic,
analyst-confirmed, never silently assumed" principle. This is independent of the "State: write"
section below, which always runs regardless of whether a note was persisted here.

## State: write

If `--company`/`--proposal` were given: update this deal's state file (merge with whatever you
read above — never drop a field another step already recorded). If no state file existed yet,
create `deals/<company>/<proposal>_<today's date>/state.json`.

- **`review_trail` is append-only — never replace it.** Take the `review_trail` list you read
  above (an empty list if there wasn't one), and append exactly one new entry to it:
  ```json
  {"iteration": <len(review_trail) + 1>, "verdict": "APPROVED or REJECTED", "notes": "your revision notes, or null if approved with none", "timestamp": "current date/time, best effort"}
  ```
  Write the *whole updated list* back as `review_trail`. This is the deal's actual audit
  history — since `/assemble` loops this command until `APPROVED`, every intermediate
  `REJECTED` verdict and its notes must survive, not just the final one.
- Also set `review_verdict` to this iteration's verdict alone, as a "latest verdict" convenience
  field — `review_trail` above is the source of truth for the full history.
- Append `"review"` to `steps_completed` if it isn't already there.
