"""Synthetic, deterministic guarantor records shared by C4 tests and payload fixtures."""
import copy

from spreading_builder import evaluate_financial_model
from state_manager import entity_period_input_fingerprint

REVIEW_LABEL = "not yet reviewed by an analyst"

RAW_PERIODS = {
    "FY-Current": {
        "revenue": 1200, "cost_of_sales": 500, "admin_expenses": 110,
        "interest_paid": 35, "scheduled_principal": 75, "tax_paid": 45,
        "cash": 65, "trade_debtors": 95, "stock": 100, "trade_creditors": 75,
        "current_debt": 20, "long_term_debt": 310, "share_capital": 100,
        "retained_profit": 220,
    },
    "FY+1": {
        "revenue": 1350, "cost_of_sales": 540, "admin_expenses": 115,
        "interest_paid": 30, "scheduled_principal": 70, "tax_paid": 48,
        "cash": 72, "trade_debtors": 100, "stock": 105, "trade_creditors": 78,
        "current_debt": 18, "long_term_debt": 295, "share_capital": 100,
        "retained_profit": 245,
    },
}

_PERIOD_ENDS = {
    "FY-3": "2022-12-31",
    "FY-2": "2023-12-31",
    "FY-1": "2024-12-31",
    "FY-Current": "2025-12-31",
    "FY+1": "2026-12-31",
    "FY+2": "2027-12-31",
    "FY+3": "2028-12-31",
}


def make_guarantor_record(
    entity_id="synthetic-guarantor",
    guarantee_id="G-C4-01",
    *,
    raw_periods=None,
    analyst_supplied=None,
):
    """Return one current C3-shaped entity record with genuine model fingerprints."""
    raw = copy.deepcopy(RAW_PERIODS if raw_periods is None else raw_periods)
    supplied = copy.deepcopy(analyst_supplied or {})
    model = evaluate_financial_model(raw) if raw else {"financials": {}, "ratios": {}}
    periods = set(raw) | set(supplied)
    record = {
        "entity_id": entity_id,
        "legal_name": f"Synthetic {entity_id} Ltd",
        "role": "guarantor",
        "source": {
            "document": "Synthetic standalone accounts",
            "accounting_basis": "UK GAAP",
            "date": "2026-02-01",
        },
        "period_ends": {period: _PERIOD_ENDS[period] for period in periods},
        "perimeter": {
            "included": True,
            "control_basis": "analyst-confirmed",
            "scope_rationale": "Synthetic standalone guarantor perimeter",
            "confirmed": True,
            "confirmed_on": "2026-10-27",
        },
        "raw_figures_source": "Synthetic source document",
        "multi_period_financials": raw,
        "analyst_supplied_financials": supplied,
        "spread_status": "spread",
        "figures_basis": "recomputed",
        "spread_timestamp": "2026-11-06T12:00:00+00:00",
        "spread_source": "scripts/spreading_check.py --entity-id",
        "analyst_review_status": REVIEW_LABEL,
        "financials": model["financials"],
        "ratios": model["ratios"],
        "spread_fingerprints": {},
        "intercompany_lines": [],
        "guarantee_ids": [guarantee_id] if guarantee_id else [],
    }
    record["spread_fingerprints"] = {
        period: entity_period_input_fingerprint(entity_id, period, record)
        for period in raw
    }
    return record


def guarantor_covenant(entity_id="synthetic-guarantor", *, metric="dscr", threshold=1.25):
    return {
        "metric": metric,
        "type": "minimum" if metric == "dscr" else "maximum",
        "threshold": threshold,
        "basis": "guarantor_group",
        "basis_entity_id": entity_id,
    }


def guarantee_row(guarantee_id="G-C4-01", provider="Synthetic Guarantor"):
    return {
        "guarantee_id": guarantee_id,
        "provider": provider,
        "type": "Corporate Guarantee",
        "amount": 250000,
    }
