import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gamma.delivery.authority import DeliveryDenied
from gamma.voice.approved_playback import ApprovedAudioPlayback


class Socket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
    def send(self, message):
        item = json.loads(message)
        self.sent.append(item)
        if item.get("type") == "playback_ack" and item.get("status") == "completed":
            self.messages.append({**item, "type": "playback_receipt"})
    def recv(self, timeout=None):
        if self.messages:
            return json.dumps(self.messages.pop(0))
        time.sleep(min(timeout or .01, .01))
        raise TimeoutError()


def player(tmp_path, body):
    path = tmp_path / "fixture-player"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o700)
    return str(path)


def test_ungranted_cli_response_never_opens_connection():
    def forbidden(*args, **kwargs):
        pytest.fail("network opened before authorization")
    with pytest.raises(DeliveryDenied):
        ApprovedAudioPlayback(connector=forbidden).play_response(SimpleNamespace(tts_metadata={}))


def test_revocation_terminates_actual_player_process(tmp_path):
    pid = tmp_path / "child.pid"
    binary = player(tmp_path, f"echo $$ > '{pid}'\nexec sleep 30\n")
    class RevokingSocket(Socket):
        def recv(self, timeout=None):
            deadline = time.monotonic() + 3
            while not pid.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            return json.dumps({"type": "playback_revoked", "event_id": "event"})
    socket = RevokingSocket([])
    result = ApprovedAudioPlayback(binary=binary)._play(socket, tmp_path / "audio.wav",
        {"schema_version": 1, "lease_id": "receiver-a", "event_id": "event"})
    assert result is False
    assert [item["status"] for item in socket.sent] == ["started", "interrupted"]
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_success_and_failure_are_player_exit_receipts(tmp_path):
    for code, status in ((0, "completed"), (7, "failed")):
        socket = Socket([])
        result = ApprovedAudioPlayback(binary=player(tmp_path, f"exit {code}\n"))._play(
            socket, tmp_path / "audio.wav", {"schema_version": 1, "lease_id": "a", "event_id": "event"})
        assert result is (code == 0)
        assert socket.sent[-1]["status"] == status


def test_disconnected_receiver_terminates_player(tmp_path):
    pid = tmp_path / "child.pid"
    binary = player(tmp_path, f"echo $$ > '{pid}'\nexec sleep 30\n")
    class DisconnectedSocket(Socket):
        def recv(self, timeout=None):
            while not pid.exists():
                time.sleep(.01)
            raise RuntimeError("connection lost")
    socket = DisconnectedSocket([])
    with pytest.raises(RuntimeError, match="connection lost"):
        ApprovedAudioPlayback(binary=binary)._play(socket, tmp_path / "audio.wav",
            {"schema_version": 1, "lease_id": "a", "event_id": "event"})
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_legacy_audio_path_cannot_play(tmp_path):
    from gamma.voice.controller import AudioPlayback
    with pytest.raises(DeliveryDenied, match="Raw audio"):
        AudioPlayback().play(str(tmp_path / "not-authorized.wav"))


def test_cross_receiver_offer_rejected_before_download():
    class ContextSocket(Socket):
        def __enter__(self): return self
        def __exit__(self, *args): pass
    socket = ContextSocket([
        {"playback_lease": {"lease_id": "receiver-a"}},
        {"playback": {"lease_id": "receiver-b", "grant_id": "approved", "event_id": "event"}},
    ])
    def forbidden(**kwargs):
        pytest.fail("download started with another receiver offer")
    with pytest.raises(DeliveryDenied, match="receiver"):
        ApprovedAudioPlayback(connector=lambda *a, **k: socket, http_factory=forbidden).play_response(
            SimpleNamespace(tts_metadata={"delivery_grant": "approved"}))


def test_invalid_digest_never_requests_start_permit():
    class ContextSocket(Socket):
        def __enter__(self): return self
        def __exit__(self, *args): pass
    socket = ContextSocket([
        {"playback_lease": {"lease_id": "a"}},
        {"playback": {"lease_id": "a", "grant_id": "approved", "event_id": "event", "audio_digest": "wrong"}},
    ])
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url):
            return SimpleNamespace(content=b"substituted", raise_for_status=lambda: None)
    with pytest.raises(DeliveryDenied, match="digest"):
        ApprovedAudioPlayback(connector=lambda *a, **k: socket, http_factory=Client).play_response(
            SimpleNamespace(tts_metadata={"delivery_grant": "approved"}))
    assert not any(item["type"] == "playback_prepare" for item in socket.sent)
