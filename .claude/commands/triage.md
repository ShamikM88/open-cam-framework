---
description: Validate legal identity, UBO structure, and active charges; produce a Go/No-Go screening status (see config/skills_registry.md).
argument-hint: "[company registration number] [credit bureau summary] [charges/mortgages register notes]"
disable-model-invocation: true
---

## Task: /triage

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

**Primary inputs** (ask the user for whatever's missing): Company Registration Number, Credit
Bureau Summary, Mortgages/Charges register.

$ARGUMENTS

Validate the company's legal identity, identify its Parent/UBO structure, verify any active
charges/mortgages against it, and output an initial **Go/No-Go** screening status with your
rationale. Ground every claim in a source the user has provided or a credible public record
(e.g. a companies registry) — never invent a registration detail, charge, or UBO relationship
you cannot source.
