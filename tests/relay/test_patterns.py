"""Pattern exchange files between FL's piano roll scripts and the relay (v0.2)."""

import json
import os

import pytest
from flmp_relay.patterns import INBOX, OUTBOX, Mailbox

NOTES = [[69, 192, 48, 0.78125, 0.5, 0.5]]


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
    box = Mailbox(tmp_path)
    (tmp_path / OUTBOX).write_text(content, encoding="utf-8")
    assert box.take_outbox() is None
    assert "ignoré" in caplog.text


def test_oversized_outbox_is_skipped(tmp_path, caplog):
    box = Mailbox(tmp_path)
    (tmp_path / OUTBOX).write_text(" " * (Mailbox.MAX_BYTES + 1), encoding="utf-8")
    assert box.take_outbox() is None
    assert "ignoré" in caplog.text


def test_inbox_is_written_atomically(tmp_path):
    box = Mailbox(tmp_path)
    box.write_inbox("Bob", 96, [(60, 0, 96, 1.0, 0.5, 0.5)])
    data = json.loads((tmp_path / INBOX).read_text(encoding="utf-8"))
    assert data == {"flmp": 1, "from": "Bob", "ppq": 96, "notes": [[60, 0, 96, 1.0, 0.5, 0.5]]}
    assert sorted(p.name for p in tmp_path.iterdir()) == [INBOX]  # no temp file left
