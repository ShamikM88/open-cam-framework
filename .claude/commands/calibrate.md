---
description: Calibrate writing style and derive a CAM template from your own sample CAM PDFs, without needing an API key (see scripts/calibrate.py for the headless equivalent).
argument-hint: --type <deal_type>
disable-model-invocation: true
---

## Task: /calibrate

**Arguments:** `--type <deal_type>` (defaults to `corporate_credit` if omitted)

$ARGUMENTS

1. List `inputs/calibration_samples/`. If it's empty, tell the user to add a few of their own
   sample CAM PDFs there and stop.
2. Read every PDF in that folder directly (native PDF reading — no API key needed).
3. **Extract style.** Identify the writing tone, structure, and standard risk phrasing used
   across the samples. Write it to `config/style_guide.md` as a `# Calibrated Style Guide`
   document.
4. **Derive a template.** Analyze the *structure* of the samples (section headings, table
   columns, the order information is presented in) and produce a generic Markdown CAM template
   mirroring that structure exactly.
   **Critical:** this file may end up shared or committed — it must contain **zero** real data
   from the samples. Replace every real company name, person's name, date, or figure with a
   bracketed `[placeholder]` describing what belongs there (e.g. `[Borrower Legal Name]`,
   `[Amount]`, `[PD Grade]`).
5. Write that template to `templates/local/cam/<type>_cam.md` (creating the folder if needed) —
   this overrides the shipped default for that deal type in every future `/assemble` run, until
   the user deletes it.
6. Confirm both files were written, and remind the user this override lives under the
   git-ignored `templates/local/` and won't be committed. If it turns out to be a genuinely
   useful, well-generalized template (not specific to one deal) and they'd like to contribute it
   back, suggest opening a PR to add it under `templates/cam/` as a new shared default — but only
   if they ask for that; never do it automatically.
