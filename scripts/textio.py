"""The repository's one text-reading policy (issue #137).

Every text file this framework reads or writes is UTF-8. Python's default for
`open()` is the platform's locale encoding though -- cp1252 on Windows -- so a
file written or read without an explicit `encoding=` silently changes meaning
between machines: an em dash or a box-drawing character in a UTF-8 file arrives
as mojibake (the Guideline 12 ownership-tree example reached the model as
`â”œâ”€â”€`), and a stray byte can abort a run outright.

`read_text()` is the reader for the files `orchestrator.py` loads into a prompt.
Writers just pass `encoding="utf-8"` to `open()`; ruff's PLW1514 (see issue
#139) flags any `open()` that forgets.

No `anthropic` dependency, same testability pattern as `state_manager.py`.
"""
import sys


class TextEncodingError(ValueError):
    """A file could not be decoded under this repo's encoding policy. The
    message always names the file, so a failed run says which one to fix."""


def read_text(path, legacy_fallback=False):
    """Read `path` as UTF-8 (a leading byte-order mark, as Windows editors
    add, is tolerated and stripped).

    `legacy_fallback=False` is for shipped, repository-owned files
    (`agents/*.md`): they are UTF-8 by construction, so a decode failure is a
    bug to fix and fails loudly -- never silently repaired.

    `legacy_fallback=True` is for user-owned files that may predate this
    policy (`config/style_guide.md`, the credit-policy files, deal learnings).
    The only known non-UTF-8 writer was `calibrate.py` on Windows, which wrote
    `config/style_guide.md` in cp1252. If such a file is not valid UTF-8 it is
    read as cp1252 and a warning naming the file is printed to stderr, so the
    fallback is never silent. A file that decodes under neither raises.

    Nothing here ever substitutes characters (`errors="replace"`): a `£`
    quietly turned into `?` in a policy or a style guide is worse than a
    failure.
    """
    path = str(path)
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except UnicodeDecodeError as utf8_error:
        if not legacy_fallback:
            raise TextEncodingError(
                f"{path} is not valid UTF-8 ({utf8_error.reason} at byte {utf8_error.start}). "
                "Repository-owned files must be UTF-8; re-save it as UTF-8."
            ) from utf8_error
        try:
            with open(path, encoding="cp1252") as f:
                text = f.read()
        except UnicodeDecodeError as legacy_error:
            raise TextEncodingError(
                f"{path} is neither valid UTF-8 nor valid cp1252 "
                f"({legacy_error.reason} at byte {legacy_error.start}). Re-save it as UTF-8."
            ) from legacy_error
        print(f"[WARN] {path} is not valid UTF-8; read it as legacy cp1252. "
              "Re-save it as UTF-8 so it reads the same on every machine.", file=sys.stderr)
        return text
