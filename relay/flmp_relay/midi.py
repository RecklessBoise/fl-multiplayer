"""MIDI side of the relay: SysEx frames in and out of the IAC buses.

`MidoMidiIO` talks to real ports through mido + python-rtmidi. rtmidi calls
our input callback from its own thread, so frames are handed to asyncio with
`call_soon_threadsafe`. `QueueMidiIO` is an in-memory stand-in for tests and
the end-to-end simulation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import mido

log = logging.getLogger("flmp.relay")

QUEUE_LIMIT = 10_000


class MidiPortError(RuntimeError):
    pass


class MidiIO(Protocol):
    async def receive(self) -> bytes: ...

    def send(self, frame: bytes) -> None: ...

    def close(self) -> None: ...


def find_port(names: list[str], wanted: str) -> str | None:
    """Exact match first, then a case-insensitive suffix match.

    macOS names IAC buses "IAC Driver <bus name>", so "FLMP In" matches
    "IAC Driver FLMP In" but never "IAC Driver FLMP Out".
    """
    if wanted in names:
        return wanted
    w = wanted.lower()
    for name in names:
        if name.lower().endswith(w):
            return name
    return None


def message_from_frame(frame: bytes) -> mido.Message:
    if len(frame) < 2 or frame[0] != 0xF0 or frame[-1] != 0xF7:
        raise ValueError("not a SysEx frame")
    return mido.Message("sysex", data=list(frame[1:-1]))


def frame_from_message(msg: mido.Message) -> bytes | None:
    if msg.type != "sysex":
        return None
    return bytes([0xF0, *msg.data, 0xF7])


class QueueMidiIO:
    """In-memory MIDI: `fl_*` methods play the FL Studio side."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self.outgoing: asyncio.Queue[bytes] = asyncio.Queue()

    async def receive(self) -> bytes:
        return await self.incoming.get()

    def send(self, frame: bytes) -> None:
        self.outgoing.put_nowait(bytes(frame))

    def close(self) -> None:
        pass

    def fl_sends(self, frame: bytes) -> None:
        self.incoming.put_nowait(bytes(frame))

    async def fl_receives(self, timeout: float = 2.0) -> bytes:
        return await asyncio.wait_for(self.outgoing.get(), timeout)

    async def fl_receives_nothing(self, delay: float = 0.15) -> None:
        await asyncio.sleep(delay)
        if not self.outgoing.empty():
            raise AssertionError(f"unexpected frame sent to FL: {self.outgoing.get_nowait()!r}")

    def drain(self) -> list[bytes]:
        out = []
        while not self.outgoing.empty():
            out.append(self.outgoing.get_nowait())
        return out


class MidoMidiIO:
    def __init__(self, inport: Any, outport: Any, queue: asyncio.Queue[bytes]) -> None:
        self._in = inport
        self._out = outport
        self._queue = queue

    @classmethod
    def open(cls, in_name: str, out_name: str, loop: asyncio.AbstractEventLoop) -> MidoMidiIO:
        """Open `in_name` (FL -> relay) and `out_name` (relay -> FL)."""
        inputs, outputs = mido.get_input_names(), mido.get_output_names()
        real_in = find_port(inputs, in_name)
        if real_in is None:
            raise MidiPortError(
                f"Entrée MIDI « {in_name} » introuvable. Ports disponibles : {inputs or 'aucun'}"
            )
        real_out = find_port(outputs, out_name)
        if real_out is None:
            raise MidiPortError(
                f"Sortie MIDI « {out_name} » introuvable. Ports disponibles : {outputs or 'aucun'}"
            )
        queue: asyncio.Queue[bytes] = asyncio.Queue(QUEUE_LIMIT)

        def enqueue(frame: bytes) -> None:
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                log.warning("File MIDI pleine : message de FL ignoré")

        def on_message(msg: mido.Message) -> None:  # runs on rtmidi's thread
            frame = frame_from_message(msg)
            if frame is not None:
                loop.call_soon_threadsafe(enqueue, frame)

        inport = mido.open_input(real_in, callback=on_message)
        try:
            outport = mido.open_output(real_out)
        except Exception:
            inport.close()
            raise
        log.info("MIDI : entrée « %s », sortie « %s »", real_in, real_out)
        return cls(inport, outport, queue)

    async def receive(self) -> bytes:
        return await self._queue.get()

    def send(self, frame: bytes) -> None:
        self._out.send(message_from_frame(frame))

    def close(self) -> None:
        self._in.close()
        self._out.close()
