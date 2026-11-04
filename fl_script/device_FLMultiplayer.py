# name=FL Multiplayer
# url=https://github.com/RecklessBoise/fl-multiplayer
"""FL Multiplayer: MIDI controller script (v1: transport and tempo).

Install this file together with the `flmp_core` folder in
~/Documents/Image-Line/FL Studio/Settings/Hardware/FL Multiplayer/
FL adds the script's folder to its Python path, so `flmp_core` is importable.

Not affiliated with Image-Line. Only FL Studio API functions documented at
https://il-group.github.io/FL-Studio-API-Stubs/ are used:
    transport.isPlaying / start / stop / getSongPos / setSongPos   (API 1; mode arg API 3)
    mixer.getCurrentTempo                                          (API 1)
    general.getVersion / processRECEvent, midi.REC_Tempo           (API 1)
    device.isAssigned / midiOutSysex                               (API 1)
"""

import device
import general
import midi
import mixer
import transport

from flmp_core.controller import Controller

SONGLENGTH_ABSTICKS = 2  # getSongPos/setSongPos mode: absolute ticks
SONG_POS_MODE_API = 3  # the `mode` argument exists since API version 3
VERSION = "0.1.0-dev"


def _log(text):
    print(text)


class FLAdapter:
    """The only place that touches FL Studio's modules."""

    def __init__(self):
        self.api = general.getVersion()

    def is_playing(self):
        return bool(transport.isPlaying())

    def song_pos(self):
        if self.api < SONG_POS_MODE_API:
            return None
        return int(transport.getSongPos(SONGLENGTH_ABSTICKS))

    def play_from(self, pos):
        if self.api >= SONG_POS_MODE_API:
            transport.setSongPos(pos, SONGLENGTH_ABSTICKS)
        if not transport.isPlaying():
            transport.start()  # start() toggles play/pause, so only call it when stopped

    def stop(self):
        if transport.isPlaying():
            transport.stop()

    def read_state(self):
        tempo = mixer.getCurrentTempo()
        # Documented as "current tempo"; accept both BPM (e.g. 140.0) and BPM*1000.
        value = round(tempo) if tempo > 1000 else round(tempo * 1000)
        return {("tempo", None): value}

    def apply(self, change):
        if change.kind == "tempo":
            general.processRECEvent(
                midi.REC_Tempo, change.value, midi.REC_Control | midi.REC_UpdateControl
            )
            return True
        return False  # mixer / channel rack arrive in a later version


def _send(frame):
    if device.isAssigned():
        device.midiOutSysex(frame)


_controller = None
_last_failure = None


def _guard(where, fn):
    """Run fn(); never let an exception reach FL Studio (log each new one once)."""
    global _last_failure
    try:
        return fn()
    except Exception as exc:
        text = where + ": " + repr(exc)
        if text != _last_failure:
            _last_failure = text
            _log("FL Multiplayer : erreur dans " + text)
        return None


def _init():
    global _controller
    _controller = None
    adapter = FLAdapter()
    controller = Controller(adapter, _send, _log)
    controller.on_init()
    _controller = controller
    _log(f"FL Multiplayer {VERSION} chargé (API FL {adapter.api})")
    if not device.isAssigned():
        _log(
            "FL Multiplayer : aucune sortie MIDI reliée. Dans Options > MIDI settings, "
            "donnez à « FLMP Out » le même numéro de port que « FLMP In »."
        )


def OnInit():
    _guard("OnInit", _init)


def OnDeInit():
    global _controller
    _controller = None


def OnIdle():
    controller = _controller
    if controller is not None:
        _guard("OnIdle", controller.on_idle)


def OnSysEx(msg):
    controller = _controller
    if controller is not None and _guard("OnSysEx", lambda: controller.on_sysex(msg.sysex)):
        msg.handled = True
