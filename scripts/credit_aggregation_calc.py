"""Pure B2b calculation under Section A v4.1 and the #472 refusal clarification."""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from math import isfinite
from typing import Any

LABEL = "credit aggregation, not accounting-reviewed"
VARIANT_SUFFIX = "_excluding_other_income"
VARIANT_LABEL = "EBITDA excluding other income (all other income of every participant removed, intragroup and external)"
MEMO_LABEL = "total debt plus debt of left-out entities guaranteed or secured by a participant"
LIMITATIONS = (
    "Debt may be understated: a matching but wrong declaration can remove bank debt; participants may support, "
    "guarantee or secure debt of left-out entities. See the supported-debt memo and guarantees between participants.",
    "Aggregate cash and EBITDA may not be available to the borrower: structural subordination, partial ownership, "
    "restrictions and trapped cash can limit access.",
    "The analyst's attestations are the analyst's own and are not verified; evidence references are not authenticated.",
    "Not a consolidation under IFRS 10 or FRS 102. No accountant reviewed these rules or the output; this says "
    "nothing about whether participant accounts are audited.",
    "Read the participants and ownership, left-out and skipped chain entities, eliminations, debt before and after "
    "(including amount and percentage removed), refused journals, maturity mismatches, attestations and withholdings.",
    "Debt may also be overstated: unjournalled, excluded or refused loans, intragroup lease liabilities and cash "
    "pool positions in overdraft stay in debt. An incorrect attestation may overstate EBITDA. Total debt counts "
    "loan_notes, which may include preference shares.",
    "An aggregation covenant result is indicative, not a compliance test under the facility's own definition.",
    "Events after the reporting date, including repayments, forgiveness, new loans, undrawn facilities, keepwells "
    "and comfort letters, are not reflected.",
    "Not eliminated: investments against equity, goodwill and minorities, intercompany trade balances, sales and "
    "costs, dividends, management charges, unrealised profit, scheduled principal, interest (including accrued), FX and tax.",
    "Trends can mislead when a loan is refused in one period and journalled in another, or when participants change.",
    "A carrying amount net of expected credit losses, or for a below-market loan, can differ and prevent a match.",
)

BORROWER = "@borrower"
PERIMETER_KEYS = {
    "accounts_type", "standalone", "controlled", "controller", "ownership_pct", "control_from", "control_to",
    "status", "period_start", "period_end", "period_months", "framework", "currency", "scale",
    "debt_mapping_uniform", "exceptions", "no_intragroup_stock_or_asset_profit", "cash_pool_in_overdraft",
    "restricted_cash_amount", "income_from_left_out", "charges_to_left_out",
}
GATE_KEYS = {"ebitda_intragroup", "schedule", "unoffset_other_income_amount", "cash_no_group_balance", "no_intragroup_interest"}
DECLARATION_KEYS = {
    "loan_id", "legal_entity", "counterparty_entity_id", "line_key", "direction", "amount", "evidence", "confirmed",
    "of_which_owed_to_group", "separate_loan_confirmed",
}
JOURNAL_KEYS = {
    "journal_id", "type", "loan_id", "lender", "debtor", "lender_line", "debtor_line", "amount",
    "maturity_mismatch_confirmed", "confirmed",
}
RECEIVABLES = {"other_fixed_assets", "other_current_assets"}
DEBTORS = {"current_debt", "long_term_debt"}
DEBT_LINES = ("current_debt", "overdraft", "long_term_debt", "loan_notes")
INCOME_LINES = {"revenue", "other_income"}
COST_LINES = {"cost_of_sales", "admin_expenses"}
RATIO_GATES = ("gross_leverage", "net_debt_to_ebitda", "ebit_interest_cover", "ebitda_interest_cover")


