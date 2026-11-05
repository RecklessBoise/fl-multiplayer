# name=FL Multiplayer
# url=https://github.com/RecklessBoise/fl-multiplayer
"""FL Multiplayer: MIDI controller script (transport, tempo, mixer, channel rack).

Install this file together with the `flmp_core` folder in
~/Documents/Image-Line/FL Studio/Settings/Hardware/FL Multiplayer/
FL adds the script's folder to its Python path, so `flmp_core` is importable.

Not affiliated with Image-Line. Only FL Studio API functions documented at
https://il-group.github.io/FL-Studio-API-Stubs/ are used:
    transport.isPlaying / start / stop / getSongPos / setSongPos   (API 1; mode arg API 3)
    mixer.getCurrentTempo                                          (API 1)
    general.getVersion / processRECEvent, midi.REC_Tempo           (API 1)
    device.isAssigned / midiOutSysex                               (API 1)
    mixer.getTrackInfo (TN_Master, TN_LastIns), getTrackVolume / setTrackVolume,
        getTrackPan / setTrackPan, isTrackSolo / soloTrack          (API 1)
    mixer.isTrackMuted / muteTrack                                 (API 2)
    channels.channelCount(globalCount)                             (API 3)
    channels.getChannelVolume / setChannelVolume, getChannelPan / setChannelPan,
        isChannelMuted / muteChannel                               (API 1; useGlobalIndex API 33)
"""

import channels
import device
import general
import midi
import mixer
import transport

from flmp_core.codec import pan_to_u14, u14_to_pan, u14_to_unit, unit_to_u14
from flmp_core.controller import Controller

SONGLENGTH_ABSTICKS = 2  # getSongPos/setSongPos mode: absolute ticks
SONG_POS_MODE_API = 3  # the `mode` argument exists since API version 3
MUTE_API = 2  # mixer.isTrackMuted / muteTrack
GLOBAL_COUNT_API = 3  # channels.channelCount(globalCount)
GLOBAL_INDEX_API = 33  # channels.*(useGlobalIndex=True)
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
        # FL 2026 (API 45) returns BPM*1000 (e.g. 95000); getCurrentTempo(True) gives BPM.
        # Older versions are undocumented, so accept both BPM (e.g. 140.0) and BPM*1000.
        state = {("tempo", None): round(tempo) if tempo > 1000 else round(tempo * 1000)}
        self._read_mixer(state)
        self._read_channels(state)
        return state

    def _tracks(self):
        # Master and inserts only: the "current" track mirrors the selected one.
        return range(mixer.getTrackInfo(midi.TN_Master), mixer.getTrackInfo(midi.TN_LastIns) + 1)

    def _read_mixer(self, state):
        for i in self._tracks():
            state[("mix_vol", i)] = unit_to_u14(mixer.getTrackVolume(i))
            state[("mix_pan", i)] = pan_to_u14(mixer.getTrackPan(i))
            state[("mix_solo", i)] = int(bool(mixer.isTrackSolo(i)))
            if self.api >= MUTE_API:
                state[("mix_mute", i)] = int(bool(mixer.isTrackMuted(i)))

    def _global(self):
        # Global indexes don't depend on the channel group shown in the rack.
        return self.api >= GLOBAL_INDEX_API

    def _channel_count(self):
        if self.api >= GLOBAL_COUNT_API and self._global():
            return channels.channelCount(True)
        return channels.channelCount()

    def _read_channels(self, state):
        g = self._global()
        for i in range(self._channel_count()):
            if g:
                vol = channels.getChannelVolume(i, False, True)
                pan = channels.getChannelPan(i, True)
                mute = channels.isChannelMuted(i, True)
            else:
                vol = channels.getChannelVolume(i)
                pan = channels.getChannelPan(i)
                mute = channels.isChannelMuted(i)
            state[("ch_vol", i)] = unit_to_u14(vol)
            state[("ch_pan", i)] = pan_to_u14(pan)
            state[("ch_mute", i)] = int(bool(mute))

    def apply(self, change):
        kind, i, v = change.kind, change.index, change.value
        if kind == "tempo":
            general.processRECEvent(midi.REC_Tempo, v, midi.REC_Control | midi.REC_UpdateControl)
        elif kind == "mix_vol":
            mixer.setTrackVolume(i, u14_to_unit(v), midi.PIM_None)
        elif kind == "mix_pan":
            mixer.setTrackPan(i, u14_to_pan(v), midi.PIM_None)
        elif kind == "mix_mute":
            mixer.muteTrack(i, v)
        elif kind == "mix_solo":
            mixer.soloTrack(i, v)
        elif kind in ("ch_vol", "ch_pan", "ch_mute"):
            self._apply_channel(kind, i, v)
        else:
            return False
        return True

    def _apply_channel(self, kind, i, v):
        g = self._global()
        if kind == "ch_vol":
            if g:
                channels.setChannelVolume(i, u14_to_unit(v), midi.PIM_None, True)
            else:
                channels.setChannelVolume(i, u14_to_unit(v), midi.PIM_None)
        elif kind == "ch_pan":
            if g:
                channels.setChannelPan(i, u14_to_pan(v), midi.PIM_None, True)
            else:
                channels.setChannelPan(i, u14_to_pan(v), midi.PIM_None)
        elif g:
            channels.muteChannel(i, v, True)
        else:
            channels.muteChannel(i, v)


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
