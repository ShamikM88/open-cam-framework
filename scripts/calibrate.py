import json
import os
import sys
import glob
import argparse
from anthropic import Anthropic
from pypdf import PdfReader
from template_resolver import local_cam_template_path

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

_DEFAULT_MODEL = "claude-sonnet-5"


def _load_settings():
    """config/settings.json's full contents -- mirrors orchestrator.py's
    own _load_settings(). Falls back to {} if the config file is missing
    or malformed rather than raising.
    """
    try:
        with open("config/settings.json", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _resolve_calibrate_model():
    """Calibration (style/tone extraction, template derivation) is a
    different task profile from drafting/reviewing a CAM, so it gets its
    own optional "calibrate_model" override in config/settings.json --
    but defaults to "maker_model" (the Underwriter's own configured
    model) rather than a separate hardcoded constant, since there's no
    institutional-governance reason for calibration to need a
    structurally different model the way the Checker does (see #31):
    calibration never independently audits the Maker's own work.

    Unlike orchestrator.py's _resolve_maker_checker_config(), a missing
    or malformed config here falls back to _DEFAULT_MODEL rather than
    raising: calibration is a one-time setup step deriving a style
    guide/template, not drafting a real credit memo, so a friendlier
    default is appropriate where orchestrator.py's fail-loud behavior
    (see #34) would just be an unnecessary obstacle to a first-run
    `--mock` smoke test.
    """
    settings = _load_settings()
    return settings.get("calibrate_model") or settings.get("maker_model") or _DEFAULT_MODEL


STYLE_PROMPT = (
    "Analyze these sample CAMs and extract writing style, tone, and standard "
    "risk phrasing.\n\n"
    "Apply editorial judgment, don't mirror uncritically: capture genuine house "
    "conventions -- terseness, fact density, what information is required and in "
    "what order, standard phrasing for grades/ratings -- but don't encode a "
    "stylistic quirk as house style just because the samples happen to do it, if "
    "it actually hurts readability. In particular, don't extract a rule that "
    "eliminates all connective prose from narrative sections in favor of "
    "disconnected one-line bullets, or that reduces figure-grounded commentary to "
    "bare number call-outs with no explained relationship between them "
    "(\"Revenue: -4%. Margin: +2%.\") -- a short paragraph connecting related "
    "facts, still grounded in specific figures, is clearer than fragments of the "
    "same information, and terse/information-dense is the actual goal, not "
    "maximally fragmented.\n\n{text}"
)

TEMPLATE_PROMPT = (
    "Analyze the structure of these sample CAMs -- the section headings, the "
    "table columns, and the order information is presented in -- and produce "
    "a generic Markdown CAM template that mirrors that structure exactly.\n\n"
    "Critical: this template will be committed to a shared codebase, so it "
    "must contain ZERO real data from the samples. No real company names, "
    "people's names, dates, or figures. Replace every one of those with a "
    "bracketed [placeholder] describing what belongs there (e.g. "
    "[Borrower Legal Name], [Amount], [PD Grade]). Output only the Markdown "
    "template itself, nothing else.\n\nSample CAMs:\n{text}"
)

STYLE_MERGE_PROMPT = (
    "Below are style/tone analyses of different parts of the SAME set of sample "
    "CAMs, each produced independently. Merge them into ONE style guide.\n\n"
    "Keep every genuine house convention any part found (terseness, fact density, "
    "required information and its order, standard phrasing for grades/ratings); "
    "where parts agree, state the convention once; where they contradict, follow "
    "what most parts show and don't invent a compromise. Apply the same editorial "
    "judgment as the individual analyses: don't encode a stylistic quirk as house "
    "style if it hurts readability, and don't produce a rule that eliminates all "
    "connective prose in favor of disconnected bullets or bare number call-outs. "
    "Output only the merged style guide, nothing else.\n\n{text}"
)

TEMPLATE_MERGE_PROMPT = (
    "Below are partial generic Markdown CAM templates, each derived independently "
    "from a different part of the SAME set of sample CAMs. Merge them into ONE "
    "template that mirrors the combined structure: every section heading and table "
    "column any part found, each once, in the order a CAM would naturally present "
    "them.\n\n"
    "Critical: this template will be committed to a shared codebase, so it must "
    "contain ZERO real data. No real company names, people's names, dates, or "
    "figures. Replace every one of those with a bracketed [placeholder] describing "
    "what belongs there (e.g. [Borrower Legal Name], [Amount], [PD Grade]). If any "
    "part leaked something that looks like real data, replace it with a placeholder "
    "too. Output only the Markdown template itself, nothing else.\n\n{text}"
)

MOCK_STYLE_GUIDE = (
    "# Calibrated Style Guide (MOCK)\n\n"
    "This file was generated by `calibrate.py --mock` -- no Anthropic API call was made, "
    "so no real style or tone was actually extracted from your samples. Run without "
    "`--mock` (with `ANTHROPIC_API_KEY` set) to produce a real style guide.\n"
)

MOCK_TEMPLATE = (
    "# Credit Assessment Memorandum (MOCK)\n\n"
    "This file was generated by `calibrate.py --mock` -- no Anthropic API call was made, "
    "so this is a placeholder, not a template actually derived from your samples' "
    "structure. Run without `--mock` (with `ANTHROPIC_API_KEY` set) to derive a real one.\n\n"
    "**Borrower Name:** [Company Name]\n\n"
    "## Summary\n"
    "- **Verdict:** [Approve / Decline]\n"
)

# Per-call input limit for the sample text sent to Claude (issue #109). Text
# beyond this is either discarded ("ignore") or processed in further
# CHUNK_CHAR_LIMIT-sized parts and merged ("split") -- never silently dropped.
CHUNK_CHAR_LIMIT = 12_000

# Merge-call input budget: per-part findings are merged in groups no larger
# than this (hierarchically, if need be), so a very large sample set can't
# produce one merge call bigger than the model can take.
MERGE_CHAR_BUDGET = 60_000

OVERFLOW_MODES = ("ask", "split", "ignore")


def _read_sample_text():
    text_content = ""
    for path in glob.glob("inputs/calibration_samples/*.pdf"):
        reader = PdfReader(path)
        for page in reader.pages:
            text_content += page.extract_text() + "\n"
    return text_content


def _split_into_chunks(text, limit=CHUNK_CHAR_LIMIT):
    """Split `text` into consecutive pieces of at most `limit` characters,
    preferring paragraph breaks, then line breaks, then spaces over a hard
    cut mid-word. Lossless: "".join(result) == text. In memory only -- the
    source PDFs are never written to, let alone split.
    """
    chunks = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + limit, n)
        if end < n:
            window = text[start:end]
            for sep in ("\n\n", "\n", " "):
                idx = window.rfind(sep)
                # Only accept a boundary past the halfway mark, so a lone
                # early newline can't produce a tiny first chunk.
                if idx >= limit // 2:
                    end = start + idx + len(sep)
                    break
        chunks.append(text[start:end])
        start = end
    return chunks


