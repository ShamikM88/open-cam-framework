<!--
Shared template for every PR, regardless of whether a person, ChatGPT, Claude Code, or another tool drafts it.
For API/CLI-created PRs, copy this structure manually. Do not leave placeholders.
Keep the AI assistance disclosure as the final section. Be concise for small changes; for complex changes,
include traceable acceptance criteria, design trade-offs, docs, and assurance evidence.
-->

## Summary
<!-- What changed for users or maintainers? Keep to a few lines. -->

## Problem and acceptance criteria
<!-- Link the issue/design/incident. For issue-driven or complex work, map each material acceptance criterion to its implementation or validation evidence. For a trivial change, state the immediate goal and keep this short. -->

## Implementation and design decisions
<!-- Summarize meaningful code, tests, and workflow changes. For non-trivial work, explain the key approach, important alternatives/trade-offs, invariants preserved, and any deliberately deferred scope. For straightforward edits, one concise paragraph is enough. -->

## Documentation changes
<!-- Explicitly describe documentation work; do not make reviewers infer it from the code diff. Name changed pages/files and any examples, CLI help, configuration, workflow, prompt, or skill guidance updated. If no docs changed, say "None" and why documentation was not needed. -->
- **Updated:** 
- **Not updated / reason:** 

## Validation and evidence
<!-- All results must correspond to the current PR head. Link actual CI runs, commands, artifacts, benchmarks, or review evidence. Never imply an unrun check passed. -->
- **Tests and regression coverage:** 
- **Coverage / security / assurance gates:** 
- **Mutation testing (when relevant):** 
- **Performance / benchmark evidence (when relevant):** 
- **CI run(s) for current head:** 

## Safety, compatibility, and scope
<!-- Say what changes in user-visible behavior, API/CLI, persisted state/schema, generated artifacts, or operational procedures. State whether required checks, coverage floors, security controls, and mutation scope changed. Only claim something is unchanged after checking the diff. -->
- **Behavior / compatibility / migration:** 
- **Assurance controls:** 
- **Scope boundary:** 

## Risks and follow-up
<!-- Known limitations, rollback/recovery concerns, unresolved review points, and separately tracked follow-ups. Use "None known" only after checking. -->

## Review checklist
- [ ] Before requesting review, apply the [mergeability rubric](../docs/contributing.md#mergeability-rubric).
- [ ] Branch is based on current `main`; the reported results correspond to the current PR head.
- [ ] Issue goals / acceptance criteria are addressed or explicitly deferred with reasons.
- [ ] Documentation changes are listed above, including an explicit reason when no documentation changed.
- [ ] Tests and checks are reported accurately; no green result is inferred or promised before it exists.
- [ ] Compatibility, security, and assurance implications have been considered; no gate was weakened without explicit review.
- [ ] Known limitations and follow-ups are stated.
- [ ] For agent-authored work, the `CLAIM` and `OUTCOME` on issue #245 are linked or named ([Agent ledger](../docs/agent-ledger.md)); otherwise write "not applicable".

## AI assistance disclosure
<!-- REQUIRED and must remain the final section. Identify each AI product/tool and exact model/version known to have contributed (for a Claude Code session, the model the session itself reports). If not exposed or verified, say so; never guess. A tool's own attribution footer (for example Claude Code's "Generated with Claude Code" line) may follow this section, but does not replace it. Distinguish implementation/authorship from planning, documentation, or review. If no AI contributed, say "None". -->
- **Assistant(s) and model(s):** [e.g. ChatGPT (GPT-6), Claude Code (exact model if known), or None]
- **Contribution scope:** [what each did—planning, implementation, tests, documentation, review; be specific and don't overstate authorship]
- **Human review:** [Pending / Reviewed by <maintainer>]. The human maintainer owns the final decision to merge.
