# Reference templates

This folder holds the framework's checked-in reference templates — the actual files a fork
starts from, and the ones `scripts/orchestrator.py` reads/writes when assembling or auto-saving
a CAM.

```
templates/
├── cam/
│   ├── corporate_credit_cam.md   General corporate lending CAM template
│   └── asset_finance_cam.md      Asset-backed / equipment finance CAM template
└── spreading/
    └── default_spreading_template.xlsx   Reference financial spreading workbook (blank/formula-only)
```

## `templates/cam/`

Plain Markdown CAM layouts with bracketed `[placeholder]` fields — see the top-level
[README](../README.md#templates) for what each one covers. New deal types are added here the
same way: either by hand, or auto-saved by `orchestrator.py` the first time a new `--type` is
used for a deal.

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
