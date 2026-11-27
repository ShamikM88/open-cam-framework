"""Pure B2b calculation under Section A v4.1 and the #472 refusal clarification."""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from fractions import Fraction
from math import gcd, isfinite
from typing import Any, NotRequired, TypedDict

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
# The declared length in months must agree with the declared dates (maintainer decision on #472): the span is checked
# against whole months of 30.4375 days with a tolerance that keeps 52- and 53-week years valid.
MAX_SCALED_AMOUNT = 10 ** 300      # a sanity bound so a ratio cannot overflow a float, not a precision limit
DAYS_PER_MONTH = 30.4375
PERIOD_TOLERANCE_DAYS = 7
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
        # A gate answer left null counts as not supplied: it withholds the metrics and needs no evidence reference.
        supplied = {k for k in a if k != "evidence" and not (k in GATE_KEYS and a[k] is None)}
        evidence = obj(a["evidence"], f"{path}.evidence", supplied, GATE_KEYS)
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
    for key, a in answers.items():
        span_days = (date.fromisoformat(a["period_end"]) - date.fromisoformat(a["period_start"])).days + 1
        if abs(span_days - a["period_months"] * DAYS_PER_MONTH) > PERIOD_TOLERANCE_DAYS:
            raise ValueError(
                f"attestations[{key}].period_months ({a['period_months']}) does not match the declared dates "
                f"{a['period_start']} to {a['period_end']} ({span_days} days)")
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
    claimed_borrower = set()      # borrower declarations already used by a journal: one declaration backs one loan
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
            if not any(key[0] == BORROWER and d["counterparty_entity_id"] == counterpart
                       for key, d in sides.items()):
                raise ValueError(f"journal {jid} has no deal-borrower declaration for {counterpart}")
            # Only the declaration for this journal's own loan is claimed. A journal whose loan_id has none is a
            # matching refusal below, and it never consumes another loan's declaration (CG628-12 / PX628-114).
            if (BORROWER, loan) in sides:
                claimed_borrower.add((BORROWER, loan))
        # Every presented journal is charged against the participant's declared line for this loan as well as the
        # line it names, so a journal that names other (valid) lines and is later refused on matching cannot leave
        # the declared cap uncharged (CG628-15).
        charged = {(lender, lend_line), (debtor, debt_line)}
        charged |= {(entity, d["line_key"]) for entity, d in ((lender, left), (debtor, right)) if d is not None}
        for entity, line in sorted(charged):
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
    scheduled = defaultdict(Fraction)    # (participant, line) -> amount the participant's own schedule rows cite
    unoffset = {}
    for key, a in answers.items():
        mode = a.get("ebitda_intragroup")
        check_choice(mode, ("none", "offset", "offset_other_income_only", "not_offset", None), "ebitda_intragroup")
        modes[key] = mode
        unoffset_answer = a.get("unoffset_other_income_amount")
        unoffset[key] = number(0 if unoffset_answer is None else unoffset_answer, "unoffset_other_income_amount",
                               minimum=0)
        rows = array([] if a.get("schedule") is None else a["schedule"], f"{key}.schedule")
        for row in rows:
            r = obj(row, f"{key}.schedule row", {"counterparty", "line", "amount"})
            cp = text(r["counterparty"], "schedule counterparty")
            line = text(r["line"], "schedule line")
            amount = number(r["amount"], "schedule amount", minimum=0)
            if cp == key or cp not in answers or line not in INCOME_LINES | COST_LINES or not amount:
                raise ValueError(f"{key}.schedule row has an invalid participant, EBITDA line or amount")
            scheduled[key, line] += amount
            if line in INCOME_LINES:
                income[key, cp, amount, line] += 1
            else:
                costs[cp, key, amount] += 1
    if any(mode in (None, "not_offset") or answers[key]["no_intragroup_stock_or_asset_profit"] is not True
           for key, mode in modes.items()):
        return False, "Intragroup EBITDA offsets or absence of intragroup profit in assets were not attested."
    if any(mode == "offset_other_income_only" and answers[key].get("unoffset_other_income_amount") is None
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
    for (key, line), amount in sorted(scheduled.items()):
        if amount > raw[key].get(line, 0):
            return False, (f"The intragroup amount scheduled on {line} for {key} exceeds the {line} recorded in its "
                           "accounts.")
    variant = "offset_other_income_only" in modes.values()
    if variant and any(raw[key].get("other_income", 0) < u for key, u in unoffset.items()):
        return False, "Other income less the attested unoffset amount is negative."
    return variant, None


def _left_out_memo(request, participants, debt):
    left_out = request["left_out"]
    if not isinstance(left_out, dict) or set(left_out) & participants:
        raise ValueError("left_out must be an object keyed by non-participant IDs")
    covered, problem = {}, None
    statements = defaultdict(list)      # debt_id -> every (entity, amount, overlap_confirmed) statement about it
    for entity, item in sorted(left_out.items()):
        text(entity, "left_out entity ID")
        item = obj(item, f"left_out[{entity}]", {"reason", "external_debt", "support"})
        text(item["reason"], "left-out reason")
        external = item["external_debt"]
        if external not in (None, "unknown"):
            external = number(external, "left-out external_debt", minimum=0)
        debts = {}
        for row in array(item["support"], "left-out support"):
            r = obj(row, "support", {"participant", "type", "debt_id", "covered_principal", "evidence"},
                    {"overlap_confirmed"})
            if text(r["participant"], "support participant") not in participants:
                raise ValueError("left-out support must be provided by a participant")
            check_choice(r["type"], ("guarantee", "security"), "support type")
            text(r["evidence"], "support evidence")
            check_choice(r.get("overlap_confirmed", False), (True, False), "overlap_confirmed")
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
                else:
                    statements[identifier].append((entity, amount, r.get("overlap_confirmed", False)))
                    if debts.setdefault(identifier, amount) != amount:
                        problem = f"conflicting covered principal for {entity} debt {identifier}"
        entity_total = sum(debts.values())
        if isinstance(external, Fraction) and entity_total > external:
            problem = f"covered principal conflicts with external debt for {entity}"
    # A debt covered by more than one statement is the same covered principal only if EVERY statement attests the
    # overlap and the amounts agree: the rule reads the whole set, never the order of the rows (maintainer decision on
    # #472, PX628-108 / PX628-111): equal amounts alone could be two disjoint slices.
    for identifier, rows in sorted(statements.items()):
        amounts = {amount for _, amount, _ in rows}
        if len(amounts) > 1:
            problem = f"conflicting covered principal for debt {identifier}"
        elif len(rows) > 1 and not all(flag for _, _, flag in rows):
            unattested = ", ".join(sorted({entity for entity, _, flag in rows if not flag}))
            problem = f"overlap of covered principal on debt {identifier} is not attested for {unattested}"
        else:
            covered[identifier] = next(iter(amounts))
    for row in array(request["participant_guarantees"], "participant_guarantees"):
        r = obj(row, "participant guarantee", {"provider", "beneficiary", "amount", "evidence"})
        provider, beneficiary = (text(r[k], f"guarantee {k}") for k in ("provider", "beneficiary"))
        if provider == beneficiary or {provider, beneficiary} - participants:
            raise ValueError("a participant guarantee needs two different participants")
        text(r["evidence"], "guarantee evidence")
        if r["amount"] not in (None, "unknown"):
            number(r["amount"], "guarantee amount", minimum=0)
    return {"value": "unknown" if problem else json_number(debt + sum(covered.values())), "reason": problem}


# A registered accounting basis and a declared framework are compared by family; values outside this table are not
# judged (PX628-115): only two recognised, different families are a contradiction.
BASIS_FAMILIES = {
    "uk gaap": "UK GAAP", "frs 101": "FRS 101", "frs 102": "FRS 102", "frs 105": "FRS 105",
    "ifrs": "IFRS", "ifrs for smes": "IFRS for SMEs", "us gaap": "US GAAP", "u.s. gaap": "US GAAP",
}
# Named standards that may be recorded under the other label without a contradiction: the umbrella UK GAAP covers
# each FRS standard, and FRS 101 applies IFRS recognition and measurement (decision on #472). Two different specific
# standards (FRS 102 and FRS 105, say) are a contradiction.
COMPATIBLE_BASES = frozenset(frozenset(pair) for pair in (
    ("UK GAAP", "FRS 101"), ("UK GAAP", "FRS 102"), ("UK GAAP", "FRS 105"), ("IFRS", "FRS 101"),
))


def _check_declared_frameworks(request: dict[str, Any], inputs: dict[str, Any]) -> None:
    for key, item in inputs.items():
        source = item.get("source")
        registered = source.get("accounting_basis") if isinstance(source, dict) else None
        attestation = request["attestations"].get(key) if isinstance(request.get("attestations"), dict) else None
        declared = attestation.get("framework") if isinstance(attestation, dict) else None
        if not isinstance(registered, str) or not isinstance(declared, str):
            continue
        registered_family = BASIS_FAMILIES.get(registered.strip().casefold())
        declared_family = BASIS_FAMILIES.get(declared.strip().casefold())
        if (registered_family and declared_family and registered_family != declared_family
                and frozenset((registered_family, declared_family)) not in COMPATIBLE_BASES):
            raise ValueError(
                f"{key}: the registered accounting basis {registered!r} contradicts the declared framework "
                f"{declared!r}; reconcile the record or the attestation")


class PeriodResult(TypedDict):
    """The calculated, stored result of one period (the `computed` object); the producer and every consumer share it.

    The nested records keep a mapping type: their inner shapes are owned by this module and validated by
    `state_manager` (`computed_figures_problem`). `output_digest` is added by the caller after the digest is computed.
    """

    financials: dict[str, Any]
    ratios: dict[str, Any]
    withheld: dict[str, str]
    adjusted_raw: dict[str, int | float]
    adjusted_raw_rounded: list[str]
    label: str
    limitations: list[str]
    metric_label: str | None
    participant_table: list[dict[str, Any]]
    eliminations_applied: list[dict[str, Any]]
    refused_journals: list[dict[str, Any]]
    debt_before: int | float
    debt_eliminated: int | float
    debt_eliminated_pct: float | None
    debt_lines: dict[str, dict[str, int | float]]
    debt_memo: dict[str, Any]
    cash: dict[str, int | float | None]
    group_net_debt: int | float | None
    attestations: dict[str, Any]
    left_out: dict[str, Any]
    skipped_chain_entities: dict[str, Any]
    participant_guarantees: list[Any]
    output_digest: NotRequired[str]


def calculate_period(period: str, request: dict[str, Any], inputs: dict[str, Any],
                     legal_names: dict[str, str | None]) -> PeriodResult:
    from spreading_builder import FIELD_LABELS, evaluate_financial_model

    # A misspelt line (long_term_debt_typo) must never be silently dropped from the group total (PX628-110 / CG628-11).
    for key, item in inputs.items():
        unknown = sorted((line for line in item["raw_period"] if line not in FIELD_LABELS), key=str)
        if unknown:
            raise ValueError(
                f"{key}: unknown raw line-item name(s) {', '.join(repr(line) for line in unknown)} in {period}; "
                "known names are the keys of spreading_builder.FIELD_LABELS; nothing was written")
    _check_declared_frameworks(request, inputs)
    raw = {}
    for key, item in inputs.items():
        raw[key] = {line: number(value, f"{key}.{line}") for line, value in item["raw_period"].items()
                    if value is not None}

    def combined():
        # Exact aggregate of every participant's line (maintainer decision on #472, PX628-109 / PX628-113): amounts
        # stay exact fractions all the way to the evaluator and are converted only at the output boundary.
        return {line: sum((r.get(line, Fraction()) for r in raw.values()), Fraction())
                for line in sorted(set().union(*raw.values()))}

    def printed(amounts):
        return {line: json_number(value) for line, value in amounts.items()}

    def evaluate(figures):
        # Exact arithmetic (maintainer decision on #472): the unchanged evaluator works in binary floating point, so
        # 0.1 - 0.3 + 0.2 would leave a tiny positive EBITDA instead of exact zero. Every amount is therefore scaled
        # by one common factor to a whole integer (a Python integer, exact at any size), evaluated, and the two money
        # figures it exposes are scaled back; the ratios are scale-free. No epsilon is used.
        exact = {line: value if isinstance(value, Fraction) else number(value, f"figure {line}")
                 for line, value in figures.items()}
        scale = 1
        for value in exact.values():
            scale = scale * value.denominator // gcd(scale, value.denominator)
        scaled = {line: value * scale for line, value in exact.items()}
        if any(abs(value) >= MAX_SCALED_AMOUNT for value in scaled.values()):
            raise ValueError("an amount is too large or too precise to be evaluated exactly")
        result = evaluate_financial_model({period: {line: int(value) for line, value in scaled.items()}})
        financials = dict(result["financials"][period])
        exact_money = {}
        for money in ("total_debt", "ebitda"):
            if financials.get(money) is not None:
                exact_money[money] = Fraction(str(financials[money])) / scale
                financials[money] = json_number(exact_money[money])
        return financials, result["ratios"][period], exact_money

    answers, perimeter_reason = _perimeter(request, raw)
    sides, caps = _declarations(request, inputs, raw, legal_names)
    before = combined()
    debt_before = evaluate(before)[0]["total_debt"]
    applied, refused = _apply_journals(request, sides, caps, raw)
    adjusted_exact = combined()
    adjusted = printed(adjusted_exact)
    # A JSON number is a binary float: an exact decimal total it cannot hold is stored rounded, so the lines affected are
    # named instead of being rounded silently (CG628-17). The evaluation itself stayed exact.
    adjusted_rounded = sorted(line for line, value in adjusted_exact.items()
                              if value.denominator != 1 and Fraction(Decimal(repr(float(value)))) != value)
    variant, ebitda_reason = _ebitda_gate(answers, raw)
    ebitda_reason = perimeter_reason or ebitda_reason
    negative_cash = sorted(key for key, lines in raw.items() if lines.get("cash", 0) < 0)
    cash_attested = all(a.get("cash_no_group_balance") is True for a in answers.values())
    # A recorded `null` cash is "unavailable", not an attested zero (PX628-120): the cash outputs are withheld.
    unavailable_cash = sorted(key for key, item in inputs.items() if "cash" in item["raw_period"]
                              and item["raw_period"]["cash"] is None)
    cash_ok = cash_attested and not negative_cash and not unavailable_cash
    restricted_exact = sum((number(a["restricted_cash_amount"], "restricted cash") for a in answers.values()),
                           Fraction())
    restricted = json_number(restricted_exact)
    cash_exact = adjusted_exact.get("cash", Fraction())
    unrestricted_exact = cash_exact - restricted_exact if cash_ok else None
    unrestricted = json_number(cash_exact - restricted_exact) if cash_ok else None
    interest_ok = all(a.get("no_intragroup_interest") is True for a in answers.values())
    ev_fin, ev_rat, ev_exact = evaluate(adjusted_exact)
    if variant or (cash_ok and restricted_exact):
        ev_fin, ev_rat, ev_exact = evaluate(adjusted_exact | ({"other_income": Fraction()} if variant else {}) |
                                            ({"cash": unrestricted_exact} if cash_ok else {}))
    debt = ev_fin["total_debt"]
    debt_exact = ev_exact["total_debt"]
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
            # Both gates' reasons are kept when both fail, so fixing one does not hide the other (PX628-117).
            cash_parts = []
            if not cash_attested:
                cash_parts.append("Qualifying cash free of group or related-party balances was not attested.")
            if negative_cash:
                cash_parts.append(f"Cash is negative for {', '.join(negative_cash)}; an overdraft belongs in the "
                                  "debt lines, not in qualifying cash.")
            if unavailable_cash:
                cash_parts.append(f"Cash is recorded as unavailable (null) for {', '.join(unavailable_cash)}; "
                                  "an unavailable amount is not an attested zero.")
            cash_reason = " ".join(cash_parts)
            reason = f"{reason} {cash_reason}" if reason else cash_reason
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
        p_raw = dict(values)
        figures, _, _ = evaluate(p_raw | ({"other_income": Fraction()} if variant else {}))
        table.append({"participant": key, "legal_entity": legal_names[key], "status": answers[key]["status"],
                      "ownership_pct": answers[key]["ownership_pct"], "total_debt": figures["total_debt"],
                      "cash": None if key in unavailable_cash else json_number(p_raw.get("cash", Fraction())),
                      "ebitda": None if ebitda_reason else figures["ebitda"]})
    eliminated = json_number(sum((number(j["amount"], "eliminated principal") for j in applied), Fraction()))
    return {
        "financials": fin, "ratios": ratios, "withheld": withheld, "adjusted_raw": adjusted,
        "adjusted_raw_rounded": adjusted_rounded,
        "label": LABEL, "limitations": list(LIMITATIONS), "metric_label": VARIANT_LABEL if variant else None,
        "participant_table": table, "eliminations_applied": applied, "refused_journals": refused,
        "debt_before": debt_before, "debt_eliminated": eliminated,
        "debt_eliminated_pct": eliminated / debt_before * 100 if debt_before > 0 else None,
        "debt_lines": {k: {"before": json_number(before.get(k, Fraction())), "after": adjusted.get(k, 0)}
                       for k in DEBT_LINES},
        "debt_memo": _left_out_memo(request, set(raw), debt_exact),
        "cash": {"total": json_number(cash_exact) if cash_ok else None, "restricted": restricted,
                 "unrestricted": unrestricted},
        "group_net_debt": json_number(debt_exact - unrestricted_exact) if cash_ok else None,
        "attestations": answers, "left_out": request["left_out"], "skipped_chain_entities": request["skipped_chain_entities"],
        "participant_guarantees": request["participant_guarantees"],
    }
