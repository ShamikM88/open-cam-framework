"""Per-step persistence of the actual source material a step fetches or is
given -- not just the citation string that ends up in state.json's
`sources` lists (see CLAUDE.md's Maker-Checker grounding rules and
agents/underwriter_agent.md's Guideline 1). A citation is only as
auditable as the document it points to: until this module existed, a
downloaded Companies House filing, a browsed web page, or an
analyst-provided document lived only in a session's own scratchpad and
was gone once the session ended, leaving nothing on disk for an analyst
to spot-check a claim against or re-derive a figure from later.

Saves per-step, not batched at the end -- matching CLAUDE.md's existing
"checkpoint after every step" philosophy rather than introducing a
different persistence rhythm just for source material. Whichever step
(/triage, /research, /spread, /commercial, /collateral, /project) fetched
or was given the material calls save_source() as part of its own
checkpoint.

Dependency-free (no anthropic/docx/openpyxl imports), matching
template_resolver.py/state_manager.py's own pattern. deals/<Company>/
<Proposal>_<Date>/sources/ is already gitignored for free (it's under
deals/, gitignored wholesale -- see CLAUDE.md's confidentiality rule), so
this introduces no new confidentiality gap.
"""
import argparse
import json
import os
import re
import shutil
from datetime import date

from state_manager import (
    DEALS_DIR,
    LOCK_TIMEOUT_SECONDS,
    _FileLock,
    resolve_date_str,
    sanitize_path_component,
)


def _deals_root(base_dir):
    return os.path.join(base_dir, DEALS_DIR) if base_dir else DEALS_DIR


def sources_dir(company, proposal, date_str=None, base_dir=None):
    """deals/<Company>/<Proposal>_<Date>/sources/ -- reuses an existing
    dated folder for this company/proposal the same way state_path() does
    (via state_manager.resolve_date_str()), so source material always
    lands alongside that deal's own state.json rather than a fresh folder
    of its own. Does not create the directory -- callers that are about to
    write into it should os.makedirs(..., exist_ok=True) themselves (see
    save_source()); a read-only caller (read_manifest()) has no reason to
    create it.
    """
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str:
        date_str = sanitize_path_component(date_str, "date_str")
    date_str = resolve_date_str(company, proposal, date_str=date_str, base_dir=base_dir)
    return os.path.join(_deals_root(base_dir), company, f"{proposal}_{date_str}", "sources")


