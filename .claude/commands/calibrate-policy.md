---
description: Calibrate this institution's own credit policy from a supplied policy document, referenced during every future CAM draft/audit (see scripts/calibrate.py's sibling -- this has no headless equivalent yet).
argument-hint: (no arguments -- org-wide, not per-deal-type)
disable-model-invocation: true
---

## Task: /calibrate-policy

Unlike `/calibrate` (which is per-`--type` -- writing style and a CAM template), this is
org-wide, one-time setup -- closer in spirit to `config/settings.json` than to
`templates/local/cam/<deal_type>_cam.md`.

1. List `inputs/credit_policy/`. If it's empty, tell the user to add their institution's credit
   policy document(s) there (lending criteria, required mitigants, structuring norms, risk
   appetite boundaries) and stop.
2. Read every document in that folder directly (native PDF/text reading — no API key needed).
3. **Extract policy.** Identify the lending criteria, required mitigants, structuring norms, and
   risk appetite boundaries as clearly actionable rules. Write it to `config/credit_policy.md` as
   a `# Institutional Credit Policy (Calibrated)` document.
   **Apply editorial judgment, don't mirror uncritically** — the same principle `/calibrate`
   applies to style extraction: organize and clarify into rules an Underwriter/Risk Reviewer can
   actually check a draft against, don't just mechanically transcribe verbose legal/policy prose
   verbatim.
   **No placeholder-scrubbing needed here** — unlike `/calibrate`'s derived *template* (which may
   end up shared or committed), this file is never promoted or shared; it stays local to this
   fork exactly like `config/style_guide.md` does, so there's no reason to strip out real
   institutional detail the way there is for a template.
4. Confirm the file was written, and remind the user it lives under the git-ignored
   `config/credit_policy.md` and won't be committed.

Once calibrated, every future `/assemble`/`/review` run (and `orchestrator.py`'s headless
pipeline) automatically picks this up: the Underwriter drafts with awareness of it (advisory —
see `agents/underwriter_agent.md`'s Guideline 10), and the Risk Reviewer audits the draft against
it and flags any violation as a REJECTED-worthy finding (mandatory — see
`agents/risk_reviewer_agent.md`'s Audit Checklist item 5). No further action is needed here to
wire a specific deal to it — it applies to every deal in this fork automatically once present.