def _choose_overflow_mode(total_chars, chunk_count, requested):
    """Resolve what to do with samples longer than CHUNK_CHAR_LIMIT: "split" or
    "ignore". Decided up front, before any API call or file write, so a run
    never needs to ask (or fail) halfway through.

    An explicit --on-overflow wins. "ask" prompts on a terminal; with no one
    to answer (no TTY: CI, automation), it falls back to "split" -- the
    failure this exists to prevent is silently losing material, and splitting
    only costs extra API calls.
    """
    if requested in ("split", "ignore"):
        return requested

    if not sys.stdin.isatty():
        print(f"[INFO] Samples contain {total_chars:,} characters (limit per call: "
              f"{CHUNK_CHAR_LIMIT:,}); no terminal to ask, so splitting into "
              f"{chunk_count} parts and merging. Pass --on-overflow ignore to truncate instead.")
        return "split"

    discarded = total_chars - CHUNK_CHAR_LIMIT
    print(f"\nYour samples contain {total_chars:,} characters; the limit per Claude call is "
          f"{CHUNK_CHAR_LIMIT:,}.")
    print(f"  [i] Ignore - use only the first {CHUNK_CHAR_LIMIT:,} characters "
          f"({discarded:,} discarded)")
    print(f"  [s] Split  - process all {chunk_count} parts, then merge the results "
          f"({2 * chunk_count + 2} API calls instead of 2)")
    while True:
        try:
            answer = input("Choice [i/s]: ").strip().lower()
        except EOFError:
            print("[INFO] No input available; splitting.")
            return "split"
        if answer in ("s", "split"):
            return "split"
        if answer in ("i", "ignore"):
            return "ignore"
        print("Please enter 'i' or 's'.")


