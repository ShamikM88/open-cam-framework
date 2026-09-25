"""Persisted analyst-confirmed spreading conventions, reused across deals
instead of being re-confirmed/re-corrected one-shot every time (issue #58).

Two independent scopes, sharing one file shape and one read-modify-write
implementation:

- Borrower-specific: deals/<Company>/_conventions.json -- e.g. "this
  borrower's accounts embed Depreciation inside Cost of Goods Sold."
- Enterprise-wide: config/spreading_conventions.json -- e.g. "this
  institution always supplies its own pre-spread figures, never
  framework-recomputed," applying across every borrower.

Only ever consulted by the interactive /spread command -- never by
orchestrator.py's headless CLI. Headless mode has no analyst present to
confirm anything, and the whole point of this module (see CLAUDE.md and
issue #58) is that every application of a persisted convention must be
confirmed/disclosed, never silently assumed. Don't wire this module into
orchestrator.py's run_pipeline() -- it has no analyst to ask.

Dependency-free (no anthropic/docx/openpyxl imports), matching
source_manifest.py/state_manager.py/template_resolver.py's own pattern.
Reuses state_manager.py's sanitize_path_component()/_FileLock rather than
duplicating either.

config/spreading_conventions.json needs its own explicit .gitignore line
(config/ isn't broadly ignored); deals/<Company>/_conventions.json is
already covered by the existing wholesale deals/ ignore entry.
"""
import argparse
import json
import os
import tempfile
from datetime import date

from state_manager import DEALS_DIR, LOCK_TIMEOUT_SECONDS, _FileLock, sanitize_path_component

COMPANY_CONVENTIONS_FILENAME = "_conventions.json"
ENTERPRISE_CONVENTIONS_FILENAME = "spreading_conventions.json"


def _deals_root(base_dir):
    return os.path.join(base_dir, DEALS_DIR) if base_dir else DEALS_DIR


def company_convention_path(company, base_dir=None):
    """deals/<Company>/_conventions.json -- one level above the dated
    <Proposal>_<Date>/ folders, so it's shared across every deal for this
    borrower rather than scoped to one proposal. Leading underscore keeps
    it visually distinct from a dated proposal folder when this company's
    deals/<Company>/ directory is listed."""
    company = sanitize_path_component(company, "company")
    return os.path.join(_deals_root(base_dir), company, COMPANY_CONVENTIONS_FILENAME)


def enterprise_convention_path(base_dir=None):
    """config/spreading_conventions.json -- fork-wide, same tier as
    config/credit_policy.md/credit_policy_notes.md."""
    return os.path.join(base_dir or ".", "config", ENTERPRISE_CONVENTIONS_FILENAME)


def _read_convention(path):
    """Return the parsed convention record at `path`, or None if it doesn't
    exist yet. Raises ValueError (not a raw json.JSONDecodeError) on a
    corrupted file -- mirrors state_manager.read_state()'s own discipline:
    never let a corrupted file look like "nothing on file" and get silently
    overwritten by the next write."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Convention file at {path} is corrupted and could not be parsed ({e}). "
                "It may have been left partial by an interrupted write. Restore it "
                "from a backup or fix it by hand before continuing."
            ) from e


def _write_convention(path, *, financials_source, note=None, confirmed_date=None, extra_history_fields=None):
    """Locked read-modify-write: overwrite the top-level "current" fields
    and append one entry to `history` (echoes state.json's own
    review_trail/review_verdict "full history + latest convenience field"
    idiom). Called every time a convention is confirmed at /spread time --
    new, reconfirmed unchanged, or corrected -- not only on first
    confirmation or on an actual change, so confirmed_date always reflects
    the most recent confirmation and callers never need a "did this
    change" branch.
    """
    confirmed_date = confirmed_date or date.today().isoformat()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with _FileLock(path, timeout=LOCK_TIMEOUT_SECONDS):
        current = _read_convention(path) or {}
        history = list(current.get("history") or [])
        entry = {"financials_source": financials_source, "note": note, "confirmed_date": confirmed_date}
        entry.update(extra_history_fields or {})
        history.append(entry)

        record = {
            "financials_source_default": financials_source,
            "financials_source_note": note,
            "confirmed_date": confirmed_date,
            "history": history,
        }

        # Atomic, matching state_manager._merge_and_write()'s exact pattern:
        # write to a temp file in the same directory (so os.replace() is a
        # same-filesystem rename) and swap it into place, rather than
        # truncating the target file directly -- and clean up the temp file
        # on any failure instead of leaving it orphaned on disk.
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            os.replace(tmp_path, path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    return record


def read_company_convention(company, base_dir=None):
    return _read_convention(company_convention_path(company, base_dir=base_dir))


def write_company_convention(company, *, financials_source, note=None, confirmed_date=None,
                              proposal=None, base_dir=None):
    path = company_convention_path(company, base_dir=base_dir)
    extra = {"proposal": proposal} if proposal else {}
    return _write_convention(path, financials_source=financials_source, note=note,
                              confirmed_date=confirmed_date, extra_history_fields=extra)


def read_enterprise_convention(base_dir=None):
    return _read_convention(enterprise_convention_path(base_dir=base_dir))


def write_enterprise_convention(*, financials_source, note=None, confirmed_date=None, base_dir=None):
    path = enterprise_convention_path(base_dir=base_dir)
    return _write_convention(path, financials_source=financials_source, note=note,
                              confirmed_date=confirmed_date)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read or write a persisted analyst-confirmed spreading convention -- "
                     "borrower-specific (--company) or enterprise-wide (--enterprise). "
                     "Callable from a slash command's Bash step (see .claude/commands/spread.md). "
                     "No Anthropic dependency."
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--company", help="Borrower-specific scope: deals/<Company>/_conventions.json")
    scope.add_argument("--enterprise", action="store_true",
                        help="Enterprise-wide scope: config/spreading_conventions.json")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--read", action="store_true")
    mode.add_argument("--write", action="store_true")

    parser.add_argument("--financials-source", help="Required with --write, e.g. analyst-supplied")
    parser.add_argument("--note", help="Free-text description of the convention")
    parser.add_argument("--confirmed-date", help="Defaults to today (ISO 8601) if omitted")
    parser.add_argument("--proposal", help="Only meaningful with --company -- recorded on the history entry")
    args = parser.parse_args()

    if args.write and not args.financials_source:
        parser.error("--write requires --financials-source")
    if args.proposal and not args.company:
        parser.error("--proposal is only meaningful with --company")

    if args.read:
        result = read_company_convention(args.company) if args.company else read_enterprise_convention()
        print(json.dumps({"found": result is not None, "convention": result}, indent=2))
    else:
        if args.company:
            record = write_company_convention(
                args.company, financials_source=args.financials_source, note=args.note,
                confirmed_date=args.confirmed_date, proposal=args.proposal,
            )
        else:
            record = write_enterprise_convention(
                financials_source=args.financials_source, note=args.note,
                confirmed_date=args.confirmed_date,
            )
        print(json.dumps(record, indent=2))
