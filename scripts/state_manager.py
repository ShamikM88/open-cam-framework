"""File-backed state persistence for a single deal.

Dependency-free (no anthropic/docx/openpyxl imports), matching
template_resolver.py's pattern, so it's cheap to unit test and safe to
import from anywhere (a slash command's Bash step, orchestrator.py) without
pulling in heavy dependencies.

See CLAUDE.md's "Context Window & State Management Protocol" section for the
full rationale: every step of a deal checkpoints its results here, so a
multi-step deal survives context compaction or a resumed session instead of
relying on the conversation itself to remember figures.

Date resolution: when `date_str` isn't given, an existing
deals/<company>/<proposal>_<date>/ folder for this company/proposal is found
automatically (most recent wins, if somehow more than one exists) rather
than always defaulting to today -- unlike deal_export.py's export_deal(),
whose job (a one-shot export) never needs to be found again later, so
today is always correct there. This is what lets a deal resumed on a later
calendar day still find its original state.json.

review_trail: append_review_trail() is the one exception to write_state()'s
plain shallow-merge semantics baked into this module itself, rather than
left to each caller -- see its docstring.
"""
import glob
import json
import os
from datetime import datetime

DEALS_DIR = "deals"


def _deals_root(base_dir):
    return os.path.join(base_dir, DEALS_DIR) if base_dir else DEALS_DIR


def _existing_date_str(company, proposal, base_dir=None):
    """Date suffix of the most recent existing deals/<company>/<proposal>_<date>/
    folder for this company/proposal, or None if there isn't one yet.

    Date suffixes are ISO-8601 (YYYY-MM-DD), so sorting them as plain
    strings also sorts them chronologically -- the lexicographic max is
    the most recent.
    """
    prefix = f"{proposal}_"
    pattern = os.path.join(_deals_root(base_dir), company, f"{prefix}*")
    dates = [
        os.path.basename(p)[len(prefix):]
        for p in glob.glob(pattern)
        if os.path.isdir(p) and os.path.basename(p).startswith(prefix)
    ]
    return max(dates) if dates else None


def _resolve_date_str(company, proposal, date_str=None, base_dir=None):
    if date_str:
        return date_str
    return _existing_date_str(company, proposal, base_dir=base_dir) or datetime.now().strftime("%Y-%m-%d")


def state_path(company, proposal, date_str=None, base_dir=None):
    """Path to this deal's state.json, resolved the same way deal_export.py
    resolves its output directory: deals/<Company>/<Proposal>_<Date>/ --
    except that an explicit `date_str` isn't required to find an existing
    file; see the module docstring.
    """
    date_str = _resolve_date_str(company, proposal, date_str=date_str, base_dir=base_dir)
    return os.path.join(_deals_root(base_dir), company, f"{proposal}_{date_str}", "state.json")


def read_state(company, proposal, date_str=None, base_dir=None):
    """Return the parsed state.json dict for this deal, or None if it doesn't exist yet."""
    path = state_path(company, proposal, date_str=date_str, base_dir=base_dir)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_state(company, proposal, date_str=None, base_dir=None, **fields):
    """Merge `fields` into this deal's state.json (if any) and write it back.

    Creates deals/<Company>/<Proposal>_<Date>/ if it doesn't exist yet --
    reusing an existing dated folder for this company/proposal when
    `date_str` isn't given, rather than always creating a new one dated
    today. `company`, `proposal`, and `date` are always kept in sync
    automatically.

    The merge is a shallow dict.update(): a fresh `financials={...}` replaces
    the whole financials dict rather than merging inside it, and a fresh
    `steps_completed=[...]` replaces the whole list. Callers that want to
    extend a nested dict or list should read_state() first, build the full
    updated value themselves, and pass that back.

    Returns the full state dict that was written.
    """
    date_str = _resolve_date_str(company, proposal, date_str=date_str, base_dir=base_dir)
    path = state_path(company, proposal, date_str=date_str, base_dir=base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    state = read_state(company, proposal, date_str=date_str, base_dir=base_dir) or {}
    state["company"] = company
    state["proposal"] = proposal
    state["date"] = date_str
    state.update(fields)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    return state


def append_review_trail(company, proposal, verdict, notes=None, timestamp=None,
                         date_str=None, base_dir=None, **extra_fields):
    """Append one entry to this deal's append-only `review_trail`, and set
    `review_verdict` to this iteration's verdict as a "latest verdict"
    convenience field.

    write_state()'s shallow merge would silently replace the whole
    review_trail list if a caller just passed a fresh `review_trail=[...]`
    -- since /assemble loops /review until APPROVED, that was losing every
    intermediate REJECTED verdict and its revision notes as soon as the
    next iteration wrote state. This reads the existing trail first (`[]`
    if there isn't one yet), appends, and writes the full list back, so
    `review_trail` is always the deal's complete audit history regardless
    of how many review iterations it took.

    `iteration` is always `len(existing review_trail) + 1`, so it's correct
    whether this is orchestrator.py's one-shot audit call or one loop of
    /assemble -> /review. Any `extra_fields` are merged in on the same
    write (e.g. `deal_type`, `steps_completed`), exactly like write_state().

    Returns the full state dict that was written.
    """
    timestamp = timestamp or datetime.now().isoformat()
    existing = read_state(company, proposal, date_str=date_str, base_dir=base_dir) or {}
    trail = list(existing.get("review_trail") or [])
    trail.append({
        "iteration": len(trail) + 1,
        "verdict": verdict,
        "notes": notes,
        "timestamp": timestamp,
    })

    return write_state(
        company, proposal, date_str=date_str, base_dir=base_dir,
        review_verdict=verdict, review_trail=trail, **extra_fields,
    )
