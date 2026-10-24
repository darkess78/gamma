"""CLI receiver using the same lease, start permit and revocation as browsers."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from websockets.sync.client import connect

from ..config import settings
from ..delivery.authority import DeliveryDenied


class ApprovedAudioPlayback:
    def __init__(self, *, binary=None, connector=connect, http_factory=httpx.Client):
        self.binary = binary
        self.connector = connector
        self.http_factory = http_factory

    def play_response(self, response):
        grant = response.tts_metadata.get("delivery_grant")
        if not grant:
            raise DeliveryDenied("CLI playback requires an approved delivery grant")
        base = settings.shana_internal_base_url.rstrip("/")
        parsed = urlsplit(base)
        query = {"target_policy": "dashboard_monitor", "client_name": "cli_playback"}
        if settings.api_auth_enabled and settings.api_bearer_token:
            query["token"] = settings.api_bearer_token
        url = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc,
                         "/v1/performer/events", urlencode(query), ""))
        headers = {"Authorization": f"Bearer {settings.api_bearer_token}"} if settings.api_auth_enabled else {}
        with self.connector(url, open_timeout=10, close_timeout=2) as socket:
            ready = json.loads(socket.recv(timeout=10))
            lease = ready["playback_lease"]["lease_id"]
            socket.send(json.dumps({"type": "subscriber_capabilities", "text_ready": True, "audio_ready": True}))
            socket.send(json.dumps({"type": "delivery_offer", "grant_id": grant}))
            event = self._wait(socket, lambda item: bool(item.get("playback")), lease=lease)
            offer = event["playback"]
            if offer.get("lease_id") != lease or offer.get("grant_id") != grant:
                raise DeliveryDenied("CLI playback offer does not belong to this receiver")
            receipt = {"schema_version": 1, "lease_id": lease, "event_id": offer["event_id"]}
            with self.http_factory(headers=headers, timeout=10) as client:
                result = client.get(base + f"/v1/delivery/{grant}/audio")
                result.raise_for_status()
                data = result.content
            if hashlib.sha256(data).hexdigest() != offer.get("audio_digest"):
                raise DeliveryDenied("CLI audio digest mismatch")
            with tempfile.TemporaryDirectory(prefix="gamma-approved-audio-") as directory:
                path = Path(directory) / "approved.wav"
                path.write_bytes(data)
                socket.send(json.dumps({"type": "playback_prepare", **receipt}))
                permit = self._wait(socket, lambda item: item.get("type") == "playback_permit", lease=lease)
                if (permit.get("event_id") != offer["event_id"] or permit.get("lease_id") != lease
                        or permit.get("expires_at", 0) <= time.time()):
                    raise DeliveryDenied("CLI playback permit is stale")
                return self._play(socket, path, receipt)

    @staticmethod
    def _wait(socket, predicate, *, lease):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            item = json.loads(socket.recv(timeout=max(.01, deadline-time.monotonic())))
            if item.get("type") in {"playback_rejected", "playback_revoked", "delivery_rejected"}:
                raise DeliveryDenied("CLI delivery denied or revoked")
            if predicate(item):
                return item
        raise DeliveryDenied("CLI delivery timed out")

    def _play(self, socket, path, receipt):
        # The player is always a separately cancellable process. No detached audio.
        binary = self.binary or ("afplay" if __import__("platform").system() == "Darwin" else "aplay")
        player = shutil.which(binary)
        if not player:
            self._ack(socket, receipt, "failed")
            return False
        process = None
        status = "failed"
        try:
            process = subprocess.Popen([player, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._ack(socket, receipt, "started")
            while process.poll() is None:
                try:
                    message = json.loads(socket.recv(timeout=.1))
                except TimeoutError:
                    continue
                if message.get("type") in {"playback_revoked", "playback_rejected", "output_cleared"}:
                    if message.get("event_id", receipt["event_id"]) == receipt["event_id"]:
                        status = "interrupted"
                        break
            if status != "interrupted":
                status = "completed" if process.returncode == 0 else "failed"
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            self._ack(socket, receipt, status)
        if status == "completed":
            confirmed = self._wait(socket, lambda item: item.get("type") == "playback_receipt"
                and item.get("event_id") == receipt["event_id"] and item.get("status") == "completed",
                lease=receipt["lease_id"])
            if confirmed.get("lease_id", receipt["lease_id"]) != receipt["lease_id"]:
                raise DeliveryDenied("CLI completion receipt belongs to another receiver")
            return True
        return False

    @staticmethod
    def _ack(socket, receipt, status):
        socket.send(json.dumps({"type": "playback_ack", **receipt, "status": status}))
