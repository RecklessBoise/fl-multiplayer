"""`flmp monitor`: a pretend participant for testing with a single FL Studio.

It joins a room, prints what the others send, and turns typed commands
(`play 384`, `stop`, `tempo 128.5`) into changes, as if a remote user did them.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from flmp_core import PROTOCOL_VERSION
from flmp_core.messages import ProtocolError, parse_fields
from websockets.asyncio.client import connect

from flmp_relay.bridge import check_server_url, clean

HELP = "Commandes : play [tick] | stop | tempo <bpm> | quit"


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
        else:
            return None
    except (ValueError, ProtocolError):
        return None
    return {"kind": change.kind, "index": change.index, "value": change.value}


def describe(msg: dict[str, Any], names: dict[str, str]) -> str | None:
    kind = msg.get("type")
    if kind == "change":
        who = clean(names.get(msg.get("from"), "?")).strip("'")
        what, value = msg.get("kind"), msg.get("value")
        if what == "play":
            return f"{who} → lecture depuis le tick {value}"
        if what == "stop":
            return f"{who} → arrêt"
        if what == "tempo" and isinstance(value, int):
            return f"{who} → tempo {value / 1000:.3f} BPM"
        return f"{who} → {clean(what)} [{msg.get('index')}] = {value}"
    if kind == "peer_joined":
        return f"{clean(msg.get('name'))} a rejoint le salon"
    if kind == "peer_left":
        return f"{clean(names.get(msg.get('peer'), 'Un participant'))} a quitté le salon"
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
        if welcome.get("type") != "welcome":
            print(f"Refusé : {clean(welcome.get('code'))}")
            return
        me = welcome["peer"]
        names = {me: name}
        names.update({p["peer"]: p["name"] for p in welcome.get("peers", [])})
        print(f"Moniteur connecté au salon {clean(room)}. {HELP}")

        async def reader() -> None:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "peer_joined":
                    names[msg.get("peer")] = msg.get("name")
                if msg.get("type") == "snapshot_request":
                    # We hold no FL state: answer at once so newcomers don't wait.
                    await ws.send(
                        json.dumps({"type": "snapshot", "for": msg["for"], "changes": []})
                    )
                    continue
                if msg.get("type") == "change" and msg.get("from") == me:
                    continue
                text = describe(msg, names)
                if text:
                    print(text)

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