def _complete(model, prompt):
    response = client.messages.create(
        model=model,
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _merge_parts(parts, merge_prompt, model, label):
    """Reduce per-chunk results to one via `merge_prompt`. Merged in groups
    of at most MERGE_CHAR_BUDGET characters (at least two per group, so every
    round makes progress) and repeated until a single result remains.
    """
    while len(parts) > 1:
        groups, current, size = [], [], 0
        for part in parts:
            if len(current) >= 2 and size + len(part) > MERGE_CHAR_BUDGET:
                groups.append(current)
                current, size = [], 0
            current.append(part)
            size += len(part)
        groups.append(current)
        print(f"  merging {len(parts)} {label} part(s) in {len(groups)} group(s)...")
        # A leftover group of one has nothing to merge with -- carry it into
        # the next round unchanged rather than spend a call on it.
        parts = [
            group[0] if len(group) == 1 else _complete(
                model, merge_prompt.format(text="\n\n---\n\n".join(
                    f"Part {i}:\n{p}" for i, p in enumerate(group, 1))))
            for group in groups
        ]
    return parts[0]


def _derive(text_content, mode, prompt, merge_prompt, model, label):
    """One Claude pass over the first CHUNK_CHAR_LIMIT characters ("ignore", or
    text that already fits), or one pass per chunk merged into a single result
    ("split").
    """
    if mode == "ignore" or len(text_content) <= CHUNK_CHAR_LIMIT:
        return _complete(model, prompt.format(text=text_content[:CHUNK_CHAR_LIMIT]))
    chunks = _split_into_chunks(text_content)
    parts = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  {label}: part {i}/{len(chunks)}...")
        parts.append(_complete(
            model, prompt.format(text=f"(Part {i} of {len(chunks)} of the sample set.)\n\n{chunk}")))
    return _merge_parts(parts, merge_prompt, model, label)


def run_calibration(deal_type, mock=False, on_overflow="ask"):
    if not mock and not os.environ.get("ANTHROPIC_API_KEY"):
        print("[INFO] ANTHROPIC_API_KEY not found. Running calibrate.py in --mock mode.")
        mock = True

    # Read the samples first (local only: no API call, no file written) so
    # the length check below is the first decision point of the run -- see
    # _choose_overflow_mode().
    text_content = _read_sample_text()
    if not text_content:
        print("No sample PDFs found in inputs/calibration_samples/")
        return

    total_chars = len(text_content)
    mode = "split"
    if total_chars > CHUNK_CHAR_LIMIT:
        chunk_count = len(_split_into_chunks(text_content))
        if mock:
            print(f"[MOCK] Samples contain {total_chars:,} characters; a real run would "
                  f"split them into {chunk_count} parts (limit per call: {CHUNK_CHAR_LIMIT:,}).")
        else:
            mode = _choose_overflow_mode(total_chars, chunk_count, on_overflow)

    template_path = local_cam_template_path(deal_type)

    if mock:
        # Deterministic, canned output -- no Anthropic client call of any kind.
        # This only exercises the PDF-reading and file-writing plumbing so the
        # pipeline can be smoke-tested without an API key or network access;
        # it does not, and should not, attempt to derive a real style guide or
        # template, since that's the one thing this script actually does.
        os.makedirs(os.path.dirname(template_path), exist_ok=True)
        print(f"[MOCK] Read {total_chars} characters from "
              f"{len(glob.glob('inputs/calibration_samples/*.pdf'))} sample PDF(s).")
        with open("config/style_guide.md", "w", encoding="utf-8") as f:
            f.write(MOCK_STYLE_GUIDE)
        print("[MOCK] Wrote placeholder `config/style_guide.md`.")
        with open(template_path, "w", encoding="utf-8") as f:
            f.write(MOCK_TEMPLATE)
        print(f"[MOCK] Wrote placeholder template to {template_path}.")
        return

    model = _resolve_calibrate_model()

    # Both results are computed in memory and only then written together, so
    # a failure partway through (including during a long split run) leaves any
    # existing style guide and template untouched rather than a new one next
    # to a stale other.
    print("[1/2] Extracting writing style and tone...")
    style_text = _derive(text_content, mode, STYLE_PROMPT, STYLE_MERGE_PROMPT, model, "style")

    print(f"[2/2] Deriving a '{deal_type}' CAM template from your samples...")
    template_text = _derive(text_content, mode, TEMPLATE_PROMPT, TEMPLATE_MERGE_PROMPT, model, "template")

    os.makedirs(os.path.dirname(template_path), exist_ok=True)
    with open("config/style_guide.md", "w", encoding="utf-8") as f:
        f.write("# Calibrated Style Guide\n\n" + style_text)
    print("Calibration complete. Created `config/style_guide.md`.")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(template_text)
    print(f"Derived template written to {template_path} -- this overrides the "
          f"shipped default for '{deal_type}' deals until you remove it.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--type", default="corporate_credit",
        help="Deal type these samples represent, e.g. corporate_credit, asset_finance "
             "(matches orchestrator.py's --type). Defaults to corporate_credit.",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Skip all Anthropic API calls and write deterministic placeholder output "
             "instead -- for testing the PDF-reading/file-writing pipeline without an "
             "API key. Triggered automatically if ANTHROPIC_API_KEY isn't set.",
    )
    parser.add_argument(
        "--on-overflow", choices=OVERFLOW_MODES, default="ask",
        help=f"What to do when the combined samples exceed {CHUNK_CHAR_LIMIT:,} characters "
             "(the per-call limit): 'split' processes every part and merges the results; "
             "'ignore' uses only the first part and discards the rest; 'ask' (default) "
             "prompts on a terminal and falls back to 'split' when there is none.",
    )
    args = parser.parse_args()
    run_calibration(args.type, mock=args.mock, on_overflow=args.on_overflow)
