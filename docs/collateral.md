# Collateral analysis

This guide follows the collateral workflow as implemented on `main` after the calculator, workbook, stressed-residual sensitivity and policy-ground-truth changes in issue #266. It describes the inputs and evidence, the interactive `/collateral` route, the deterministic CLI, the recorded results, the workbook and the policy checks. It links to the formula register for the rules rather than restating the formulas.

## What the workflow produces

The calculator turns per-asset inputs and explicitly classified non-asset mitigants into a recorded analysis. It retains full precision and produces per-asset results plus deal-level totals. The workbook uses those stored per-asset results as literal values in its Collateral & Exposure sheet. Since PR [#559](https://github.com/ShamikM88/open-cam-framework/pull/559) merged on 4 November 2026, the policy ground-truth function can also expose the stored per-asset collateral figures and the two deal-level LGD aggregates to the CAM checks.

The collateral entries in the [formula register](formula-register.md#f-049--non-rv-funded-exposure-at-origination) are the authoritative specifications: F-049–F-057 and F-066. The residual-realisation sensitivity is F-067 in the [same register](formula-register.md#f-067--stressed-residual-realisation-sensitivity). Read [Collateral formula conventions](financial-model.md#collateral-formula-conventions-issue-266) for the portfolio assumptions and the distinction between the collateral coverage fraction and the older aggregate coverage percentage.

## Inputs and ownership

The calculator CLI requires a JSON object with an `assets` list and may include a `mitigants` list. The complete payload is validated before anything is written. It is not a copy of the persisted `state.json` object; the recorded shape is documented in the [data model](data-model.md#state-manager-and-statejson).

### Per-asset inputs

| Input | Requirement and ownership |
| --- | --- |
| `asset_id` | Required, unique, stable join key. It must match `[A-Za-z0-9-]{1,32}`. The values `max` and `avg` are reserved and refused because they collide with deal-level LGD keys. Reuse the same ID for the same asset on reruns. |
| `valuation` | Required, finite and non-negative. Use the evidenced valuation or invoice figure; retain the source document in the deal's `sources/` folder through the source-manifest workflow. |
| `down_payment_pct` | Required fraction in the range 0–1, taken from the agreed deal/financing terms. |
| `est_rv_pct` | Required residual-value estimate as a fraction in the range 0–1. **This is a risk-team input.** Do not derive or silently revise the estimate. A value above the financed share (one minus the down-payment fraction) is refused. |
| `lgd_pct` | Optional per-asset LGD fraction in the range 0–1. **This is a risk-team input.** The calculator echoes the given value; it does not estimate LGD. When it is absent for any asset, deal-level maximum and average LGD are N/A rather than zero. |
| `cap_or_model_value`, `non_recovery`, `costs`, `number_of_units` | Optional non-negative inputs; omitted values default to zero. `costs` means enforcement/recovery costs, not general operating costs. |
| `asset_class`, `perfection_status` | Optional non-empty descriptive strings used in the legacy projection/workbook context. |

The analyst supplies and evidences the valuation, deal terms, asset identity and supporting security/charge information. The risk team owns the LGD and estimated RV assumptions; the analyst should record them as given rather than infer them. The command also asks for the deal's PD grade and any bureau/reference-agency score. Those remain user-supplied inputs outside the collateral calculator payload; the framework has no bureau or rating-agency integration and does not adjust them.

### Non-asset mitigants

Each entry in the optional `mitigants` list requires a non-negative `amount` and an explicit boolean `eligible_for_netting`. An optional `mitigant_id` can identify it. Mark a mitigant eligible only when it is available on default and has not already been netted in the financing. The calculator allocates only eligible mitigants against exposure; it reports any excess separately. Missing eligibility is a validation error, not an implicit `false` or `true`.

### Payload outline

The following is a structural example, not a set of credit assumptions. Replace the placeholders with verified deal inputs and include every applicable optional field:

```json
{
  "assets": [
    {
      "asset_id": "AST-001",
      "valuation": 100000,
      "down_payment_pct": 0.2,
      "est_rv_pct": 0.4,
      "lgd_pct": 0.15,
      "asset_class": "HGV"
    }
  ],
  "mitigants": [
    {
      "mitigant_id": "MIT-001",
      "amount": 5000,
      "eligible_for_netting": true
    }
  ]
}
```

Percentage values in this calculator payload are fractions: for example, `0.4` means 40%. This is deliberately distinct from the legacy aggregate collateral-cover percentage, which retains its existing 0–100 convention (KD-2). See F-054 and the KD-2 discussion in the [financial model](financial-model.md#collateral-formula-conventions-issue-266).

## Interactive `/collateral` workflow

1. **Find the deal first.** The command checks the existing dated deal record using `state_manager.py --find`. A missing record is a stop; the command must not create a new deal folder or state file. It offers only the near matches returned by the lookup.
2. **Collect and evidence the inputs.** Confirm each asset's stable ID, valuation/invoice, down payment, risk-team RV and LGD inputs, plus any non-asset mitigants and their eligibility. Record the security package separately, one entry per charge, with the exact values expected by policy code: `perfection_status: "Perfected"` and `ranking: "First"` for a fully perfected first-ranking charge. Other values are treated as a policy gap. Save source documents, not just citations, through `source_manifest.py`.
3. **Prepare one complete payload.** Collect all assets and mitigants in one UTF-8 JSON object outside the repository. Do not calculate or round collateral figures in prose.
4. **Run the deterministic calculator once.** Use the calculator's JSON output as the numeric source of truth. It validates the entire payload before writing.
5. **Use the returned figures.** Explain the unrounded outputs without inventing substitutes. A non-zero exit prints one `error:` line; stop and do not write partial results or manually complete the step marker.

The command's `State: write` block no longer writes any collateral state: the calculator is the sole writer of `collateral`, `collateral_analysis` and the `collateral` step marker (maintainer decision, [#266 comment 6642820954](https://github.com/ShamikM88/open-cam-framework/issues/266#issuecomment-6642820954)); the command writes only the analyst inputs (`inputs.pd`, `inputs.lgd`) and the `security_package`.

## Command-line calculator

The canonical CLI reference is [Collateral check](cli-reference.md#collateral-check). The command requires the deal identity and one input file:

```bash
python scripts/collateral_check.py --company "<company>" --proposal "<proposal>" --input-file "<payload.json>"
```

To read the same JSON payload from standard input, pass `-`:

```bash
python scripts/collateral_check.py --company "<company>" --proposal "<proposal>" --input-file - < "<payload.json>"
```

The script writes nothing until the payload validates and the analysis is computed. On refusal it emits one `error:` line, exits with status 1 and leaves the state file unchanged. On success it records the full analysis, refreshes the backward-compatible `collateral` projection from those results and marks the `collateral` step complete. The printed JSON is the calculation report.

The full stored analysis shape is described in [State manager and state.json](data-model.md#state-manager-and-statejson); this guide intentionally does not reproduce that schema.

## Deal-level figures

The calculator reports these deal-level result keys:

- `total_exposure` and `total_collateral_value`
- `coverage_pct`
- `gross_uncovered_risk` and `net_uncovered_risk`
- `excess_mitigant`
- `collateral_max_lgd_pct` and `collateral_avg_lgd_pct`

They are the aggregate results of the per-asset analysis and eligible mitigants, not separate prose calculations. Missing LGD information can make maximum and average LGD N/A. The precise treatment of each figure, its edge cases and units lives in [F-049–F-057 and F-066](formula-register.md#f-049--non-rv-funded-exposure-at-origination).

## Optional residual-realisation sensitivity

A deal may already carry `stress_assumptions.rv_realisation_pct`. If present, the calculator includes a top-level `stressed` object in its JSON output. The assumption is a finite percentage from 0 to 100, where 100 means full realisation; absent means no stressed output. A value of `0.5` means 0.5%, not 50%.

Only the residual part of collateral value is stressed. The base exposures are unchanged. The reported stress variants cover collateral value, coverage, gross uncovered risk and net uncovered risk. The derived stressed figures are **report-only**: they are not persisted to `state.json`, and the stored stress assumption is not rewritten by the calculator. A malformed or out-of-range assumption is refused with one `error:` line and no write. The rule and worked example are in [F-067](formula-register.md#f-067--stressed-residual-realisation-sensitivity).

## Workbook output

The Collateral & Exposure sheet retains the descriptive/source columns and adds result columns populated with literal values from the recorded analysis, joined by `asset_id`. The current headers are:

| Existing collateral/source columns | Calculator result columns |
| --- | --- |
| Asset Class | Non-RV Funded Exposure |
| Exposure (Rental + RV) | RV Exposure |
| Number of Units | Total Exposure (Inc. RV) |
| Cap / Model Value | Net Exposure |
| Non-Recovery | Coverage % |
| Costs | Uncovered Risk (Gross) |
| Collateral Value | Net Uncovered Risk |
| CV % of Exposure | LGD % |
| Perfection Status | |

The workbook does not reimplement the collateral formulas. Result cells are N/A when there is no analysis, no stable asset ID to join on, an input-digest mismatch, or a figure is N/A under the register rules. The stored results are literal calculator outputs; they are not Excel formulas.

## Policy ground truth after PR #559

PR [#559](https://github.com/ShamikM88/open-cam-framework/pull/559) merged into `main` on 4 November 2026. The policy ground-truth function now reads the stored calculator results and adds per-asset keys in the form `collateral_<asset_id>_<metric>` for the registered metrics: non-RV funded exposure, RV exposure, total exposure, net exposure, collateral value, coverage, gross uncovered risk, LGD and net uncovered risk. It also exposes `collateral_max_lgd_pct` and `collateral_avg_lgd_pct` when those aggregates are available.

This is a handoff of stored numeric results into the policy ground truth; the policy reader does not recalculate the formulas. Both readers (this one and the workbook) apply one freshness rule, `stored_analysis_is_current()`: the stored `calculator_version` must equal the calculator's, the input digest must match the stored inputs and mitigants, and recalculating those inputs with the current calculator must reproduce the stored per-asset and deal-level results exactly. A manual edit to a result, an analysis from another calculator version, a changed input, a malformed object, or an ambiguous asset ID makes the analysis stale: nothing from it is used, and N/A figures are omitted, never defaulted. Readers never repair or rewrite the state; rerun `/collateral` to refresh it. This follows the maintainer's `RECOMPUTE` decision on the [stored-result integrity proposal on #266](https://github.com/ShamikM88/open-cam-framework/issues/266#issuecomment-6642842137).

## Related references

- [Formula register: F-049–F-057, F-066 and F-067](formula-register.md#f-049--non-rv-funded-exposure-at-origination)
- [Financial model: collateral conventions and KD-2](financial-model.md#collateral-formula-conventions-issue-266)
- [Data model: `collateral_analysis` and state keys](data-model.md#state-manager-and-statejson)
- [CLI reference: collateral_check.py](cli-reference.md#collateral-check)
- [Command reference: `/collateral`](commands.md#collateral)
- [Issue #266: collateral design decisions and open points](https://github.com/ShamikM88/open-cam-framework/issues/266)
