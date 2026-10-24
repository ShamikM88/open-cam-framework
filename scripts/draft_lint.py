"""Deterministic lint for internal framework language and unfinished scaffolding.

This module has no third-party dependencies. It is shared by CAM compliance
checks, research-brief export, and an offline command-line interface.
"""
import argparse
import json
import re
import sys


FENCE_OPEN_RE = re.compile(r"^ {0,3}(\x60{3,}|~{3,})(.*)$")
JSON_INFO_RE = re.compile(r"json[ \t]*", re.IGNORECASE)
GUIDANCE_COMMENT_RE = re.compile(r"<!--\s*Guidance:\s*(.*?)\s*-->", re.IGNORECASE | re.DOTALL)
TEMPLATE_PLACEHOLDER_RE = re.compile(r"(?<!\\)\[([^\]\r\n]{1,120})\](?!\s*\()", re.DOTALL)
OBVIOUS_PLACEHOLDER_RE = re.compile(
    r"\[(?:name|date|amount|value|placeholder|todo|tbd|"
    r"company name|borrower(?: legal)? name|borrower|company|"
    r"internal reference|registration number|amount\s*(?:&|and)\s*currency|"
    r"name\s+or\s+team|insert(?:\s+[^\]\r\n]{1,80})?|"
    r"enter(?:\s+[^\]\r\n]{1,80})?)\](?!\()",
    re.IGNORECASE,
)

FORBIDDEN_PATTERNS = (
    ("state.json", re.compile(r"(?<!\w)state\.json(?!\w)", re.IGNORECASE)),
    ("steps_completed", re.compile(r"(?<!\w)steps_completed(?!\w)", re.IGNORECASE)),
    ("financials_source", re.compile(r"(?<!\w)financials_source(?!\w)", re.IGNORECASE)),
    ("policy_state", re.compile(r"(?<!\w)policy_state(?!\w)", re.IGNORECASE)),
    ("cp_id", re.compile(r"(?<!\w)cp_ids?(?:_[A-Za-z0-9_]+)?(?!\w)", re.IGNORECASE)),
    ("Guideline number", re.compile(r"\bGuideline\s+\d+\b", re.IGNORECASE)),
    ("Audit Checklist item number", re.compile(r"\bAudit\s+Checklist\s+item\s+\d+\b", re.IGNORECASE)),
    ("slash command", re.compile(
        r"(?<![\w-])/(?:assemble|calibrate-policy|calibrate|collateral|commercial|"
        r"committee-summary|project|research|review|spread|start-review|triage)(?![\w-])",
        re.IGNORECASE,
    )),
    ("this framework", re.compile(r"\bthis\s+framework\b", re.IGNORECASE)),
    ("this session", re.compile(r"\bthis\s+session\b", re.IGNORECASE)),
    ("deterministic check", re.compile(r"\bdeterministic\s+checks?\b", re.IGNORECASE)),
    ("code-enforced", re.compile(r"\bcode[\s-]+enforced\b", re.IGNORECASE)),
    ("Underwriter Agent", re.compile(r"\bUnderwriter\s+Agent\b", re.IGNORECASE)),
    ("OpenCAM", re.compile(r"(?<![A-Za-z0-9_])OpenCAM(?![A-Za-z0-9_])", re.IGNORECASE)),
)


