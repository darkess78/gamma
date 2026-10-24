import io
import math
import threading
import wave


class PCMUtteranceBuffer:
    def __init__(self, sample_rate=48000, channels=2, sample_width=2,
                 max_seconds=10, silence_seconds=0.6, max_users=4):
        if not self._positive_number(sample_rate) or int(sample_rate) != sample_rate:
            raise ValueError("invalid sample_rate")
        if not self._positive_number(channels) or int(channels) != channels:
            raise ValueError("invalid channels")
        if not self._positive_number(sample_width) or int(sample_width) != sample_width:
            raise ValueError("invalid sample_width")
        if not self._positive_number(max_seconds):
            raise ValueError("invalid max_seconds")
        if not self._positive_number(silence_seconds):
            raise ValueError("invalid silence_seconds")
        if not self._positive_number(max_users) or int(max_users) != max_users:
            raise ValueError("invalid max_users")

        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.sample_width = int(sample_width)
        self.max_seconds = float(max_seconds)
        self.silence_seconds = float(silence_seconds)
        self.max_users = int(max_users)
        self.frame_bytes = self.channels * self.sample_width
        self.max_frames = max(1, int(self.max_seconds * self.sample_rate))
        self.max_packet_bytes = 2 * self.sample_rate * self.frame_bytes
        self._lock = threading.RLock()
        self._speakers = {}

    @staticmethod
    def _positive_number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(value) and value > 0

    def _valid_user(self, user_id):
        return isinstance(user_id, str) and user_id.isdecimal()

    def _valid_pcm(self, pcm):
        return isinstance(pcm, (bytes, bytearray)) and len(pcm) > 0 and len(pcm) % self.frame_bytes == 0

    def _wav(self, pcm):
        out = io.BytesIO()
        with wave.open(out, "wb") as handle:
            handle.setnchannels(self.channels)
            handle.setsampwidth(self.sample_width)
            handle.setframerate(self.sample_rate)
            handle.writeframes(pcm)
        return out.getvalue()

    def push(self, user_id, pcm, *, now):
        with self._lock:
            if not self._valid_user(user_id) or not self._valid_pcm(pcm):
                return []
            if len(pcm) > self.max_packet_bytes:
                return []

            speaker = self._speakers.get(user_id)
            if speaker is None:
                if len(self._speakers) >= self.max_users:
                    return []
                speaker = {"pcm": b"", "last_now": now}
                self._speakers[user_id] = speaker

            pcm = bytes(pcm)
            max_chunk_bytes = self.max_frames * self.frame_bytes
            combined = speaker["pcm"] + pcm
            emitted = []

            while len(combined) > max_chunk_bytes:
                chunk = combined[:max_chunk_bytes]
                combined = combined[max_chunk_bytes:]
                emitted.append({"user_id": user_id, "wav": self._wav(chunk)})

            speaker["pcm"] = combined
            if now >= speaker["last_now"]:
                speaker["last_now"] = now
            return emitted

    def expire(self, *, now):
        with self._lock:
            expired = []
            stale = [user_id for user_id, speaker in self._speakers.items()
                     if now - speaker["last_now"] >= self.silence_seconds]
            for user_id in stale:
                speaker = self._speakers.pop(user_id)
                if speaker["pcm"]:
                    expired.append({"user_id": user_id, "wav": self._wav(speaker["pcm"])})
            return expired

    def flush(self, user_id):
        with self._lock:
            speaker = self._speakers.pop(user_id, None)
            if speaker is None or not speaker["pcm"]:
                return None
            return {"user_id": user_id, "wav": self._wav(speaker["pcm"])}

    def clear(self):
        with self._lock:
            self._speakers.clear()
