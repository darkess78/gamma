"""Bounded owner capture, normalized before the private WAV ingress limit."""
from collections import deque
import math
import re
import struct
import threading

from ..integrations.discord.audio_buffer import PCMUtteranceBuffer


def _low_pass():
    # 31-tap Hamming-window sinc, with a 7 kHz cutoff before 3:1 decimation.
    # Its 15-input-frame delay is 0.313 ms. Coefficients are built once.
    center, cutoff = 15, 7000 / 48000
    taps = [(2 * cutoff if i == center else
             math.sin(2 * math.pi * cutoff * (i - center)) / (math.pi * (i - center)))
            * (.54 - .46 * math.cos(2 * math.pi * i / 30)) for i in range(31)]
    gain = sum(taps)
    return tuple(value / gain for value in taps)


_TAPS = _low_pass()


class OwnerPCMBuffer:
    """One pinned 48 kHz stereo PCM source, emitted as <=12 s 16 kHz mono WAV.

    Conversion state belongs to an utterance, not a global provider. Packet
    boundaries do not change sample counts or filtering. Capture timestamps are
    local transport diagnostics and grant no speaker or delivery authority.
    """
    def __init__(self, owner_id):
        if (not isinstance(owner_id, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', owner_id)
                or int(owner_id) >= 2 ** 64):
            raise ValueError('pinned owner ID required')
        self.owner_id = owner_id
        self._lock = threading.RLock()
        self._buffer = PCMUtteranceBuffer(sample_rate=16000, channels=1, sample_width=2,
                                          max_seconds=12, silence_seconds=.6, max_users=1)
        self._reset_conversion()

    def _reset_conversion(self):
        self._history = deque([0.0] * len(_TAPS), maxlen=len(_TAPS))
        self._phase = 0
        self._started_at = self._last_packet_at = None

    @staticmethod
    def _valid_time(now):
        return type(now) in {int, float} and math.isfinite(now)

    def _decorate(self, items, reason):
        for item in items:
            item.update(capture_started_monotonic=self._started_at,
                        capture_ended_monotonic=self._last_packet_at,
                        capture_reason=reason)
            # Only a duration cap can leave the next part of this utterance.
            if self._started_at is not None:
                duration = (len(item['wav']) - 44) / 32000
                self._started_at = min(self._last_packet_at, self._started_at + duration)
        return items

    def _expire(self, now):
        items = self._buffer.expire(now=now)
        self._decorate(items, 'silence_timeout')
        if items or (self._last_packet_at is not None and now - self._last_packet_at >= .6):
            self._reset_conversion()
        return items

    def push(self, user_id, pcm, *, now):
        if (user_id != self.owner_id or not isinstance(pcm, (bytes, bytearray))
                or not pcm or len(pcm) % 4 or len(pcm) > 384000 or not self._valid_time(now)):
            return []
        with self._lock:
            emitted = self._expire(now)
            if self._started_at is None:
                self._started_at = now - len(pcm) / 192000
            self._last_packet_at = max(now, self._last_packet_at) if self._last_packet_at is not None else now
            output = bytearray()
            for left, right in struct.iter_unpack('<hh', pcm):
                self._history.append((left + right) / 2)
                self._phase += 1
                if self._phase == 3:
                    value = round(sum(tap * sample for tap, sample in zip(_TAPS, self._history)))
                    output.extend(struct.pack('<h', max(-32768, min(32767, value))))
                    self._phase = 0
            if output:
                emitted.extend(self._decorate(self._buffer.push(user_id, output, now=now), 'max_duration'))
            return emitted

    def flush(self, user_id):
        if user_id != self.owner_id:
            return None
        with self._lock:
            item = self._buffer.flush(user_id)
            if item:
                self._decorate([item], 'speaking_stop')
            self._reset_conversion()
            return item

    def expire(self, *, now):
        if not self._valid_time(now):
            return []
        with self._lock:
            return self._expire(now)

    def clear(self):
        with self._lock:
            self._buffer.clear()
            self._reset_conversion()
