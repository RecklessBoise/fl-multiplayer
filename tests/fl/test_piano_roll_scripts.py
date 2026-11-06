"""Loads the real piano roll scripts (.pyscript) with a fake `flpianoroll` module."""

import ast
import json
import sys
import types
from pathlib import Path

import pytest

FOLDER = Path(__file__).resolve().parents[2] / "fl_script" / "piano_roll"
SEND = FOLDER / "FL Multiplayer - Envoyer.pyscript"
RECEIVE = FOLDER / "FL Multiplayer - Recevoir.pyscript"


class FakeNote:
    def __init__(self, number=60, time=0, length=96, velocity=0.78125, pan=0.5, release=0.5):
        self.number, self.time, self.length = number, time, length
        self.velocity, self.pan, self.release = velocity, pan, release
        self.selected = False


class FakeScore:
    def __init__(self, ppq=96):
        self.PPQ = ppq
        self.notes = []

    @property
    def noteCount(self):
        return len(self.notes)

    def getNote(self, index):
        return self.notes[index]

    def addNote(self, note):
        self.notes.append(note)

    def clearNotes(self, all=False):
        """Like FL: without `all`, only the selection goes if there is one."""
        if all or not any(n.selected for n in self.notes):
            self.notes = []
        else:
            self.notes = [n for n in self.notes if not n.selected]


class FakeDialog:
    def __init__(self, title, description):
        self.inputs = {}

    def AddInputCheckbox(self, name, value, hint=""):
        self.inputs[name] = value

    def AddInputCombo(self, name, options, value, hint=""):
        self.inputs[name] = value

    def GetInputValue(self, name):
        return self.inputs[name]


@pytest.fixture
def fl(monkeypatch):
    mod = types.ModuleType("flpianoroll")
    mod.score = FakeScore()
    mod.Note = FakeNote
    mod.ScriptDialog = FakeDialog
    mod.messages = []
    mod.Utils = types.SimpleNamespace(ShowMessage=mod.messages.append)
    monkeypatch.setitem(sys.modules, "flpianoroll", mod)
    return mod


def load(path, exchange_dir):
    ns = {"__name__": "pyscript"}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)  # noqa: S102
    ns["EXCHANGE_DIR"] = str(exchange_dir)
    return ns


def run(ns, **inputs):
    if "createDialog" not in ns:  # FL runs a script without dialog once, right away
        ns["apply"]()
        return
    form = ns["createDialog"]()
    form.inputs.update(inputs)
    ns["apply"](form)


# -- constraints -------------------------------------------------------------------


@pytest.mark.parametrize("path", [SEND, RECEIVE])
def test_scripts_use_only_flpianoroll_os_and_json(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 7))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported <= {"flpianoroll", "os", "json"}


def test_default_exchange_dir_matches_the_relay(fl):
    from flmp_relay.cli import default_exchange_dir

    for path in (SEND, RECEIVE):
        ns = {"__name__": "pyscript"}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)  # noqa: S102
        assert Path(ns["EXCHANGE_DIR"]) == default_exchange_dir()


# -- Envoyer -------------------------------------------------------------------------


def test_send_has_no_dialog_so_it_runs_once():
    # With a dialog, FL runs the script in preview on open and on every change:
    # that would send several times, before the user even clicks Accept.
    ns = {"__name__": "pyscript"}
    exec(compile(SEND.read_text(encoding="utf-8"), str(SEND), "exec"), ns)  # noqa: S102
    assert "createDialog" not in ns


@pytest.mark.parametrize("path", [SEND, RECEIVE])
def test_scripts_never_create_folders(path):
    # FL's embedded Python fails on mkdir (SystemError, seen in FL 2026).
    source = path.read_text(encoding="utf-8")
    assert "makedirs" not in source and "mkdir" not in source


