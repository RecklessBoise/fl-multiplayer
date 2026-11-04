import pytest
from flmp_relay import cli
from flmp_relay import midi as midi_mod
from flmp_relay.midi import MidiPortError


@pytest.fixture
def ports(monkeypatch):
    def set_ports(inputs, outputs):
        monkeypatch.setattr(midi_mod.mido, "get_input_names", lambda: list(inputs))
        monkeypatch.setattr(midi_mod.mido, "get_output_names", lambda: list(outputs))

    set_ports(["IAC Driver FLMP Out"], ["IAC Driver FLMP In"])
    return set_ports


class TestArgs:
    def test_join_args(self, monkeypatch):
        monkeypatch.delenv(cli.SECRET_ENV, raising=False)
        args = cli.parse_args(["join", "--server", "wss://x.org", "--room", "abcd", "--name", "Al"])
        assert (args.server, args.room, args.name) == ("wss://x.org", "abcd", "Al")
        assert args.in_port == "FLMP Out" and args.out_port == "FLMP In"
        assert cli.read_secret(args) is None

    def test_secret_from_env(self, monkeypatch):
        monkeypatch.setenv(cli.SECRET_ENV, "from-env")
        args = cli.parse_args(["join", "--server", "wss://x", "--room", "ABCD", "--name", "A"])
        assert cli.read_secret(args) == "from-env"

    def test_secret_prompt(self, monkeypatch):
        monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "typed")
        args = cli.parse_args(
            ["join", "--server", "wss://x", "--room", "ABCD", "--name", "A", "--ask-secret"]
        )
        assert cli.read_secret(args) == "typed"

    def test_secret_is_never_a_command_line_value(self):
        argv = ["join", "--server", "wss://x", "--room", "R", "--name", "A", "--secret", "x"]
        with pytest.raises(SystemExit):
            cli.parse_args(argv)


class TestDoctor:
    async def test_all_good(self, ports, server):
        url = await server()
        lines = []
        assert await cli.doctor(url, "FLMP Out", "FLMP In", echo=lines.append) is True
        text = "\n".join(lines)
        assert text.count("OK") >= 3
        assert "ÉCHEC" not in text

    async def test_missing_buses_explain_fix(self, ports):
        ports([], [])
        lines = []
        assert await cli.doctor(None, "FLMP Out", "FLMP In", echo=lines.append) is False
        text = "\n".join(lines)
        assert "FLMP Out" in text and "FLMP In" in text
        assert "Configuration audio et MIDI" in text

    async def test_unreachable_server(self, ports, unused_tcp_port):
        lines = []
        url = f"ws://127.0.0.1:{unused_tcp_port}"
        assert await cli.doctor(url, "FLMP Out", "FLMP In", lines.append) is False
        assert any("serveur" in line.lower() and "ÉCHEC" in line for line in lines)

    async def test_insecure_url_is_reported(self, ports):
        lines = []
        assert await cli.doctor("ws://example.org", "FLMP Out", "FLMP In", lines.append) is False


class TestMain:
    def test_join_with_missing_ports_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise MidiPortError("Entrée MIDI « FLMP Out » introuvable")

        monkeypatch.setattr(cli.MidoMidiIO, "open", boom)
        code = cli.main(["join", "--server", "ws://localhost:1", "--room", "ABCD", "--name", "A"])
        assert code == 1
        assert "flmp doctor" in capsys.readouterr().err

    def test_join_with_bad_url_exits_2(self, capsys):
        code = cli.main(["join", "--server", "ws://example.org", "--room", "ABCD", "--name", "A"])
        assert code == 2
        assert "wss://" in capsys.readouterr().err

    def test_doctor_exit_code(self, ports, capsys):
        assert cli.main(["doctor"]) == 0
        assert "OK" in capsys.readouterr().out


class TestInstallScript:
    def test_copies_script_and_core_package(self, tmp_path):
        dest = tmp_path / "FL Multiplayer"
        assert cli.main(["install-script", "--dest", str(dest)]) == 0
        script = dest / "device_FLMultiplayer.py"
        assert script.read_text(encoding="utf-8").startswith("# name=FL Multiplayer")
        assert (dest / "flmp_core" / "controller.py").is_file()
        assert not list(dest.rglob("__pycache__"))

    def test_reinstall_replaces_old_core_but_keeps_other_files(self, tmp_path):
        dest = tmp_path / "FL Multiplayer"
        (dest / "flmp_core").mkdir(parents=True)
        (dest / "flmp_core" / "stale.py").write_text("old")
        (dest / "notes.txt").write_text("mine")
        assert cli.main(["install-script", "--dest", str(dest)]) == 0
        assert not (dest / "flmp_core" / "stale.py").exists()
        assert (dest / "notes.txt").read_text() == "mine"

    def test_default_destination_is_fl_hardware_folder(self):
        assert cli.default_script_dir().parts[-5:] == (
            "Image-Line",
            "FL Studio",
            "Settings",
            "Hardware",
            "FL Multiplayer",
        )


class TestInstallScriptSafety:
    def test_refuses_unexpected_destination_without_force(self, tmp_path):
        dest = tmp_path / "Documents"
        (dest / "flmp_core").mkdir(parents=True)
        (dest / "flmp_core" / "precious.txt").write_text("keep me")
        assert cli.main(["install-script", "--dest", str(dest)]) == 1
        assert (dest / "flmp_core" / "precious.txt").read_text() == "keep me"

    def test_force_allows_other_destination(self, tmp_path):
        dest = tmp_path / "Elsewhere"
        assert cli.main(["install-script", "--dest", str(dest), "--force"]) == 0
        assert (dest / "device_FLMultiplayer.py").is_file()

    def test_refuses_symlinked_script(self, tmp_path):
        dest = tmp_path / "FL Multiplayer"
        dest.mkdir()
        target = tmp_path / "victim.txt"
        target.write_text("original")
        (dest / "device_FLMultiplayer.py").symlink_to(target)
        assert cli.main(["install-script", "--dest", str(dest)]) == 1
        assert target.read_text() == "original"
