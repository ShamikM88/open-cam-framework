---
description: Run the Risk Reviewer ("Checker") agent against a drafted CAM, auditing it before it's finalized (see config/skills_registry.md).
argument-hint: "[paste the draft to review, or leave blank to review the most recent draft in this conversation]"
---

## Task: /review

Read `agents/risk_reviewer_agent.md` and act according to that role for the rest of this task.

Review the CAM draft below. If no draft is given as an argument, review the most recently
drafted CAM content earlier in this conversation.

$ARGUMENTS

Re-verify every financial ratio against the raw inputs, flag any ungrounded assertion or missing
source reference, and challenge any risk mitigant that isn't a concrete, enforceable policy
condition. End with a clear verdict, exactly as specified by the role: **APPROVED** or
**REJECTED**, with specific, actionable revision notes for anything rejected.