def test_send_writes_every_note(fl, tmp_path):
    fl.score.notes = [FakeNote(69, 192, 48), FakeNote(60, 0, 96, 1.0, 0.25, 0.75)]
    run(load(SEND, tmp_path))
    data = json.loads((tmp_path / "outbox.json").read_text(encoding="utf-8"))
    assert data == {
        "flmp": 1,
        "ppq": 96,
        "notes": [[69, 192, 48, round(0.78125, 4), 0.5, 0.5], [60, 0, 96, 1.0, 0.25, 0.75]],
    }
    assert "2 notes" in fl.messages[-1]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["outbox.json"]  # no temp file


def test_send_nothing_when_no_notes(fl, tmp_path):
    run(load(SEND, tmp_path))
    assert not (tmp_path / "outbox.json").exists()
    assert "Aucune note" in fl.messages[-1]


def write_inbox(folder, notes, ppq=96, sender="Alice"):
    folder.mkdir(parents=True, exist_ok=True)
    data = {"flmp": 1, "from": sender, "ppq": ppq, "notes": notes}
    (folder / "inbox.json").write_text(json.dumps(data), encoding="utf-8")


def test_receive_replaces_all_notes(fl, tmp_path):
    old = FakeNote(50)
    old.selected = True  # a selection must not limit "replace"
    fl.score.notes = [old, FakeNote(52)]
    write_inbox(tmp_path, [[69, 192, 48, 0.8, 0.4, 0.6]])
    run(load(RECEIVE, tmp_path))
    got = [(n.number, n.time, n.length, n.velocity, n.pan, n.release) for n in fl.score.notes]
    assert got == [(69, 192, 48, 0.8, 0.4, 0.6)]
    assert "Alice" in fl.messages[-1] and "1 note" in fl.messages[-1]


def test_receive_can_add(fl, tmp_path):
    fl.score.notes = [FakeNote(50)]
    write_inbox(tmp_path, [[69, 0, 48, 0.8, 0.5, 0.5]])
    run(load(RECEIVE, tmp_path), Mode=1)
    assert [n.number for n in fl.score.notes] == [50, 69]


def test_receive_converts_resolution(fl, tmp_path):
    fl.score.PPQ = 192
    write_inbox(tmp_path, [[60, 96, 48, 0.8, 0.5, 0.5]], ppq=96)
    run(load(RECEIVE, tmp_path))
    note = fl.score.notes[0]
    assert (note.time, note.length) == (192, 96)


def test_receive_without_inbox_changes_nothing(fl, tmp_path):
    fl.score.notes = [FakeNote(50)]
    run(load(RECEIVE, tmp_path))
    assert [n.number for n in fl.score.notes] == [50]
    assert "Aucun pattern" in fl.messages[-1]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"ppq": 96}),
        json.dumps({"ppq": "x", "notes": []}),
        json.dumps({"ppq": 96, "notes": [[999, 0, 1, 0.5, 0.5, 0.5]]}),
    ],
)
def test_receive_rejects_a_bad_inbox_without_touching_notes(fl, tmp_path, content):
    fl.score.notes = [FakeNote(50)]
    (tmp_path / "inbox.json").write_text(content, encoding="utf-8")
    run(load(RECEIVE, tmp_path))
    assert [n.number for n in fl.score.notes] == [50]
    assert "illisible" in fl.messages[-1]


def test_send_refuses_an_unsupported_resolution(fl, tmp_path):
    fl.score.PPQ = 4000
    fl.score.notes = [FakeNote()]
    run(load(SEND, tmp_path))
    assert not (tmp_path / "outbox.json").exists()
    assert "résolution" in fl.messages[-1]


def test_send_only_the_selection_when_there_is_one(fl, tmp_path):
    a, b = FakeNote(60), FakeNote(62)
    b.selected = True
    fl.score.notes = [a, b]
    run(load(SEND, tmp_path))
    data = json.loads((tmp_path / "outbox.json").read_text(encoding="utf-8"))
    assert [n[0] for n in data["notes"]] == [62]


def test_send_without_exchange_folder_asks_to_start_the_relay(fl, tmp_path):
    fl.score.notes = [FakeNote()]
    run(load(SEND, tmp_path / "missing"))
    assert not (tmp_path / "missing").exists()
    assert "relais" in fl.messages[-1]
