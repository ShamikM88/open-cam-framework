<!--
Default template for all PRs, regardless of whether a person, ChatGPT, Claude Code, or another tool drafts it.
When opening a PR through the API or CLI, copy this structure manually; GitHub only inserts it automatically in supported UI flows.
Do not leave placeholders. Keep the AI assistance disclosure as the final section.
-->

## Summary
<!-- What changes for users or maintainers? -->

## Why
<!-- Link the issue and explain the problem being solved. -->

## Changes
<!-- List the meaningful code, tests, docs, and workflow changes. Note important exclusions. -->

## Validation
<!-- Report the latest results for this exact PR head, not an earlier commit or an outdated main run. Link CI runs and record material local checks. -->
- **Tests:**
- **Coverage / assurance gates:** 
- **Other checks:**
- **CI run(s):**

## Safety and scope
<!-- State whether production behavior, public interfaces, required checks, security controls, coverage floors, and mutation scope changed. Do not claim unchanged areas unless verified from the diff. -->
- **Behavior / compatibility:**
- **Assurance controls:**
- **Scope boundary:**

## Risks and follow-up
<!-- Record known limitations, remaining work, and separately tracked follow-ups. Write "None known" only after checking. -->

## Review checklist
- [ ] Branch is based on current `main`; results above correspond to the current head.
- [ ] Tests and checks are reported accurately; no green result is inferred or promised before it exists.
- [ ] Documentation and examples were updated where needed.
- [ ] No assurance gate or scope was weakened without explicit review.
- [ ] Known limitations and follow-ups are stated.

## AI assistance disclosure
<!-- REQUIRED and must remain the final section. Identify each AI product/tool and the exact model/version known to have contributed. If the model identifier is not exposed or cannot be verified, say so rather than guessing. Distinguish implementation/authorship from review or advice. If no AI contributed, say "None". -->
- **Assistant(s) and model(s):** [e.g. ChatGPT (GPT-6), Claude Code (Claude <exact model>), or None]
- **Contribution scope:** [what each did: planning, code, tests, documentation, or review; be specific and don't overstate authorship]
- **Human review:** [Pending / Reviewed by <maintainer>]. The human maintainer owns the final decision to merge.