def obj(value: Any, path: str, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    if keys := set(value) - required - (optional or set()):
        raise ValueError(f"{path} has unknown keys: {', '.join(sorted(keys))}")
    if missing := required - set(value):
        raise ValueError(f"{path} is missing: {', '.join(sorted(missing))}")
    return value


def array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list")
    return value


def text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be non-blank text")
    return value


def number(value: Any, path: str, *, minimum: int | None = None) -> Fraction:
    if type(value) not in (int, float) or not isfinite(value) or (minimum is not None and value < minimum):
        raise ValueError(f"{path} must be a finite number of at least {minimum}" if minimum is not None
                         else f"{path} must be a finite number")
    # Exact decimal-value matching and caps, not binary-float addition or a tolerance.
    return Fraction(str(value))


def json_number(value: Fraction) -> int | float:
    return int(value) if value.denominator == 1 else float(value)


def confirmed(value: Any, path: str) -> None:
    if value is not True:
        raise ValueError(f"{path} must be explicitly confirmed")


def check_choice(value: Any, choices: tuple[Any, ...], path: str) -> None:
    if value not in choices or type(value) not in {type(choice) for choice in choices}:
        raise ValueError(f"{path} has an invalid value")


def day(value: Any, path: str) -> str:
    try:
        if date.fromisoformat(text(value, path)).isoformat() == value:
            return value
    except ValueError:
        pass
    raise ValueError(f"{path} must be an ISO date (YYYY-MM-DD)")


def _evidence(value: Any, path: str) -> None:
    for name, item in obj(value, path, {"document", "page_or_note"}).items():
        text(item, f"{path}.{name}")


def _control(node: dict[str, Any], path: str, end: str) -> None:
    if node["controller"] is not None:
        text(node["controller"], f"{path}.controller")
    start = day(node["control_from"], f"{path}.control_from")
    finish = node["control_to"]
    if start > end or (finish is not None and day(finish, f"{path}.control_to") <= end):
        raise ValueError(f"{path}: control not in force at period end")


def _perimeter(request: dict[str, Any], raw: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    participants = set(raw)
    answers = obj(request["attestations"], "attestations", participants)
    skipped = request["skipped_chain_entities"]
    if not isinstance(skipped, dict) or set(skipped) & participants:
        raise ValueError("skipped_chain_entities must be an object with distinct non-participant IDs")
    start, end = None, request["borrower_period_end"]
    for key, att in answers.items():
        path = f"attestations[{key}]"
        a = obj(att, path, PERIMETER_KEYS | {"evidence"}, GATE_KEYS)
        evidence = obj(a["evidence"], f"{path}.evidence", set(a) - {"evidence"}, GATE_KEYS)
        for field, ref in evidence.items():
            text(ref, f"{path}.evidence.{field}")
        confirmed(a["standalone"], f"{path}.standalone")
        confirmed(a["controlled"], f"{path}.controlled")
        check_choice(a["accounts_type"], ("audited", "management"), f"{path}.accounts_type")
        check_choice(a["status"], ("obligor", "guarantor", "neither"), f"{path}.status")
        if number(a["ownership_pct"], f"{path}.ownership_pct", minimum=0) > 100:
            raise ValueError(f"{path}.ownership_pct cannot exceed 100")
        for field in ("period_start", "period_end"):
            day(a[field], f"{path}.{field}")
        if a["period_end"] != end or a["period_start"] > end:
            raise ValueError(f"{path}.period_end / period_start does not match the historical period")
        if type(a["period_months"]) is not int or a["period_months"] < 1:
            raise ValueError(f"{path}.period_months must be a positive integer")
        start = a["period_start"]
        number(a["scale"], f"{path}.scale", minimum=1)
        for field in ("framework", "currency"):
            text(a[field], f"{path}.{field}")
        for field in ("debt_mapping_uniform", "no_intragroup_stock_or_asset_profit",
                      "cash_no_group_balance", "no_intragroup_interest"):
            check_choice(a.get(field), (True, False, None), f"{path}.{field}")
        for exception in array(a["exceptions"], f"{path}.exceptions"):
            text(exception, f"{path}.exceptions")
        for field in ("income_from_left_out", "charges_to_left_out"):
            for line, amount in obj(a[field], f"{path}.{field}", set(), INCOME_LINES | COST_LINES).items():
                number(amount, f"{path}.{field}.{line}", minimum=0)
        _control(a, path, end)
        for field in ("cash_pool_in_overdraft", "restricted_cash_amount"):
            amount = number(a[field], f"{path}.{field}", minimum=0)
            line = "overdraft" if field == "cash_pool_in_overdraft" else "cash"
            if amount and amount > raw[key].get(line, 0):
                raise ValueError(f"{path}.{field} exceeds recorded {line}")
    for key, a in answers.items():
        for field in ("period_start", "period_months", "framework", "currency", "scale"):
            if a[field] != answers[BORROWER][field]:
                raise ValueError(f"attestations[{key}].{field} does not match the borrower")
    for key, node in skipped.items():
        path = f"skipped_chain_entities[{key}]"
        text(key, path)
        obj(node, path, {"controller", "control_from", "control_to", "external_debt", "evidence"})
        number(node["external_debt"], f"{path}.external_debt", minimum=0)
        text(node["evidence"], f"{path}.evidence")
        _control(node, path, end)

    nodes = answers | skipped
    roots, visited = set(), set()
    for key in participants:
        chain: set[str] = set()
        while True:
            if key in chain or key not in nodes:
                raise ValueError(f"broken or cyclic control chain at {key!r}; skipped entities need attested debt")
            chain.add(key)
            parent = nodes[key]["controller"]
            if parent is None:
                roots.add(key)
                break
            key = parent
        visited.update(chain)
    if len(roots) != 1 or not roots <= participants or visited != set(nodes):
        raise ValueError("control chain must connect every participant and skipped entity through one selected root")
    if any(nodes[key]["control_from"] > start for key in visited):
        return answers, "Control began within the period; only period-end balances are comparable."
    if answers[BORROWER]["period_months"] != 12:
        return answers, "EBITDA is not annualised; the common period is not 12 months."
    if any(a["debt_mapping_uniform"] is not True or a["exceptions"] for a in answers.values()):
        return answers, "Uniform debt mapping and EBITDA accounting policies were not attested."
    return answers, None


def _declarations(request, inputs, raw, legal_names):
    sides, caps, alike = {}, {}, defaultdict(list)
    pairs = {}
    for entity, values in inputs.items():
        lines = request["declarations"] if entity == BORROWER else values["intercompany_lines"]
        for index, item in enumerate(array(lines, f"{entity} declarations")):
            path = f"{entity} declaration[{index}]"
            d = obj(item, path, DECLARATION_KEYS, {"period"} if entity != BORROWER else None)
            loan = text(d["loan_id"], f"{path}.loan_id")
            if (entity, loan) in sides:
                raise ValueError(f"{path}: duplicate loan_id {loan!r} for a participant")
            name = text(d["legal_entity"], f"{path}.legal_entity")
            if legal_names[entity] is not None and name != legal_names[entity]:
                raise ValueError(f"{path}.legal_entity does not match the participant")
            other = text(d["counterparty_entity_id"], f"{path}.counterparty_entity_id")
            if other == entity or other not in raw:
                raise ValueError(f"{path}: counterparty must be a different participant")
            pair = frozenset((entity, other))
            if pairs.setdefault(loan, pair) != pair:
                raise ValueError(f"{path}: duplicate loan_id {loan!r} across different party pairs")
            line = text(d["line_key"], f"{path}.line_key")
            check_choice(d["direction"], ("receivable", "payable"), f"{path}.direction")
            confirmed(d["confirmed"], f"{path}.confirmed")
            check_choice(d["separate_loan_confirmed"], (True, False), f"{path}.separate_loan_confirmed")
            _evidence(d["evidence"], f"{path}.evidence")
            amount = number(d["amount"], f"{path}.amount", minimum=0)
            if not amount:
                raise ValueError(f"{path}.amount must be positive")
            cap = number(d["of_which_owed_to_group"], f"{path} cap", minimum=0)
            if cap > raw[entity].get(line, 0) or caps.setdefault((entity, line), cap) != cap:
                raise ValueError(f"{path}: cap exceeds the participant's line or conflicts with another declaration")
            sides[entity, loan] = d
            alike[entity, other, line, d["direction"], amount].append(d["separate_loan_confirmed"])
    if any(len(flags) > 1 and not all(flags) for flags in alike.values()):
        raise ValueError("equal declarations require confirmation as separate loans on both sides")
    return sides, caps


def _apply_journals(request, sides, caps, raw):
    applied, refused = [], []
    seen_journals, seen_loans = set(), set()
    used = defaultdict(Fraction)
    for j in array(request["journals"], "journals"):
        j = obj(j, "journal", JOURNAL_KEYS)
        jid = text(j["journal_id"], "journal_id")
        loan = text(j["loan_id"], "loan_id")
        if jid in seen_journals or loan in seen_loans:
            raise ValueError("duplicate journal_id or reused loan_id")
        seen_journals.add(jid)
        seen_loans.add(loan)
        check_choice(j["type"], ("loan_principal",), "journal type (only loan_principal)")
        confirmed(j["confirmed"], f"journal {jid} confirmed")
        check_choice(j["maturity_mismatch_confirmed"], (True, False), "maturity_mismatch_confirmed")
        lender, debtor = (text(j[k], k) for k in ("lender", "debtor"))
        if lender == debtor or any(p not in raw for p in (lender, debtor)):
            raise ValueError(f"journal {jid} must name two different participants")
        lend_line, debt_line = (text(j[k], k) for k in ("lender_line", "debtor_line"))
        amount = number(j["amount"], f"journal {jid} amount", minimum=0)
        if not amount:
            raise ValueError(f"journal {jid} amount must be positive")

        left, right = sides.get((lender, loan)), sides.get((debtor, loan))
        if BORROWER in (lender, debtor):
            counterpart = debtor if lender == BORROWER else lender
            if not any(owner == BORROWER and d["counterparty_entity_id"] == counterpart
                       for (owner, _), d in sides.items()):
                raise ValueError(f"journal {jid} has no deal-borrower declaration")
        for entity, line in ((lender, lend_line), (debtor, debt_line)):
            if (entity, line) in caps:
                used[entity, line] += amount
                if used[entity, line] > caps[entity, line]:
                    raise ValueError(f"journal {jid}: cumulative cap exceeded on {entity}.{line}")
        mismatch = (lend_line == "other_current_assets") != (debt_line == "current_debt")
        reason = None
        if lend_line not in RECEIVABLES or debt_line not in DEBTORS:
            reason = "lender or debtor line is outside the permitted loan-principal line pairs"
        elif left is None or right is None:
            reason = "loan_id does not match two confirmed declarations"
        elif ((left["counterparty_entity_id"], left["line_key"], left["direction"]) != (debtor, lend_line, "receivable")
              or (right["counterparty_entity_id"], right["line_key"], right["direction"]) != (lender, debt_line, "payable")):
            reason = "counterparty, line or direction does not match both declarations"
        elif any(number(d["amount"], "declaration amount") != amount for d in (left, right)):
            reason = "journal amount differs from one or both declared principal carrying amounts"
        elif mismatch and not j["maturity_mismatch_confirmed"]:
            reason = "maturity mismatch was not confirmed"
        if reason:
            refused.append({**j, "reason": reason})
            continue
        before_lend, before_debt = raw[lender][lend_line], raw[debtor][debt_line]
        raw[lender][lend_line] -= amount
        raw[debtor][debt_line] -= amount
        applied.append({**j, "maturity_mismatch": mismatch,
                        "lender_before": json_number(before_lend), "lender_after": json_number(raw[lender][lend_line]),
                        "debtor_before": json_number(before_debt), "debtor_after": json_number(raw[debtor][debt_line])})
    return applied, refused


def _ebitda_gate(answers, raw):
    modes, income, costs = {}, Counter(), Counter()
    unoffset = {}
    for key, a in answers.items():
        mode = a.get("ebitda_intragroup")
        check_choice(mode, ("none", "offset", "offset_other_income_only", "not_offset", None), "ebitda_intragroup")
        modes[key] = mode
        unoffset[key] = number(a.get("unoffset_other_income_amount", 0), "unoffset_other_income_amount", minimum=0)
        rows = array(a.get("schedule", []), f"{key}.schedule")
        for row in rows:
            r = obj(row, f"{key}.schedule row", {"counterparty", "line", "amount"})
            cp = text(r["counterparty"], "schedule counterparty")
            line = text(r["line"], "schedule line")
            amount = number(r["amount"], "schedule amount", minimum=0)
            if cp == key or cp not in answers or line not in INCOME_LINES | COST_LINES or not amount:
                raise ValueError(f"{key}.schedule row has an invalid participant, EBITDA line or amount")
            if line in INCOME_LINES:
                income[key, cp, amount, line] += 1
            else:
                costs[cp, key, amount] += 1
    if any(mode in (None, "not_offset") or answers[key]["no_intragroup_stock_or_asset_profit"] is not True
           for key, mode in modes.items()):
        return False, "Intragroup EBITDA offsets or absence of intragroup profit in assets were not attested."
    if any(mode == "offset_other_income_only" and "unoffset_other_income_amount" not in answers[key]
           for key, mode in modes.items()):
        return False, "The unoffset other income amount was not stated."
    if any((a.get("schedule") or unoffset[key]) if mode == "none" else not a.get("schedule")
           for key, mode in modes.items() for a in (answers[key],)):
        return False, "The intragroup EBITDA schedule does not reconcile with the attested mode."
    remainder = defaultdict(Fraction)
    # Match revenue first. If either income line could match the same expense, only a remaining
    # other_income row qualifies for the variant. Unmatched costs or revenue always withhold.
    for (owner, cp, amount, line), count in sorted(income.items(), key=lambda row: row[0][3] != "revenue"):
        pair = owner, cp, amount
        matches = min(count, costs[pair])
        costs[pair] -= matches
        if count > matches:
            if modes[owner] != "offset_other_income_only" or line != "other_income":
                return False, "The intragroup EBITDA schedule does not reconcile across counterparties."
            remainder[owner] += amount * (count - matches)
    if any(costs.values()) or any(remainder[key] != u for key, u in unoffset.items()):
        return False, "The intragroup EBITDA schedule does not reconcile to unoffset other income."
    variant = "offset_other_income_only" in modes.values()
    if variant and any(raw[key].get("other_income", 0) < u for key, u in unoffset.items()):
        return False, "Other income less the attested unoffset amount is negative."
    return variant, None


def _left_out_memo(request, participants, debt):
    left_out = request["left_out"]
    if not isinstance(left_out, dict) or set(left_out) & participants:
        raise ValueError("left_out must be an object keyed by non-participant IDs")
    covered, problem = {}, None
    for entity, item in sorted(left_out.items()):
        text(entity, "left_out entity ID")
        item = obj(item, f"left_out[{entity}]", {"reason", "external_debt", "support"})
        text(item["reason"], "left-out reason")
        external = item["external_debt"]
        if external not in (None, "unknown"):
            external = number(external, "left-out external_debt", minimum=0)
        debts = {}
        for row in array(item["support"], "left-out support"):
            r = obj(row, "support", {"participant", "type", "debt_id", "covered_principal", "evidence"})
            if text(r["participant"], "support participant") not in participants:
                raise ValueError("left-out support must be provided by a participant")
            check_choice(r["type"], ("guarantee", "security"), "support type")
            text(r["evidence"], "support evidence")
            identifier = r["debt_id"]
            amount = r["covered_principal"]
            if identifier is not None:
                text(identifier, "covered debt_id")
            if amount in (None, "unknown"):
                problem = f"covered principal is unknown for {entity}"
            else:
                amount = number(amount, "covered principal", minimum=0)
                if identifier is None:
                    problem = f"overlap cannot be established for {entity} without an attested debt_id"
                elif (covered.setdefault(identifier, amount) != amount
                      or debts.setdefault(identifier, amount) != amount):
                    problem = f"conflicting covered principal for {entity} debt {identifier}"
        entity_total = sum(debts.values())
        if isinstance(external, Fraction) and entity_total > external:
            problem = f"covered principal conflicts with external debt for {entity}"
    for row in array(request["participant_guarantees"], "participant_guarantees"):
        r = obj(row, "participant guarantee", {"provider", "beneficiary", "amount", "evidence"})
        provider, beneficiary = (text(r[k], f"guarantee {k}") for k in ("provider", "beneficiary"))
        if provider == beneficiary or {provider, beneficiary} - participants:
            raise ValueError("a participant guarantee needs two different participants")
        text(r["evidence"], "guarantee evidence")
        if r["amount"] not in (None, "unknown"):
            number(r["amount"], "guarantee amount", minimum=0)
    return {"value": "unknown" if problem else json_number(debt + sum(covered.values())), "reason": problem}


def calculate_period(period, request, inputs, legal_names):
    from spreading_builder import FIELD_LABELS, evaluate_financial_model

    raw = {}
    for key, item in inputs.items():
        raw[key] = {line: number(value, f"{key}.{line}") for line, value in item["raw_period"].items()
                    if line in FIELD_LABELS and value is not None}

    def combined():
        return {line: json_number(sum((r.get(line, Fraction()) for r in raw.values()), Fraction()))
                for line in sorted(set().union(*raw.values()))}

    def evaluate(figures):
        result = evaluate_financial_model({period: figures})
        return result["financials"][period], result["ratios"][period]

    answers, perimeter_reason = _perimeter(request, raw)
    sides, caps = _declarations(request, inputs, raw, legal_names)
    before = combined()
    debt_before = evaluate(before)[0]["total_debt"]
    applied, refused = _apply_journals(request, sides, caps, raw)
    adjusted = combined()
    variant, ebitda_reason = _ebitda_gate(answers, raw)
    ebitda_reason = perimeter_reason or ebitda_reason
    cash_ok = all(a.get("cash_no_group_balance") is True for a in answers.values())
    restricted = json_number(sum((number(a["restricted_cash_amount"], "restricted cash") for a in answers.values()), Fraction()))
    cash = adjusted.get("cash", 0)
    unrestricted = json_number(number(cash, "cash") - number(restricted, "restricted cash")) if cash_ok else None
    interest_ok = all(a.get("no_intragroup_interest") is True for a in answers.values())
    ev_fin, ev_rat = evaluate(adjusted)
    if variant or (cash_ok and restricted):
        ev_fin, ev_rat = evaluate(adjusted | ({"other_income": 0} if variant else {}) |
                                  ({"cash": unrestricted} if cash_ok else {}))
    debt = ev_fin["total_debt"]
    fin = dict.fromkeys(k for k in ev_fin if k != "raw")
    ratios = dict.fromkeys(ev_rat)
    withheld = {k: "Outside the closed v1 metric list; other group adjustments are not made."
                for k in (*fin, *ratios)}
    for metric in ("tangible_net_worth", "total_equity", "gearing"):
        withheld[metric] = "Investments against equity are not eliminated in v1."
    for metric in ("dscr", "cash_flow_dscr"):
        withheld[metric] = "Intercompany scheduled principal and working-capital movements are not eliminated."
    fin["total_debt"] = debt
    withheld.pop("total_debt")
    for metric in ("ebitda", *RATIO_GATES):
        store, source = (fin, ev_fin) if metric == "ebitda" else (ratios, ev_rat)
        reason = ebitda_reason
        if metric == "net_debt_to_ebitda" and not cash_ok:
            reason = reason or "Qualifying cash free of group or related-party balances was not attested."
        if metric.endswith("interest_cover") and not interest_ok:
            reason = reason or "Absence of intragroup interest was not attested."
        if not reason and source[metric] is None:
            denom = "gross interest paid" if metric.endswith("interest_cover") else "EBITDA"
            reason = f"{denom} is zero or negative."
        key = metric + VARIANT_SUFFIX if variant else metric
        if variant:
            withheld[metric] = "Only the excluding-other-income variant is available on this basis."
        store[key] = None if reason else source[metric]
        if reason:
            withheld[key] = reason
        else:
            withheld.pop(key, None)
    table = []
    for key, values in raw.items():
        p_raw = {line: json_number(value) for line, value in values.items()}
        figures, _ = evaluate(p_raw | ({"other_income": 0} if variant else {}))
        table.append({"participant": key, "legal_entity": legal_names[key], "status": answers[key]["status"],
                      "ownership_pct": answers[key]["ownership_pct"], "total_debt": figures["total_debt"],
                      "cash": p_raw.get("cash", 0), "ebitda": None if ebitda_reason else figures["ebitda"]})
    eliminated = json_number(sum((number(j["amount"], "eliminated principal") for j in applied), Fraction()))
    return {
        "financials": fin, "ratios": ratios, "withheld": withheld, "adjusted_raw": adjusted,
        "label": LABEL, "limitations": list(LIMITATIONS), "metric_label": VARIANT_LABEL if variant else None,
        "participant_table": table, "eliminations_applied": applied, "refused_journals": refused,
        "debt_before": debt_before, "debt_eliminated": eliminated,
        "debt_eliminated_pct": eliminated / debt_before * 100 if debt_before > 0 else None,
        "debt_lines": {k: {"before": before.get(k, 0), "after": adjusted.get(k, 0)} for k in DEBT_LINES},
        "debt_memo": _left_out_memo(request, set(raw), number(debt, "total debt")),
        "cash": {"total": cash if cash_ok else None, "restricted": restricted, "unrestricted": unrestricted},
        "group_net_debt": json_number(number(debt, "total debt") - number(unrestricted, "unrestricted cash"))
        if cash_ok and ebitda_reason is None else None,
        "attestations": answers, "left_out": request["left_out"], "skipped_chain_entities": request["skipped_chain_entities"],
        "participant_guarantees": request["participant_guarantees"],
    }