def _fence_blocks(lines):
    """Return (opening index, closing index, info string) for each complete Markdown fence; unclosed ones are skipped."""
    blocks = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip("\r\n")
        opening = FENCE_OPEN_RE.match(line)
        if not opening or (opening.group(1)[0] == chr(96) and chr(96) in opening.group(2)):
            index += 1
            continue
        fence_char = opening.group(1)[0]
        fence_length = len(opening.group(1))
        close_re = re.compile(rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*$")
        close_index = None
        for candidate in range(index + 1, len(lines)):
            if close_re.match(lines[candidate].rstrip("\r\n")):
                close_index = candidate
                break
        if close_index is None:
            index += 1
            continue
        blocks.append((index, close_index, opening.group(2).strip()))
        index = close_index + 1
    return blocks


def _fenced_line_indexes(lines):
    """Return indexes inside complete Markdown fences; unclosed fences are not code."""
    fenced = set()
    for opening, closing, _info in _fence_blocks(lines):
        fenced.update(range(opening, closing + 1))
    return fenced


def _trailing_json_start(text):
    """Character offset where the final complete ```json fence begins, if only blank lines follow it; else None.

    Only that one block is the Underwriter's structured output. An earlier JSON example, or any prose between it and
    the real block, stays client-facing text and must still be linted.
    """
    lines = text.splitlines(keepends=True)
    blocks = _fence_blocks(lines)
    if not blocks:
        return None
    opening, closing, info = blocks[-1]
    if not JSON_INFO_RE.fullmatch(info) or any(line.strip() for line in lines[closing + 1:]):
        return None
    return sum(len(line) for line in lines[:opening])


def _visible_text_and_lines(text, *, exclude_trailing_json=False):
    """Strip HTML comments the DOCX exporter removes and preserve source line numbers."""
    if not isinstance(text, str):
        raise ValueError("Draft content must be text.")
    excluded = _trailing_json_start(text) if exclude_trailing_json else None
    limited_text = text if excluded is None else text[:excluded]
    lines = limited_text.splitlines(keepends=True)
    fenced = _fenced_line_indexes(lines)
    visible = []
    source_lines = []
    in_comment = False

    for line_index, line in enumerate(lines):
        if not in_comment and line_index in fenced:
            visible.extend(line)
            source_lines.extend([line_index + 1] * len(line))
            continue

        position = 0
        while position < len(line):
            if in_comment:
                closing = line.find("-->", position)
                if closing < 0:
                    position = len(line)
                else:
                    in_comment = False
                    position = closing + 3
                continue

            opening = line.find("<!--", position)
            if opening < 0:
                segment = line[position:]
                visible.extend(segment)
                source_lines.extend([line_index + 1] * len(segment))
                break
            segment = line[position:opening]
            visible.extend(segment)
            source_lines.extend([line_index + 1] * len(segment))
            in_comment = True
            position = opening + 4

    if in_comment:
        raise ValueError("Unterminated HTML comment in draft.")
    return "".join(visible), source_lines


def _normalised_search(text, source_lines):
    """Casefold, collapse whitespace and remove simple Markdown emphasis delimiters.

    Returns the normalised text, the source line of each character and its offset in `text`, so a finding can quote
    the draft's own spelling rather than the casefolded form used for matching.
    """
    characters = []
    mapped_lines = []
    mapped_offsets = []
    index = 0
    while index < len(text):
        character = text[index]
        if character in "*_":
            end = index + 1
            while end < len(text) and text[end] == character:
                end += 1
            run_length = end - index
            before_word = index > 0 and (text[index - 1].isalnum() or text[index - 1] == "_")
            after_word = end < len(text) and (text[end].isalnum() or text[end] == "_")
            opens = run_length <= 2 and not before_word and end < len(text) and not text[end].isspace()
            closes = run_length <= 2 and index > 0 and not text[index - 1].isspace() and not after_word
            if opens or closes:
                index = end
                continue
        if character.isspace():
            if characters and characters[-1] != " ":
                characters.append(" ")
                mapped_lines.append(source_lines[index])
                mapped_offsets.append(index)
        else:
            for folded in character.casefold():
                characters.append(folded)
                mapped_lines.append(source_lines[index])
                mapped_offsets.append(index)
        index += 1

    while characters and characters[-1] == " ":
        characters.pop()
        mapped_lines.pop()
        mapped_offsets.pop()
    if characters and characters[0] == " ":
        characters.pop(0)
        mapped_lines.pop(0)
        mapped_offsets.pop(0)
    return "".join(characters), mapped_lines, mapped_offsets


def _normalise_phrase(text):
    return _normalised_search(text, [1] * len(text))[0]


def _template_guidance(template_text):
    lines = template_text.splitlines(keepends=True)
    fenced = _fenced_line_indexes(lines)
    result = []
    for match in GUIDANCE_COMMENT_RE.finditer(template_text):
        if template_text.count("\n", 0, match.start()) in fenced:
            continue
        phrase = " ".join(match.group(1).split())
        if phrase:
            result.append(phrase)
    return result


def _template_placeholders(template_text):
    visible, _source_lines = _visible_text_and_lines(template_text)
    tokens = {}
    for match in TEMPLATE_PLACEHOLDER_RE.finditer(visible):
        token = match.group(0)
        normalized = _normalise_phrase(token)
        if normalized:
            tokens.setdefault(normalized, token)
    return tokens


def _finding(rule, matched_text, line):
    matched = " ".join(str(matched_text).split())
    if rule == "A":
        message = f"Rule A: forbidden framework vocabulary {matched!r} at line {line}."
    elif rule == "B_TEMPLATE_GUIDANCE":
        message = f"Rule B: copied template guidance {matched!r} at line {line}."
    else:
        message = f"Rule B: unfilled template placeholder {matched!r} at line {line}."
    return {"rule": rule, "match": matched, "line": line, "message": message}


def lint_draft(draft_text, *, document_kind, template_text=None):
    """Return stable, line-numbered findings for a CAM or research brief."""
    if document_kind not in {"cam", "research-brief"}:
        raise ValueError("document_kind must be 'cam' or 'research-brief'.")
    if template_text is not None and not isinstance(template_text, str):
        raise ValueError("Template content must be text.")

    visible, source_lines = _visible_text_and_lines(
        draft_text, exclude_trailing_json=(document_kind == "cam"),
    )
    findings = []
    for _label, pattern in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(visible):
            line = source_lines[match.start()] if match.start() < len(source_lines) else 1
            findings.append(_finding("A", match.group(0), line))

    normalized, normalized_lines, normalized_offsets = _normalised_search(visible, source_lines)
    seen_placeholders = set()

    if document_kind == "cam" and template_text is not None:
        for phrase in _template_guidance(template_text):
            needle = _normalise_phrase(phrase)
            start = 0
            while needle:
                position = normalized.find(needle, start)
                if position < 0:
                    break
                line = normalized_lines[position] if position < len(normalized_lines) else 1
                findings.append(_finding("B_TEMPLATE_GUIDANCE", phrase, line))
                start = position + max(1, len(needle))

        for needle, display in _template_placeholders(template_text).items():
            start = 0
            while True:
                position = normalized.find(needle, start)
                if position < 0:
                    break
                span = (position, position + len(needle))
                if span not in seen_placeholders:
                    line = normalized_lines[position] if position < len(normalized_lines) else 1
                    findings.append(_finding("B_UNFILLED_PLACEHOLDER", display, line))
                    seen_placeholders.add(span)
                start = position + max(1, len(needle))

    for match in OBVIOUS_PLACEHOLDER_RE.finditer(normalized):
        if match.span() in seen_placeholders:
            continue
        line = normalized_lines[match.start()] if match.start() < len(normalized_lines) else 1
        original = visible[normalized_offsets[match.start()]:normalized_offsets[match.end() - 1] + 1]
        findings.append(_finding("B_UNFILLED_PLACEHOLDER", original, line))
        seen_placeholders.add(match.span())

    findings.sort(key=lambda item: (item["line"], item["rule"], item["match"].casefold()))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Lint a CAM or research brief for internal framework language and unfilled placeholders.",
    )
    parser.add_argument("--draft", required=True, help="Path to the Markdown draft")
    parser.add_argument("--kind", required=True, choices=("cam", "research-brief"),
                        help="Document kind; research briefs have no CAM template")
    parser.add_argument("--template", help="Optional exact resolved CAM template used for this draft")
    args = parser.parse_args(argv)

    try:
        with open(args.draft, encoding="utf-8") as source:
            draft_text = source.read()
        template_text = None
        if args.template:
            with open(args.template, encoding="utf-8") as source:
                template_text = source.read()
        findings = lint_draft(draft_text, document_kind=args.kind, template_text=template_text)
    except (OSError, ValueError) as exc:
        print(f"error: {' '.join(str(exc).split())}", file=sys.stderr)
        return 1

    print(json.dumps({"findings": findings}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    raise SystemExit(main())
