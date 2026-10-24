from types import SimpleNamespace
from unittest.mock import patch

from gamma.integrations.discord.secure_receive import voice_client_class


def test_only_authenticated_opus_reaches_decoder():
    from discord.ext.voice_recv import reader, rtp

    cls = voice_client_class()
    fed, notified, decrypt_calls = [], [], []
    packet = SimpleNamespace(ssrc=10, payload=120, decrypted_data=None)
    session = SimpleNamespace(ready=True, decrypt=lambda *args: decrypt_calls.append(args) or b"authenticated-opus")
    voice = SimpleNamespace(
        _connection=SimpleNamespace(dave_protocol_version=1, dave_session=session),
        _get_id_from_ssrc=lambda _: 1,
    )

    def reader_init(instance, sink, client, **kwargs):
        instance.voice_client = client
        instance.error = None
        instance.decryptor = SimpleNamespace(decrypt_rtp=lambda _: b"encrypted-opus")
        instance.speaking_timer = SimpleNamespace(notify=notified.append)
        instance.packet_router = SimpleNamespace(feed_rtp=fed.append)

    with patch.object(reader.AudioReader, "__init__", reader_init), patch.object(reader.AudioReader, "start"), patch.object(rtp, "decode_rtp", return_value=packet), patch.object(rtp, "is_rtcp", return_value=False), patch.object(reader.AudioReader, "_is_ip_discovery_packet", return_value=False):
        from discord.ext import voice_recv
        sink = voice_recv.BasicSink(lambda *args: None)
        fake_client = SimpleNamespace(is_connected=lambda: True, is_listening=lambda: False, _connection=voice._connection, _get_id_from_ssrc=voice._get_id_from_ssrc)
        cls.listen(fake_client, sink)
        fake_client._reader.callback(b"x" * 16)
        assert len(fed) == 1 and packet.decrypted_data == b"authenticated-opus"
        assert notified == [10] and len(decrypt_calls) == 1
        fake_client._connection.dave_protocol_version = 0
        fake_client._reader.callback(b"x" * 16)
        assert len(fed) == 1
        fake_client._connection.dave_protocol_version = 1
        session.decrypt = lambda *args: (_ for _ in ()).throw(RuntimeError("private audio"))
        fake_client._reader.callback(b"x" * 16)
        assert len(fed) == 1


def test_untrusted_discord_role_cannot_grant_owner():
    from gamma.integrations.discord.adapter import _roles
    assert "owner" not in _roles(["owner", "trusted", "Administrator"], is_owner=False, trust="public")
    assert _roles(["owner"], is_owner=True, trust="owner") == ["owner"]
