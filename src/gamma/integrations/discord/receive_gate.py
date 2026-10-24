"""Authenticated Discord voice receive boundary; pending local worker."""
from __future__ import annotations

_OPUS_PT = 120
_OPUS_CS_BYTES = b"\xf8\xff\xfe"
_MIN_LEN = 1
_MAX_LEN = 65536


def _positive_int_non_bool(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class DaveReceiveGate:
    def __init__(self, voice_client, media_type: str):
        self._voice_client = voice_client
        self._media_type = media_type

    def decode(self, payload, *, ssrc, payload_type):
        try:
            if payload_type != _OPUS_PT:
                return None
            if not _positive_int_non_bool(ssrc):
                return None
            if not isinstance(payload, (bytes, bytearray)):
                return None
            data = bytes(payload)
            if len(data) < _MIN_LEN or len(data) > _MAX_LEN:
                return None
            if data == _OPUS_CS_BYTES:
                return None

            connection = getattr(self._voice_client, "_connection", None)
            if connection is None:
                return None
            version = getattr(connection, "dave_protocol_version", None)
            if not _positive_int_non_bool(version):
                return None
            session = getattr(connection, "dave_session", None)
            if session is None or getattr(session, "ready", None) is not True:
                return None

            user = self._voice_client._get_id_from_ssrc(ssrc)
            if not _positive_int_non_bool(user):
                return None

            result = session.decrypt(user, self._media_type, data)
            if not isinstance(result, (bytes, bytearray)):
                return None
            out = bytes(result)
            if len(out) < 1 or len(out) > _MAX_LEN:
                return None
            return out
        except Exception:
            return None