def _slugify_url(url):
    """A URL turned into a safe-ish filename stem -- used only as a
    fallback when the caller doesn't give an explicit --filename and the
    source has no local basename to borrow (e.g. a saved web page rather
    than a downloaded file)."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("://", 1)[-1]).strip("_")
    return (stem or "source")[:150]


def _derive_filename(url, source_path, explicit):
    """Explicit --filename wins outright. Otherwise, when a URL is given,
    prefer a name derived from it (readable and traceable back to the
    source) over source_path's own basename, since source_path is often
    just a scratch/temp download location with a meaningless name (e.g.
    a browser tool's own temp file). With no URL at all -- a locally or
    analyst-provided document -- fall back to source_path's basename,
    the only name available.
    """
    if explicit:
        name = explicit
    elif url:
        name = _slugify_url(url) + os.path.splitext(source_path)[1]
    else:
        name = os.path.basename(source_path)
    return sanitize_path_component(name, "filename")


def _unique_dest_path(directory, filename):
    """Never silently overwrite a different source that happens to share a
    filename (e.g. two different pages both named "index.html") -- append
    a numeric suffix until a free name is found. A save_source() call that
    is genuinely re-fetching the *same* source (same filename, same
    directory) still gets a fresh numbered copy rather than being
    deduplicated -- the manifest is an append-only record of what was
    fetched and when, not a cache keyed on filename.
    """
    root, ext = os.path.splitext(filename)
    candidate = filename
    n = 2
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f"{root}_{n}{ext}"
        n += 1
    return candidate


def save_source(company, proposal, *, step, claim, source_path, url=None, filename=None,
                 fetched_date=None, date_str=None, base_dir=None):
    """Copy `source_path` (an already-downloaded/saved file -- a PDF, a
    saved web page's text/HTML, an analyst-provided document) into this
    deal's sources/ folder and append an entry describing it to
    sources/manifest.json.

    `step` (e.g. "triage", "research", "spread") and `claim` (a short
    description of what this source backs, e.g. "SC315671 legal identity
    and PSC filing") are both required -- an unlabelled source file
    defeats the point of this (see issue #67): the manifest is what makes
    a saved document traceable back to the claim it was fetched for.

    Only ever copies the file the caller already produced -- this module
    never fetches anything itself (no network/browser dependency), so it
    stays as dependency-free as template_resolver.py/state_manager.py.

    Returns the manifest entry written.
    """
    if not step or not claim:
        raise ValueError("save_source() requires both step and claim.")
    if not source_path or not os.path.isfile(source_path):
        raise ValueError(f"save_source() needs an existing file at source_path (got {source_path!r}).")

    directory = sources_dir(company, proposal, date_str=date_str, base_dir=base_dir)
    os.makedirs(directory, exist_ok=True)

    # One lock covers the *whole* filename-collision-check -> copy ->
    # manifest-append sequence, not just the manifest write at the end --
    # two concurrent save_source() calls (e.g. two sessions touching the
    # same deal at once, exactly the scenario _FileLock exists for; see
    # state_manager.py's own docstring) could otherwise both pass
    # _unique_dest_path()'s existence check for the same derived filename
    # before either copies its file, and the second copy would silently
    # overwrite the first's document.
    manifest_path = os.path.join(directory, "manifest.json")
    with _FileLock(manifest_path, timeout=LOCK_TIMEOUT_SECONDS):
        resolved_filename = _unique_dest_path(directory, _derive_filename(url, source_path, filename))
        dest_path = os.path.join(directory, resolved_filename)
        shutil.copyfile(source_path, dest_path)

        entry = {
            "filename": resolved_filename,
            "url": url,
            "step": step,
            "claim": claim,
            "fetched_date": fetched_date or date.today().isoformat(),
        }

        manifest = []
        if os.path.exists(manifest_path):
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        manifest.append(entry)
        tmp_path = manifest_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        os.replace(tmp_path, manifest_path)

    return entry


def read_manifest(company, proposal, date_str=None, base_dir=None):
    """The list of manifest entries recorded so far for this deal (in the
    order they were saved), or [] if no sources/ folder or manifest exists
    yet -- a deal that hasn't had any source material saved is not an
    error, just an empty record."""
    manifest_path = os.path.join(
        sources_dir(company, proposal, date_str=date_str, base_dir=base_dir), "manifest.json"
    )
    if not os.path.exists(manifest_path):
        return []
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Save a piece of fetched/given source material into this deal's "
                     "deals/<Company>/<Proposal>_<Date>/sources/ folder and record it in "
                     "sources/manifest.json. Callable from a slash command's Bash step, once "
                     "Claude has already downloaded/saved the material itself -- this script "
                     "never fetches anything on its own."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--step", required=True, help='e.g. triage, research, spread, commercial, collateral, project')
    parser.add_argument("--claim", required=True, help="Short description of what this source backs")
    parser.add_argument("--file", required=True, dest="source_path",
                         help="Path to the already-downloaded/saved source file")
    parser.add_argument("--url", help="The source URL, if this came from the web")
    parser.add_argument("--filename", help="Override the destination filename (default: derived from --file/--url)")
    args = parser.parse_args()

    entry = save_source(args.company, args.proposal, step=args.step, claim=args.claim,
                         source_path=args.source_path, url=args.url, filename=args.filename)
    print(json.dumps(entry, indent=2))
