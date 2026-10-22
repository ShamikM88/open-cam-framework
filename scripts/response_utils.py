"""Shared extraction of visible text from Anthropic message responses.

Thinking and redacted-thinking blocks are not user-facing response text. A response
with no visible text is an error at the call site, never an empty draft or verdict.
"""


class EmptyModelResponseError(RuntimeError):
    """A successful API response contained no visible text block."""


class ModelResponseTruncatedError(RuntimeError):
    """A response ended at its output-token limit (stop_reason max_tokens), so its text is incomplete."""


def response_text(response):
    """Return all visible text blocks concatenated, or None when none is usable."""
    parts = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", "text") != "text":
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    joined = "".join(parts)
    return joined if joined.strip() else None


def require_response_text(response, context="model"):
    """Return visible text or fail closed with a controlled, contextual error."""
    result = response_text(response)
    if result is None:
        raise EmptyModelResponseError(f"{context} response contained no text block")
    return result


def require_complete_text(response, context, max_tokens):
    """Visible text of a response that ended normally; a max_tokens stop is checked FIRST and fails closed with one
    controlled error, so a cut-off draft or audit is never parsed, checkpointed, recorded as a verdict or exported."""
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ModelResponseTruncatedError(
            f"{context} response was truncated (stop_reason=max_tokens, max_tokens={max_tokens}); the partial text "
            "was not used and nothing from this call was checkpointed, audited or exported")
    return require_response_text(response, context)
