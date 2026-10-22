from types import SimpleNamespace

import pytest

from response_utils import EmptyModelResponseError, require_response_text, response_text


def test_response_text_joins_visible_text_blocks_around_thinking():
    response = SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking="hidden chain"),
        SimpleNamespace(type="text", text="first"),
        SimpleNamespace(type="redacted_thinking", data="opaque"),
        SimpleNamespace(type="text", text=" second"),
    ])

    assert response_text(response) == "first second"


@pytest.mark.parametrize("response", [
    SimpleNamespace(content=[]),
    SimpleNamespace(content=None),
    SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="hidden only")]),
    SimpleNamespace(content=[SimpleNamespace(type="redacted_thinking", data="opaque")]),
    SimpleNamespace(content=[SimpleNamespace(type="text", text="  ")]),
])
def test_response_text_returns_none_without_visible_nonblank_text(response):
    assert response_text(response) is None


def test_require_response_text_fails_closed_with_context():
    response = SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="hidden only")])

    with pytest.raises(EmptyModelResponseError, match="Checker audit response contained no text block"):
        require_response_text(response, "Checker audit")
