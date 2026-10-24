"""Small receive bridge for the pinned Discord voice extension.

Discord.py owns the DAVE handshake and keys. Only authenticated Opus frames
reach the extension's decoder. No transport bytes or keys are logged here.
"""
from __future__ import annotations

from .receive_gate import DaveReceiveGate


def voice_client_class():
    import discord
    import davey
    from discord.ext import voice_recv
    from discord.ext.voice_recv import reader, rtp

    class SecureAudioReader(reader.AudioReader):
        def __init__(self, sink, voice_client, *, after=None):
            super().__init__(sink, voice_client, after=after)
            self.gate = DaveReceiveGate(voice_client, media_type=davey.MediaType.audio)
            self.dropped_packets = 0

        def callback(self, packet_data):
            if self.error:
                self.stop()
                return
            try:
                if not isinstance(packet_data, bytes) or not 12 <= len(packet_data) <= 65536:
                    return
                if self._is_ip_discovery_packet(packet_data) or rtp.is_rtcp(packet_data):
                    return
                packet = rtp.decode_rtp(packet_data)
                transport_payload = self.decryptor.decrypt_rtp(packet)
                opus = self.gate.decode(transport_payload, ssrc=packet.ssrc, payload_type=packet.payload)
                if opus is None:
                    self.dropped_packets += 1
                    return
                packet.decrypted_data = opus
            except Exception:
                self.dropped_packets += 1
                return
            try:
                self.speaking_timer.notify(packet.ssrc)
                self.packet_router.feed_rtp(packet)
            except Exception:
                self.error = RuntimeError("Discord voice decoder failed")
                self.stop()

    class SecureVoiceClient(voice_recv.VoiceRecvClient):
        def listen(self, sink, *, after=None):
            if not self.is_connected():
                raise discord.ClientException("Not connected to voice")
            if not isinstance(sink, voice_recv.AudioSink):
                raise TypeError("sink must be an AudioSink")
            if self.is_listening():
                raise discord.ClientException("Already receiving audio")
            self._reader = SecureAudioReader(sink, self, after=after)
            self._reader.start()

    return SecureVoiceClient
