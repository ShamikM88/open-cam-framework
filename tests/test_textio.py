"""Tests for scripts/textio.py: the repository's one text-reading policy
(issue #137) -- strict UTF-8 for shipped files, UTF-8-then-cp1252 with a loud
warning for user-owned legacy files, and never a silent character swap.
"""
import pytest

from textio import TextEncodingError, read_text

# cp1252 cannot encode the box-drawing characters, so a legacy (cp1252) file
# can only hold the cp1252-representable part.
UTF8_TEXT = "Facility £1.2m — Álvarez ├── │ └──"
CP1252_TEXT = "Facility £1.2m – Álvarez"


def _write_bytes(tmp_path, data, name="sample.md"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def test_utf8_file_round_trips_exactly(tmp_path):
    path = _write_bytes(tmp_path, UTF8_TEXT.encode("utf-8"))
    assert read_text(path) == UTF8_TEXT
    assert read_text(path, legacy_fallback=True) == UTF8_TEXT


def test_utf8_byte_order_mark_is_stripped(tmp_path):
    path = _write_bytes(tmp_path, b"\xef\xbb\xbf" + UTF8_TEXT.encode("utf-8"))
    assert read_text(path) == UTF8_TEXT


def test_utf8_file_in_legacy_mode_prints_no_warning(tmp_path, capsys):
    path = _write_bytes(tmp_path, UTF8_TEXT.encode("utf-8"))
    read_text(path, legacy_fallback=True)
    assert capsys.readouterr().err == ""


def test_strict_mode_fails_loudly_on_non_utf8_and_names_the_file(tmp_path):
    path = _write_bytes(tmp_path, CP1252_TEXT.encode("cp1252"), name="underwriter_agent.md")
    with pytest.raises(TextEncodingError, match="underwriter_agent.md"):
        read_text(path)


def test_strict_mode_never_substitutes_replacement_characters(tmp_path):
    path = _write_bytes(tmp_path, CP1252_TEXT.encode("cp1252"))
    with pytest.raises(TextEncodingError):
        read_text(path)  # raising, not returning text containing U+FFFD


def test_legacy_mode_reads_cp1252_and_warns_naming_the_file(tmp_path, capsys):
    path = _write_bytes(tmp_path, CP1252_TEXT.encode("cp1252"), name="style_guide.md")
    assert read_text(path, legacy_fallback=True) == CP1252_TEXT
    err = capsys.readouterr().err
    assert "[WARN]" in err and "style_guide.md" in err and "cp1252" in err


def test_legacy_mode_fails_when_neither_encoding_decodes(tmp_path):
    # 0x81 and 0x8d are undefined in cp1252 and invalid as UTF-8.
    path = _write_bytes(tmp_path, b"abc \x81\x8d def", name="credit_policy.md")
    with pytest.raises(TextEncodingError, match="credit_policy.md"):
        read_text(path, legacy_fallback=True)


def test_text_encoding_error_is_a_value_error(tmp_path):
    path = _write_bytes(tmp_path, b"\x81")
    with pytest.raises(ValueError):
        read_text(path)


def test_legacy_mode_refuses_a_utf16_file_that_cp1252_would_decode_to_nul_garbage(tmp_path):
    # e.g. a file written by a PowerShell 5.1 `>` redirect: not UTF-8, but
    # cp1252 maps almost every byte, so without the NUL check it "decodes".
    path = _write_bytes(tmp_path, "Policy text".encode("utf-16"), name="credit_policy.md")
    with pytest.raises(TextEncodingError, match="UTF-16"):
        read_text(path, legacy_fallback=True)


def test_the_shipped_agent_prompts_are_valid_strict_utf8():
    # A stray non-UTF-8 byte committed to agents/ must fail here in CI, not at
    # runtime on someone's machine (issue #137).
    from pathlib import Path
    agents = sorted((Path(__file__).resolve().parents[1] / "agents").glob("*.md"))
    assert agents, "expected agents/*.md to exist"
    for path in agents:
        assert read_text(path)
