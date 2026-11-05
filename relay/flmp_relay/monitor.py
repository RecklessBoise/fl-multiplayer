"""`flmp monitor`: a pretend participant for testing with a single FL Studio.

It joins a room, prints what the others send, and turns typed commands
(`play 384`, `stop`, `tempo 128.5`) into changes, as if a remote user did them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import sys
from typing import Any

from flmp_core import PROTOCOL_VERSION
from flmp_core.codec import pan_to_u14, u14_to_pan, u14_to_unit, unit_to_u14
from flmp_core.messages import ProtocolError, parse_fields
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from flmp_relay.bridge import check_server_url, clean

HELP = (
    "Commandes : play [tick] | stop | tempo <bpm> | mix vol|pan|mute|solo <piste> <valeur> | "
    "ch vol|pan|mute <channel> <valeur> | quit  (vol 0..1, pan -1..1, mute/solo on|off)"
)

_SECTIONS = {"mix": ("vol", "pan", "mute", "solo"), "ch": ("vol", "pan", "mute")}


def _wire_value(field: str, raw: str) -> int | None:
    if field in ("mute", "solo"):
        return {"on": 1, "off": 0}.get(raw)
    x = float(raw)
    low = -1.0 if field == "pan" else 0.0
    if not math.isfinite(x) or not low <= x <= 1.0:
        return None
    return pan_to_u14(x) if field == "pan" else unit_to_u14(x)


def _parse_indexed(parts: list[str]) -> tuple[str, int, int] | None:
    section, field = parts[0], parts[1]
    if field not in _SECTIONS[section]:
        return None
    value = _wire_value(field, parts[3])
    if value is None:
        return None
    return f"{section}_{field}", int(parts[2]), value


def parse_command(line: str) -> dict[str, Any] | None:
    parts = line.strip().lower().split()
    if not parts:
        return None
    try:
        if parts[0] == "play" and len(parts) <= 2:
            change = parse_fields("play", None, int(parts[1]) if len(parts) == 2 else 0)
        elif parts[0] == "stop" and len(parts) == 1:
            change = parse_fields("stop", None, 0)
        elif parts[0] == "tempo" and len(parts) == 2:
            change = parse_fields("tempo", None, round(float(parts[1]) * 1000))
        elif parts[0] in _SECTIONS and len(parts) == 4:
            fields = _parse_indexed(parts)
            if fields is None:
                return None
            change = parse_fields(*fields)
        else:
            return None
    except (ValueError, ProtocolError):
        return None
    return {"kind": change.kind, "index": change.index, "value": change.value}


def _target(kind: object, index: object) -> str | None:
    if not isinstance(kind, str) or not isinstance(index, int) or isinstance(index, bool):
        return None
    if kind in ("mix_vol", "mix_pan", "mix_mute", "mix_solo"):
        return f"mixer piste {index}"
    if kind in ("ch_vol", "ch_pan", "ch_mute"):
        return f"channel {index}"
    return None


def _shown_value(kind: str, value: int) -> str:
    field = kind.split("_", 1)[1]
    if field == "vol":
        return f"volume {u14_to_unit(value) * 100:.1f} %"
    if field == "pan":
        return f"pan {round(u14_to_pan(value) * 100)} %"
    return f"{field} {'activé' if value else 'désactivé'}"


def describe(msg: dict[str, Any], names: dict[str, str]) -> str | None:
    kind = msg.get("type")
    if kind == "change":
        sender = msg.get("from")
        who = clean(names.get(sender, "?") if isinstance(sender, str) else "?").strip("'")
        what, value, index = msg.get("kind"), msg.get("value"), msg.get("index")
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{who} → valeur invalide ignorée"
        if what == "play":
            return f"{who} → lecture depuis le tick {value}"
        if what == "stop":
            return f"{who} → arrêt"
        if what == "tempo":
            return f"{who} → tempo {value / 1000:.3f} BPM"
        target = _target(what, index)
        if target is not None:
            return f"{who} → {target} : {_shown_value(str(what), value)}"
        shown_index = index if isinstance(index, int) else "-"
        return f"{who} → {clean(what)} [{shown_index}] = {value}"
    if kind == "peer_joined":
        return f"{clean(msg.get('name'))} a rejoint le salon"
    if kind == "peer_left":
        peer = msg.get("peer")
        name = names.get(peer, "Un participant") if isinstance(peer, str) else "Un participant"
        return f"{clean(name)} a quitté le salon"
    if kind == "error":
        return f"Erreur serveur : {clean(msg.get('code'))}"
    return None


async def monitor(server: str, room: str, name: str, allow_insecure: bool = False) -> None:
    check_server_url(server, allow_insecure)
    async with connect(server) as ws:
        await ws.send(
            json.dumps({"type": "join", "room": room, "name": name, "proto": PROTOCOL_VERSION})
        )
        welcome = json.loads(await ws.recv())
        if not isinstance(welcome, dict) or welcome.get("type") != "welcome":
            code = welcome.get("code") if isinstance(welcome, dict) else None
            print(f"Refusé : {clean(code)}")
            return
        me = welcome.get("peer")
        names: dict[str, str] = {me: name} if isinstance(me, str) else {}
        for p in welcome.get("peers") or []:
            if isinstance(p, dict) and isinstance(p.get("peer"), str):
                names[p["peer"]] = str(p.get("name"))
        print(f"Moniteur connecté au salon {clean(room)}. {HELP}")

        async def handle(raw: str | bytes) -> None:
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                return
            kind = msg.get("type")
            if kind == "peer_joined" and isinstance(msg.get("peer"), str) and len(names) < 64:
                names[msg["peer"]] = str(msg.get("name"))
            if kind == "snapshot_request" and isinstance(msg.get("for"), str):
                # We hold no FL state: answer at once so newcomers don't wait.
                await ws.send(json.dumps({"type": "snapshot", "for": msg["for"], "changes": []}))
                return
            if kind == "change" and msg.get("from") == me:
                return
            text = describe(msg, names)
            if text:
                print(text)

        async def reader() -> None:
            try:
                async for raw in ws:
                    try:
                        await handle(raw)
                    except (ValueError, TypeError, KeyError):
                        print("Message illisible du serveur ignoré")
            except ConnectionClosed:
                pass
            print("Connexion fermée par le serveur. Tapez quit.")

        task = asyncio.create_task(reader())
        loop = asyncio.get_running_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line or line.strip().lower() in ("quit", "exit"):
                    break
                cmd = parse_command(line)
                if cmd is None:
                    print(HELP)
                    continue
                await ws.send(json.dumps({"type": "change", **cmd}))
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
