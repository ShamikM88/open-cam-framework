import json
import os
import threading
import time
from unittest.mock import patch

import pytest

import source_manifest
from source_manifest import read_manifest, save_source, sources_dir
from state_manager import write_state


def _local_file(tmp_path, name="downloaded.pdf", content=b"%PDF-1.4 fake content"):
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


def test_save_source_copies_the_file_into_the_deal_sources_folder(tmp_path):
    base = str(tmp_path)
    source_path = _local_file(tmp_path)

    save_source(
        "Acme Corp", "Fleet Loan", step="triage", claim="Legal identity",
        source_path=source_path, date_str="2026-01-15", base_dir=base,
    )

    directory = sources_dir("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert os.path.isfile(os.path.join(directory, "downloaded.pdf"))
    with open(os.path.join(directory, "downloaded.pdf"), "rb") as f:
        assert f.read() == b"%PDF-1.4 fake content"


def test_save_source_writes_a_manifest_entry_with_all_fields(tmp_path):
    base = str(tmp_path)
    source_path = _local_file(tmp_path)

    entry = save_source(
        "Acme Corp", "Fleet Loan", step="triage", claim="Legal identity and PSC filing",
        source_path=source_path, url="https://find-and-update.company-information.service.gov.uk/company/123",
        fetched_date="2026-01-15", date_str="2026-01-15", base_dir=base,
    )

    assert entry["step"] == "triage"
    assert entry["claim"] == "Legal identity and PSC filing"
    assert entry["url"] == "https://find-and-update.company-information.service.gov.uk/company/123"
    assert entry["fetched_date"] == "2026-01-15"
    assert entry["filename"]

    manifest = read_manifest("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert manifest == [entry]


def test_save_source_derives_filename_from_url_when_no_explicit_filename(tmp_path):
    base = str(tmp_path)
    source_path = _local_file(tmp_path, name="tmp8f2a.html")  # meaningless scratch name

    entry = save_source(
        "Acme Corp", "Fleet Loan", step="commercial",
        claim="Company website About page",
        source_path=source_path, url="https://example.com/about-us",
        date_str="2026-01-15", base_dir=base,
    )

    assert "example.com" in entry["filename"]
    assert entry["filename"].endswith(".html")
    assert entry["filename"] != "tmp8f2a.html"


def test_save_source_falls_back_to_source_path_basename_when_no_url(tmp_path):
    """An analyst-provided document with no URL at all -- the only name
    available is the file's own basename."""
    base = str(tmp_path)
    source_path = _local_file(tmp_path, name="analyst_spreadsheet.xlsx")

    entry = save_source(
        "Acme Corp", "Fleet Loan", step="spread", claim="Analyst-supplied pre-spread template",
        source_path=source_path, date_str="2026-01-15", base_dir=base,
    )

    assert entry["filename"] == "analyst_spreadsheet.xlsx"
    assert entry["url"] is None


def test_save_source_respects_explicit_filename_override(tmp_path):
    base = str(tmp_path)
    source_path = _local_file(tmp_path, name="raw_download.tmp")

    entry = save_source(
        "Acme Corp", "Fleet Loan", step="triage", claim="Charges register",
        source_path=source_path, url="https://example.com/charges",
        filename="sc123456_charges.html", date_str="2026-01-15", base_dir=base,
    )

    assert entry["filename"] == "sc123456_charges.html"


def test_save_source_never_overwrites_a_filename_collision(tmp_path):
    """Two different sources that would otherwise derive the same filename
    -- e.g. two pages under the same domain slugifying to something close
    -- must both survive on disk, not have the second silently clobber the
    first."""
    base = str(tmp_path)

    entry1 = save_source(
        "Acme Corp", "Fleet Loan", step="triage", claim="First fetch",
        source_path=_local_file(tmp_path, name="a.html", content=b"first"),
        filename="report.html", date_str="2026-01-15", base_dir=base,
    )
    entry2 = save_source(
        "Acme Corp", "Fleet Loan", step="commercial", claim="Second fetch",
        source_path=_local_file(tmp_path, name="b.html", content=b"second"),
        filename="report.html", date_str="2026-01-15", base_dir=base,
    )

    assert entry1["filename"] == "report.html"
    assert entry2["filename"] == "report_2.html"
    directory = sources_dir("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    with open(os.path.join(directory, "report.html"), "rb") as f:
        assert f.read() == b"first"
    with open(os.path.join(directory, "report_2.html"), "rb") as f:
        assert f.read() == b"second"


def test_save_source_requires_step_and_claim(tmp_path):
    base = str(tmp_path)
    source_path = _local_file(tmp_path)

    with pytest.raises(ValueError, match="step and claim"):
        save_source("Acme Corp", "Fleet Loan", step="", claim="Something",
                     source_path=source_path, date_str="2026-01-15", base_dir=base)

    with pytest.raises(ValueError, match="step and claim"):
        save_source("Acme Corp", "Fleet Loan", step="triage", claim="",
                     source_path=source_path, date_str="2026-01-15", base_dir=base)


def test_save_source_requires_an_existing_file(tmp_path):
    base = str(tmp_path)
    with pytest.raises(ValueError, match="existing file"):
        save_source("Acme Corp", "Fleet Loan", step="triage", claim="Something",
                     source_path=str(tmp_path / "does_not_exist.pdf"), date_str="2026-01-15", base_dir=base)


def test_read_manifest_returns_empty_list_when_nothing_saved_yet(tmp_path):
    base = str(tmp_path)
    assert read_manifest("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base) == []


def test_read_manifest_returns_entries_in_the_order_they_were_saved(tmp_path):
    base = str(tmp_path)
    save_source("Acme Corp", "Fleet Loan", step="triage", claim="First",
                source_path=_local_file(tmp_path, name="one.pdf"), date_str="2026-01-15", base_dir=base)
    save_source("Acme Corp", "Fleet Loan", step="commercial", claim="Second",
                source_path=_local_file(tmp_path, name="two.pdf"), date_str="2026-01-15", base_dir=base)

    manifest = read_manifest("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert [e["claim"] for e in manifest] == ["First", "Second"]


def test_save_source_reuses_the_dated_folder_state_json_already_wrote(tmp_path):
    """Source material belongs alongside whatever state.json a prior step
    already wrote for this deal -- it must not create a second,
    differently-dated folder of its own (matches research_export.py's own
    reuse-the-existing-folder behavior)."""
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2025-06-01", base_dir=base, triage={"go_no_go": "GO"})

    entry = save_source("Acme Corp", "Fleet Loan", step="triage", claim="Legal identity",
                         source_path=_local_file(tmp_path), base_dir=base)

    directory = sources_dir("Acme Corp", "Fleet Loan", base_dir=base)
    assert "Fleet Loan_2025-06-01" in directory
    assert os.path.isfile(os.path.join(directory, entry["filename"]))


def test_concurrent_save_source_calls_do_not_clobber_each_others_file(tmp_path):
    """Without a lock covering the whole filename-collision-check -> copy
    sequence (not just the manifest write), two concurrent save_source()
    calls deriving the same filename could both pass
    _unique_dest_path()'s existence check before either copies its file,
    and the second copy would silently overwrite the first's document --
    exactly the failure mode _FileLock exists to prevent (see
    state_manager.py's own concurrent-write tests).

    A real copyfile() is too fast for plain thread-scheduling luck to
    reliably land two threads inside that window, so this patches in an
    artificial delay to widen it deterministically -- this does not risk a
    deadlock either way: with the fix, the second thread simply blocks on
    the lock for the whole (slowed) critical section and correctly derives
    a distinct filename afterwards; without it, the delay gives the second
    thread's _unique_dest_path() call time to run *before* the first
    thread's copy lands, reliably reproducing the clobber.
    """
    base = str(tmp_path)
    file_a = _local_file(tmp_path, name="a.pdf", content=b"content A")
    file_b = _local_file(tmp_path, name="b.pdf", content=b"content B")

    real_copyfile = source_manifest.shutil.copyfile

    def slow_copyfile(*args, **kwargs):
        time.sleep(0.1)
        return real_copyfile(*args, **kwargs)

    def saver(source_path):
        save_source("Acme Corp", "Fleet Loan", step="triage", claim="Concurrent fetch",
                     source_path=source_path, filename="shared.pdf",
                     date_str="2026-01-15", base_dir=base)

    with patch.object(source_manifest.shutil, "copyfile", side_effect=slow_copyfile):
        t1 = threading.Thread(target=saver, args=(file_a,))
        t2 = threading.Thread(target=saver, args=(file_b,))
        t1.start()
        time.sleep(0.02)  # ensure t1 enters its critical section first
        t2.start()
        t1.join()
        t2.join()

    manifest = read_manifest("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert len(manifest) == 2
    filenames = {e["filename"] for e in manifest}
    assert filenames == {"shared.pdf", "shared_2.pdf"}

    directory = sources_dir("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    contents = set()
    for fn in filenames:
        with open(os.path.join(directory, fn), "rb") as f:
            contents.add(f.read())
    assert contents == {b"content A", b"content B"}


def test_sources_dir_does_not_create_the_directory_by_itself(tmp_path):
    """A read-only lookup (e.g. read_manifest() on a deal with no sources
    yet) must not have the side effect of creating an empty sources/
    folder -- only save_source() actually writing something should."""
    base = str(tmp_path)
    directory = sources_dir("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert not os.path.exists(directory)
