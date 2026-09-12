"""File-backed state persistence for a single deal.

Dependency-free (no anthropic/docx/openpyxl imports), matching
template_resolver.py's pattern, so it's cheap to unit test and safe to
import from anywhere (a slash command's Bash step, orchestrator.py) without
pulling in heavy dependencies.

See CLAUDE.md's "Context Window & State Management Protocol" section for the
full rationale: every step of a deal checkpoints its results here, so a
multi-step deal survives context compaction or a resumed session instead of
relying on the conversation itself to remember figures.

Known limitation: like deal_export.py's export_deal(), the date used to
resolve a deal's folder defaults to *today* when not given explicitly. Two
write_state() calls for the same company/proposal on different calendar
days will resolve to two different files unless the caller passes the same
explicit date_str both times. Within a single orchestrator.py run (both
checkpoints happen seconds apart, same process) this is a non-issue; the
interactive slash-command path handles it by globbing for an existing
`deals/<company>/<proposal>_*/state.json` first (see .claude/commands/) so a
deal resumed on a later day still finds its original file.
"""
import json
import os
from datetime import datetime

DEALS_DIR = "deals"


def state_path(company, proposal, date_str=None, base_dir=None):
    """Path to this deal's state.json, resolved the same way deal_export.py
    resolves its output directory: deals/<Company>/<Proposal>_<Date>/.
    """
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    deals_root = os.path.join(base_dir, DEALS_DIR) if base_dir else DEALS_DIR
    return os.path.join(deals_root, company, f"{proposal}_{date_str}", "state.json")


def read_state(company, proposal, date_str=None, base_dir=None):
    """Return the parsed state.json dict for this deal, or None if it doesn't exist yet."""
    path = state_path(company, proposal, date_str=date_str, base_dir=base_dir)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_state(company, proposal, date_str=None, base_dir=None, **fields):
    """Merge `fields` into this deal's state.json (if any) and write it back.

    Creates deals/<Company>/<Proposal>_<Date>/ if it doesn't exist yet.
    `company`, `proposal`, and `date` are always kept in sync automatically.

    The merge is a shallow dict.update(): a fresh `financials={...}` replaces
    the whole financials dict rather than merging inside it, and a fresh
    `steps_completed=[...]` replaces the whole list. Callers that want to
    extend a nested dict or list should read_state() first, build the full
    updated value themselves, and pass that back.

    Returns the full state dict that was written.
    """
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
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
