"""Command line: `flmp join …` and `flmp doctor`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import logging
import os
import shutil
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path

import flmp_core
import mido
from websockets.asyncio.client import connect

from flmp_relay.bridge import FatalJoinError, Relay, check_server_url
from flmp_relay.midi import MidiPortError, MidoMidiIO, find_port

SECRET_ENV = "FLMP_ROOM_SECRET"  # noqa: S105 - name of the env var, not a secret
DEFAULT_IN = "FLMP Out"  # FL Studio -> relay
DEFAULT_OUT = "FLMP In"  # relay -> FL Studio
IAC_HINT = (
    "→ Ouvrez « Configuration audio et MIDI » (Applications > Utilitaires), menu "
    "Fenêtre > Afficher le studio MIDI, double-cliquez sur « Gestionnaire IAC », cochez "
    "« Le gestionnaire est en ligne » et créez les bus « FLMP In » et « FLMP Out »."
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="flmp", description="FL Multiplayer : relais local")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs détaillés")
    sub = parser.add_subparsers(dest="command", required=True)

    def ports(p: argparse.ArgumentParser) -> None:
        p.add_argument("--in-port", default=DEFAULT_IN, help=f"bus FL → relais ({DEFAULT_IN})")
        p.add_argument("--out-port", default=DEFAULT_OUT, help=f"bus relais → FL ({DEFAULT_OUT})")

    join = sub.add_parser("join", help="rejoindre un salon")
    join.add_argument("--server", required=True, help="adresse du serveur (wss://…)")
    join.add_argument("--room", required=True, help="code du salon (4 à 12 lettres ou chiffres)")
    join.add_argument("--name", required=True, help="votre pseudo")
    join.add_argument(
        "--ask-secret",
        action="store_true",
        help=f"demander le mot de passe du salon (sinon variable {SECRET_ENV})",
    )
    join.add_argument("--insecure", action="store_true", help="autoriser ws:// hors localhost")
    join.add_argument(
        "--virtual",
        action="store_true",
        help="créer soi-même les ports « FLMP In » / « FLMP Out » (pas besoin de bus IAC)",
    )
    ports(join)

    mon = sub.add_parser("monitor", help="faux participant pour tester avec un seul FL")
    mon.add_argument("--server", required=True, help="adresse du serveur")
    mon.add_argument("--room", required=True, help="code du salon")
    mon.add_argument("--name", default="Moniteur", help="pseudo affiché (Moniteur)")
    mon.add_argument("--insecure", action="store_true", help="autoriser ws:// hors localhost")

    install = sub.add_parser("install-script", help="installer le script dans FL Studio")
    install.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="dossier cible (par défaut : dossier Hardware de FL)",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="accepter un dossier cible d'un autre nom",
    )

    doctor = sub.add_parser("doctor", help="vérifier l'installation")
    doctor.add_argument("--server", help="adresse du serveur à tester (optionnel)")
    doctor.add_argument("--insecure", action="store_true", help="autoriser ws:// hors localhost")
    ports(doctor)
    return parser.parse_args(argv)


SCRIPT_NAME = "device_FLMultiplayer.py"


def default_script_dir() -> Path:
    return (
        Path.home()
        / "Documents"
        / "Image-Line"
        / "FL Studio"
        / "Settings"
        / "Hardware"
        / "FL Multiplayer"
    )


def _script_source() -> Path:
    """The FL script shipped in the wheel, or the one in a source checkout."""
    packaged = resources.files("flmp_relay") / "fl_script" / SCRIPT_NAME
    if packaged.is_file():
        return Path(str(packaged))
    return Path(flmp_core.__file__).resolve().parent.parent / SCRIPT_NAME


EXPECTED_DIR_NAME = "FL Multiplayer"


def install_script(dest: Path, force: bool = False) -> Path:
    """Copy the FL script and its flmp_core package into `dest`.

    Only a folder named "FL Multiplayer" is accepted unless `force`, because an
    existing `flmp_core` folder in `dest` is replaced. Symlinks are refused, and
    the new package is copied next to the old one before being swapped in.
    """
    if dest.name != EXPECTED_DIR_NAME and not force:
        raise OSError(
            f"le dossier cible doit s'appeler « {EXPECTED_DIR_NAME} » (ou utilisez --force)"
        )
    source = _script_source()
    core = Path(flmp_core.__file__).resolve().parent
    script_target = dest / SCRIPT_NAME
    target_core = dest / "flmp_core"
    for path in (dest, script_target, target_core):
        if path.is_symlink():
            raise OSError(f"{path} est un lien symbolique : refus de l'écraser")
    dest.mkdir(parents=True, exist_ok=True)
    staging = dest / "flmp_core.new"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(core, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if target_core.exists():
        shutil.rmtree(target_core)
    staging.rename(target_core)
    shutil.copyfile(source, script_target)
    return dest


def read_secret(args: argparse.Namespace) -> str | None:
    """Never from the command line (it would show up in `ps`)."""
    if getattr(args, "ask_secret", False):
        return getpass.getpass("Mot de passe du salon : ") or None
    return os.environ.get(SECRET_ENV) or None


async def _check_server(url: str) -> str:
    async with connect(url, open_timeout=5) as ws:
        await ws.send(json.dumps({"type": "hello"}))
        reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
    if reply.get("server") != "flmp":
        raise ConnectionError("ce n'est pas un serveur FL Multiplayer")
    return f"protocole {reply.get('proto')}"


async def doctor(
    server: str | None,
    in_port: str,
    out_port: str,
    echo: Callable[[str], None] = print,
    allow_insecure: bool = False,
) -> bool:
    ok = True

    def report(good: bool, text: str, hint: str | None = None) -> None:
        nonlocal ok
        ok = ok and good
        echo(f"  {'OK   ' if good else 'ÉCHEC'}  {text}")
        if hint and not good:
            echo(f"         {hint}")

    echo("FL Multiplayer — diagnostic")
    try:
        inputs, outputs = mido.get_input_names(), mido.get_output_names()
        report(True, "Pilote MIDI disponible")
    except Exception as exc:
        report(False, f"Pilote MIDI indisponible ({exc})", "→ Réinstallez le relais.")
        inputs, outputs = [], []
    found_in = find_port(inputs, in_port)
    report(
        found_in is not None,
        f"Bus « {in_port} » (FL → relais) {'trouvé : ' + found_in if found_in else 'introuvable'}",
        IAC_HINT,
    )
    found_out = find_port(outputs, out_port)
    report(
        found_out is not None,
        f"Bus « {out_port} » (relais → FL) "
        f"{'trouvé : ' + found_out if found_out else 'introuvable'}",
        IAC_HINT,
    )
    if server:
        try:
            check_server_url(server, allow_insecure)
            detail = await _check_server(server)
            report(True, f"Serveur {server} joignable ({detail})")
        except Exception as exc:
            report(
                False,
                f"Serveur {server} injoignable : {exc}",
                "→ Vérifiez l'adresse, votre connexion Internet, ou que le serveur est démarré.",
            )
    echo("Tout est prêt." if ok else "Corrigez les points en ÉCHEC puis relancez « flmp doctor ».")
    return ok


async def _join(args: argparse.Namespace, secret: str | None) -> None:
    loop = asyncio.get_running_loop()
    midi = MidoMidiIO.open(args.in_port, args.out_port, loop, virtual=args.virtual)
    try:
        relay = Relay(midi, args.server, args.room, args.name, secret, allow_insecure=args.insecure)
        await relay.run()
    finally:
        midi.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.command == "monitor":
        from flmp_relay.monitor import monitor

        try:
            with contextlib.suppress(KeyboardInterrupt):
                asyncio.run(monitor(args.server, args.room, args.name, args.insecure))
        except (ValueError, OSError) as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0
    if args.command == "install-script":
        dest = args.dest or default_script_dir()
        try:
            install_script(dest, force=args.force)
        except OSError as exc:
            print(f"Installation impossible : {exc}", file=sys.stderr)
            return 1
        print(f"Script installé dans : {dest}")
        print(
            "Redémarrez FL Studio, puis choisissez « FL Multiplayer » dans Options > MIDI settings."
        )
        return 0
    if args.command == "doctor":
        good = asyncio.run(doctor(args.server, args.in_port, args.out_port, print, args.insecure))
        return 0 if good else 1
    try:
        check_server_url(args.server, args.insecure)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    secret = read_secret(args)
    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_join(args, secret))
    except MidiPortError as exc:
        print(f"{exc}\nLancez « flmp doctor » pour vérifier l'installation.", file=sys.stderr)
        return 1
    except FatalJoinError as exc:
        print(f"Le serveur a refusé la connexion : {exc}", file=sys.stderr)
        return 2
    return 0


def entry() -> None:
    sys.exit(main())
