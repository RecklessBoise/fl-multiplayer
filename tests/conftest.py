"""Shared fixtures: a real room server on localhost and simple JSON clients."""

import asyncio
import json

import pytest
from flmp_server.app import Settings, start_server
from websockets.asyncio.client import connect

TIMEOUT = 2.0


class Client:
    def __init__(self, ws, url=None):
        self.ws = ws
        self.url = url

    async def send(self, **msg):
        await self.ws.send(json.dumps(msg))

    async def recv(self) -> dict:
        return json.loads(await asyncio.wait_for(self.ws.recv(), TIMEOUT))

    async def recv_type(self, kind: str) -> dict:
        while True:
            msg = await self.recv()
            if msg["type"] == kind:
                return msg

    async def nothing(self, delay: float = 0.15) -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(self.ws.recv(), delay)

    async def join(self, room="ROOM1", name="Alice", **extra) -> dict:
        await self.send(type="join", room=room, name=name, proto=1, **extra)
        return await self.recv()


@pytest.fixture
async def server():
    servers = []

    async def make(**overrides):
        srv, url = await start_server(Settings(**overrides), host="127.0.0.1", port=0)
        servers.append(srv)
        return url

    yield make
    for srv in servers:
        srv.close()
        await srv.wait_closed()


@pytest.fixture
async def clients():
    opened = []

    async def make(url):
        ws = await connect(url)
        opened.append(ws)
        return Client(ws, url)

    yield make
    for ws in opened:
        await ws.close()
