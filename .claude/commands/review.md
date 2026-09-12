---
description: Run the Risk Reviewer ("Checker") agent against a drafted CAM, auditing it before it's finalized (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [paste the draft to review, or leave blank to review the most recent draft in this conversation]"
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

Re-verify every financial ratio against the raw inputs, flag any ungrounded assertion or missing
source reference, and challenge any risk mitigant that isn't a concrete, enforceable policy
condition. End with a clear verdict, exactly as specified by the role: **APPROVED** or
**REJECTED**, with specific, actionable revision notes for anything rejected.

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
