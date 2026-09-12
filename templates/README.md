# Reference templates

This folder holds the framework's checked-in reference templates — the actual files a fork
starts from, and the ones `scripts/orchestrator.py` reads/writes when assembling or auto-saving
a CAM.

```
templates/
├── cam/
│   ├── corporate_credit_cam.md   General corporate lending CAM template
│   └── asset_finance_cam.md      Asset-backed / equipment finance CAM template
├── spreading/
│   └── default_spreading_template.xlsx   Reference financial spreading workbook (blank/formula-only)
└── local/                        Gitignored -- calibrated overrides + auto-saved new-type templates
    └── cam/
        └── <deal_type>_cam.md
```

## `templates/cam/`

Plain Markdown CAM layouts with bracketed `[placeholder]` fields, shipped with the framework and
safe to share across any fork — see the top-level [README](../README.md#templates) for what each
one covers. Never contains real deal data; only ever edited by hand.

## `templates/local/cam/` (gitignored)

Where a per-fork override for a given `--type` lives, always taking precedence over the matching
file in `templates/cam/`. A file appears here two ways:

1. **`scripts/calibrate.py --type <deal_type>`** derives one from your own sample CAMs.
2. **`scripts/orchestrator.py`** auto-saves one from a deal's first draft, the first time a
   `--type` is used that has no template (override or default) yet.

Both cases can carry real, user-specific structure (or, for the auto-saved case, an actual
draft's real content) rather than the generic placeholders the shipped defaults guarantee — that's
why this directory is git-ignored rather than living under `templates/cam/`. Delete a file here
to fall back to the shipped default for that deal type.

## `templates/spreading/`

`default_spreading_template.xlsx` is the **reference copy** of the workbook layout that
`scripts/spreading_builder.py` generates — every raw-input cell is blank and every subtotal/ratio
cell holds the live Excel formula, so you can open it directly to see the exact format
(sheet names, columns, row order, formulas) without running any code. It's a generated
artifact, not hand-maintained — regenerate it whenever `spreading_builder.py`'s layout changes:

```bash
cd scripts
python -c "from spreading_builder import export_to_xlsx; export_to_xlsx('[Company Name]', '../templates/spreading/default_spreading_template.xlsx')"
```

This is also where a user-supplied custom spreading template will live once that's supported
(see the roadmap in the top-level README) — for now, `default_spreading_template.xlsx` is the
only format the framework produces.
