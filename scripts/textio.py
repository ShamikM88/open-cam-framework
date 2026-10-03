"""The text-reading policy for every file `orchestrator.py` loads into a prompt
(issue #137), plus `configure_stdio()`, which gives every CLI a UTF-8
stdout/stderr (issue #154). JSON config files (`settings.json`) and the writers
are covered by the same UTF-8 convention but read/written with
`open(..., encoding="utf-8")` directly.

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


def configure_stdio():
    """Make stdout/stderr UTF-8 when they are not already (issue #154).

    When a Windows script's stdout is redirected or piped (CI logs, Task
    Scheduler, `> log.txt`), Python encodes it with the locale encoding
    (cp1252), so a `print()` of any character cp1252 lacks -- a `>=` sign in
    the Risk Reviewer's notes, a letter in a company name -- raises
    `UnicodeEncodeError` and aborts the run, after the verdict is already
    decided but before the revision or export. An interactive console is
    unaffected (it already reports UTF-8), and so is a stream that is already
    UTF-8: those are left alone.

    UTF-8 can represent every character, so no lossy `errors="replace"` is
    needed; each stream keeps its existing error policy (stderr's
    backslashreplace stays). Streams that are `None` (a windowed process) or
    can't be reconfigured (a test capture object) are skipped. Call this once
    at the top of a script's `if __name__ == "__main__":` block -- not from
    functions, so importing a module never touches the interpreter's streams.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None or getattr(stream, "closed", False):
            continue
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
        if encoding == "utf8":
            continue
        reconfigure(encoding="utf-8", errors=stream.errors)


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
        if "\x00" in text:
            # cp1252 maps almost every byte, so it will "decode" a UTF-16 file
            # (e.g. one written by a PowerShell 5.1 `>` redirect) into NUL-laced
            # garbage; refuse that rather than put it in a prompt.
            raise TextEncodingError(
                f"{path} is not valid UTF-8 and contains NUL bytes under cp1252, so it looks like "
                "UTF-16 or a binary file. Re-save it as UTF-8."
            )
        print(f"[WARN] {path} is not valid UTF-8; read it as legacy cp1252. "
              "Re-save it as UTF-8 so it reads the same on every machine.", file=sys.stderr)
        return text
