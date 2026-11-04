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
