"""Pattern exchange files between FL's piano roll scripts and the relay (v0.2)."""

import json
import os
import time
from pathlib import Path

import pytest
from flmp_relay.patterns import INBOX, OUTBOX, Mailbox

NOTES = [[69, 192, 48, 0.78125, 0.5, 0.5]]


def later():
    """A wall clock 10 s ahead: files written by the test count as settled."""
    return time.time() + 10


def write_outbox(folder, ppq=96, notes=NOTES, mtime=None):
    path = folder / OUTBOX
    path.write_text(json.dumps({"flmp": 1, "ppq": ppq, "notes": notes}), encoding="utf-8")
    if mtime is not None:
        os.utime(path, ns=(mtime, mtime))
    return path


def test_creates_the_folder(tmp_path):
    Mailbox(tmp_path / "FL Multiplayer")
    assert (tmp_path / "FL Multiplayer").is_dir()


def test_outbox_present_at_start_is_not_resent(tmp_path):
    write_outbox(tmp_path)
    box = Mailbox(tmp_path)
    assert box.take_outbox() is None


def test_new_outbox_is_taken_once(tmp_path):
    box = Mailbox(tmp_path)
    assert box.take_outbox() is None
    write_outbox(tmp_path, mtime=1_000_000_000)
    assert box.take_outbox() == (96, [(69, 192, 48, round(0.78125, 4), 0.5, 0.5)])
    assert box.take_outbox() is None
    write_outbox(tmp_path, ppq=192, mtime=2_000_000_000)  # sent again, even if identical
    assert box.take_outbox()[0] == 192


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", json.dumps({"ppq": 96}), json.dumps({"ppq": 96, "notes": [[999]]})],
)
def test_invalid_outbox_is_skipped(tmp_path, content, caplog):
    box = Mailbox(tmp_path, wall_clock=later)
    (tmp_path / OUTBOX).write_text(content, encoding="utf-8")
    assert box.take_outbox() is None
    assert "ignoré" in caplog.text


def test_oversized_outbox_is_skipped(tmp_path, caplog):
    box = Mailbox(tmp_path, wall_clock=later)
    (tmp_path / OUTBOX).write_text(" " * (Mailbox.MAX_BYTES + 1), encoding="utf-8")
    assert box.take_outbox() is None
    assert "ignoré" in caplog.text


def test_inbox_is_written_atomically(tmp_path):
    box = Mailbox(tmp_path)
    box.write_inbox("Bob", 96, [(60, 0, 96, 1.0, 0.5, 0.5)])
    data = json.loads((tmp_path / INBOX).read_text(encoding="utf-8"))
    assert data == {"flmp": 1, "from": "Bob", "ppq": 96, "notes": [[60, 0, 96, 1.0, 0.5, 0.5]]}
    assert sorted(p.name for p in tmp_path.iterdir()) == [INBOX]  # no temp file left


def test_new_file_with_same_mtime_and_size_is_still_taken(tmp_path):
    # Coarse timestamps: the script replaces the file, so its inode changes.
    box = Mailbox(tmp_path)
    write_outbox(tmp_path, mtime=1_000_000_000)
    assert box.take_outbox() is not None
    tmp = tmp_path / "other.json"
    tmp.write_text((tmp_path / OUTBOX).read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(tmp, ns=(1_000_000_000, 1_000_000_000))
    os.replace(tmp, tmp_path / OUTBOX)
    assert box.take_outbox() is not None


def test_deeply_nested_outbox_does_not_raise(tmp_path, caplog):
    box = Mailbox(tmp_path, wall_clock=later)
    (tmp_path / OUTBOX).write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    assert box.take_outbox() is None
    assert "ignoré" in caplog.text


def test_inbox_keeps_the_latest_pattern_when_they_come_too_fast(tmp_path):
    now = [100.0]
    box = Mailbox(tmp_path, clock=lambda: now[0])
    assert box.write_inbox("A", 96, [(60, 0, 1, 0.5, 0.5, 0.5)]) is True
    now[0] += 0.3
    assert box.write_inbox("A", 96, [(61, 0, 1, 0.5, 0.5, 0.5)]) is False  # held, not lost
    assert box.write_inbox("A", 96, [(62, 0, 1, 0.5, 0.5, 0.5)]) is False  # latest wins
    assert box.flush_inbox() is None  # still too soon
    now[0] += 1.0
    assert box.flush_inbox() == ("A", 1)
    assert json.loads((tmp_path / INBOX).read_text(encoding="utf-8"))["notes"][0][0] == 62
    assert box.flush_inbox() is None


def test_failed_inbox_write_does_not_use_up_the_interval(tmp_path, monkeypatch):
    now = [100.0]
    box = Mailbox(tmp_path, clock=lambda: now[0])
    real = Path.write_text

    def fail_once(self, *a, **k):
        monkeypatch.setattr(Path, "write_text", real)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fail_once)
    with pytest.raises(OSError):
        box.write_inbox("A", 96, [])
    assert box.write_inbox("A", 96, []) is True  # right away: the failure didn't count


def test_empty_outbox_is_left_for_later_without_warning(tmp_path, caplog):
    box = Mailbox(tmp_path, wall_clock=later)
    (tmp_path / OUTBOX).write_text("", encoding="utf-8")  # truncated, not written yet
    assert box.take_outbox() is None
    assert "ignoré" not in caplog.text
    write_outbox(tmp_path)
    assert box.take_outbox() is not None


def test_sender_name_is_sanitised(tmp_path):
    box = Mailbox(tmp_path)
    box.write_inbox("Bob\x1b[2J\n" + "x" * 100, 96, [])
    sender = json.loads((tmp_path / INBOX).read_text(encoding="utf-8"))["from"]
    assert sender.isprintable() and len(sender) <= 40 and sender.startswith("Bob")


def test_outbox_still_being_written_is_left_for_later(tmp_path):
    # The FL script can't write atomically: wait until the file has settled.
    now = [2_000.0]
    box = Mailbox(tmp_path, wall_clock=lambda: now[0])
    write_outbox(tmp_path, mtime=int(2_000.0 * 1e9))  # written just now
    assert box.take_outbox() is None
    now[0] += 0.5
    assert box.take_outbox() is not None


def test_note_count_wording():
    from flmp_relay.patterns import notes_label

    assert notes_label(1) == "1 note"
    assert notes_label(3) == "3 notes"
