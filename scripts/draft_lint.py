"""Deterministic lint for internal framework language and unfinished scaffolding.

This module has no third-party dependencies. It is shared by CAM compliance
checks, research-brief export, and an offline command-line interface.
"""
import argparse
import json
import re
import sys


class DraftContentError(ValueError):
    """The draft itself cannot be linted (for example an unterminated HTML comment)."""


class TemplateContentError(ValueError):
    """The CAM template cannot be read for linting; the drafter cannot fix it."""


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


def _visible_text_and_lines(text, *, exclude_trailing_json=False, subject="draft"):
    """Strip HTML comments the DOCX exporter removes and preserve source line numbers."""
    error_type = DraftContentError if subject == "draft" else TemplateContentError
    if not isinstance(text, str):
        raise error_type(f"{subject.capitalize()} content must be text.")
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
        raise error_type(f"Unterminated HTML comment in {subject}.")
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
    visible, _source_lines = _visible_text_and_lines(template_text, subject="template")
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


CITATION_URL_RE = re.compile(r"https?://[^\s)\]]+")
CITATION_LINK_RE = re.compile(r"\[([^\]\r\n]{1,120})\]\((https?://[^)\s]+)\)")
CITATION_DEFINITION_RE = re.compile(r"^\[([^\]\r\n]{1,120})\]:\s*(https?://\S+)\s*$")
CITATION_SECTION_RE = re.compile(r"^ {0,3}(#{1,6})\s*(?:sources|references)\s*$", re.IGNORECASE)
CITATION_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s")
CITATION_ENTRY_RE = re.compile(r"^ {0,3}[-*]\s+(.*)$")
CITATION_KEY_RE = re.compile(r"(?<!\\)\[([^\]\r\n]{1,120})\](?!\s*\()")


def citation_link_warnings(text):
    """Return advisory warnings for citations that will not be clickable links in the export.

    Guideline 1 asks for citations a reader can click, written as
    [readable citation](url). This check reports two gaps and changes
    nothing: a bare http(s):// URL in a Sources entry that is not inside a
    [title](url) link, and an in-text [Key] that is not a link although a
    Sources entry defines that key and carries a URL (an in-text
    page-qualified key like [FY25 accounts, p. 12] matches the source key
    "FY25 accounts"). It is advisory only -- a code-enforced policy reason
    rejects a deal -- so it returns a list of {"line", "message"}
    warnings, sorted by line, and never raises: a source without a URL
    (Guideline 1 forbids inventing one) and a correctly linked citation
    both pass silently, and a draft it cannot analyse yields no warnings.
    """
    if not isinstance(text, str):
        return []
    try:
        visible, source_lines = _visible_text_and_lines(text)
    except DraftContentError:
        # An unterminated HTML comment makes the draft unanalysable; this
        # check is advisory, so it stays silent instead of raising.
        return []
    # Rebuild line-structured visible text: comment spans are gone, fenced
    # blocks are skipped, and each surviving line keeps its source number.
    fenced = _fenced_line_indexes(text.splitlines())
    lines = []
    position = 0
    while position < len(visible):
        line_number = source_lines[position] if position < len(source_lines) else 1
        end = visible.find("\n", position)
        if end < 0:
            end = len(visible)
        line_text = visible[position:end].rstrip("\r")
        if line_text and line_number - 1 not in fenced:
            lines.append((line_number, line_text))
        position = end + 1

    section_start = None
    section_level = None
    for index, (_number, line_text) in enumerate(lines):
        heading = CITATION_SECTION_RE.match(line_text)
        if heading:
            section_start = index
            section_level = len(heading.group(1))
            break
    if section_start is None:
        # No Sources or References section: no key is defined anywhere and
        # only Sources entries carry the bare-URL warning.
        return []
    section_end = len(lines)
    for offset in range(section_start + 1, len(lines)):
        heading = CITATION_HEADING_RE.match(lines[offset][1])
        # Same rule as the exporter's sources check: the section ends at the
        # next heading of the same or a higher level; a deeper heading stays.
        if heading and len(heading.group(1)) <= section_level:
            section_end = offset
            break
    body_lines = lines[:section_start] + lines[section_end:]
    entries = [
        (line_number, CITATION_ENTRY_RE.match(line_text).group(1))
        for line_number, line_text in lines[section_start + 1:section_end]
        if CITATION_ENTRY_RE.match(line_text)
    ]

    warnings = []
    defined_keys = {}
    for line_number, entry_text in entries:
        remainder = CITATION_LINK_RE.sub(" ", entry_text)
        remainder = CITATION_DEFINITION_RE.sub(" ", remainder.strip())
        has_url = bool(CITATION_URL_RE.search(entry_text))
        key = None
        link = CITATION_LINK_RE.search(entry_text)
        definition = CITATION_DEFINITION_RE.match(entry_text.strip())
        label = re.match(r"^\*\*([^*]+?):?\*\*:?\s", entry_text)
        if label:
            # The house Sources style is "- **Key:** title: url": the bold label is the key,
            # whether or not the title after it is itself a link.
            key = label.group(1).strip()
        elif link:
            key = link.group(1)
        elif definition:
            key = definition.group(1)
        elif ":" in entry_text:
            key = entry_text.split(":", 1)[0].strip()
        if key:
            defined_keys[key] = has_url or defined_keys.get(key, False)
        bare = CITATION_URL_RE.search(remainder)
        if bare:
            warnings.append((
                line_number, 1,
                f"Line {line_number}: bare URL {bare.group(0)} in a Sources entry is not a link; "
                "write [readable citation](url).",
            ))

    for line_number, line_text in body_lines:
        for match in CITATION_KEY_RE.finditer(line_text):
            key = match.group(1).strip()
            for defined_key, key_has_url in defined_keys.items():
                if not key_has_url:
                    continue
                if key == defined_key or key.startswith(defined_key + ","):
                    warnings.append((
                        line_number, 0,
                        f"Line {line_number}: in-text citation [{key}] is not a link although its source "
                        f"has a URL; write [{key}](url).",
                    ))
                    break

    warnings.sort(key=lambda item: (item[0], item[1]))
    return [{"line": line, "message": message} for line, _order, message in warnings]


def lint_draft(draft_text, *, document_kind, template_text=None):
    """Return stable, line-numbered findings for a CAM or research brief."""
    if document_kind not in {"cam", "research-brief"}:
        raise ValueError("document_kind must be 'cam' or 'research-brief'.")
    if template_text is not None and not isinstance(template_text, str):
        raise TemplateContentError("Template content must be text.")

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
