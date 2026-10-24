"""Dependency-free acceptance contract for the local coding worker."""
import asyncio
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('voice_controls', Path(__file__).parents[1] / 'src/gamma/integrations/discord/voice_controls.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Voice:
    def __init__(self):
        self.connected = True
        self.sink = None
        self.listen_count = 0
        self.stop_count = 0
        self.disconnect_count = 0
    def is_connected(self): return self.connected
    def listen(self, sink):
        self.sink = sink
        self.listen_count += 1
    def stop_listening(self): self.stop_count += 1
    async def disconnect(self, *, force=False):
        self.disconnect_count += 1
        self.connected = False


class Channel:
    def __init__(self, guild='100', failure=False):
        self.guild = type('Guild', (), {'id': int(guild)})()
        self.calls = []
        self.voice = Voice()
        self.failure = failure
    async def connect(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(0)
        if self.failure: raise RuntimeError('secret connection details')
        return self.voice


class Client:
    def __init__(self, channel): self.channel = channel; self.ids = []
    def get_channel(self, value): self.ids.append(value); return self.channel


class ControlsTest(unittest.IsolatedAsyncioTestCase):
    def build(self, channel=None, owner_check=None, sink_factory=None):
        channel = channel or Channel()
        self.client = Client(channel)
        self.voice_type = object()
        return module.VoiceSessionController(
            self.client, guild_id='100', channel_id='200',
            owner_check=owner_check or (lambda user_id: user_id == '1'),
            voice_factory=self.voice_type, sink_factory=sink_factory or (lambda: object()),
        )
    async def test_owner_lifecycle_idempotent(self):
        control = self.build()
        self.assertEqual((await control.handle('1', 'join'))['state'], 'listening')
        self.assertEqual((await control.handle('1', 'join'))['state'], 'listening')
        self.assertEqual(len(self.client.channel.calls), 1)
        self.assertEqual(self.client.channel.calls[0], {'cls': self.voice_type, 'self_deaf': False})
        self.assertEqual(self.client.ids, [200])
        self.assertEqual((await control.handle('1', 'pause'))['state'], 'paused')
        self.assertEqual((await control.handle('1', 'pause'))['state'], 'paused')
        self.assertEqual(self.client.channel.voice.stop_count, 1)
        self.assertEqual((await control.handle('1', 'resume'))['state'], 'listening')
        self.assertEqual(self.client.channel.voice.listen_count, 2)
        self.assertEqual((await control.handle('1', 'leave'))['state'], 'disconnected')
        self.assertEqual((await control.handle('1', 'leave'))['state'], 'disconnected')
        self.assertEqual(self.client.channel.voice.disconnect_count, 1)
    async def test_public_cannot_control_or_observe(self):
        control = self.build()
        for command in ['join', 'leave', 'pause', 'resume', 'status']:
            result = await control.handle('2', command)
            self.assertFalse(result['ok'])
            self.assertEqual(result['reason'], 'owner_required')
        self.assertEqual(self.client.ids, [])
    async def test_only_literal_true_auth(self):
        for truthy in [1, 'owner', object()]:
            control = self.build(owner_check=lambda _: truthy)
            self.assertEqual((await control.handle('1', 'join'))['reason'], 'owner_required')
            self.assertEqual(self.client.ids, [])
    async def test_concurrent_join_once(self):
        control = self.build()
        results = await asyncio.gather(*(control.handle('1', 'join') for _ in range(6)))
        self.assertTrue(all(r['ok'] for r in results))
        self.assertEqual(len(self.client.channel.calls), 1)
    async def test_wrong_guild_missing_channel_and_unknown_command(self):
        control = self.build(Channel(guild='101'))
        self.assertEqual((await control.handle('1', 'join'))['reason'], 'channel_not_allowed')
        self.assertEqual(self.client.channel.calls, [])
        control = self.build()
        self.client.channel = None
        self.assertEqual((await control.handle('1', 'join'))['reason'], 'channel_unavailable')
        self.assertEqual((await control.handle('1', 'shell'))['reason'], 'unknown_command')
    async def test_failure_sanitized_and_retry(self):
        control = self.build(Channel(failure=True))
        result = await control.handle('1', 'join')
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'voice_error')
        self.assertNotIn('secret', str(result))
        self.client.channel.failure = False
        self.assertEqual((await control.handle('1', 'join'))['state'], 'listening')
    async def test_sink_failure_disconnects(self):
        def fail(): raise RuntimeError('private sink error')
        control = self.build(sink_factory=fail)
        self.assertFalse((await control.handle('1', 'join'))['ok'])
        self.assertFalse(self.client.channel.voice.connected)
    async def test_resume_without_connection(self):
        control = self.build()
        result = await control.handle('1', 'resume')
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'not_connected')
    async def test_invalid_ids_rejected(self):
        for value in ['', '-1', '0', '1.2', 'abc']:
            with self.assertRaises(ValueError):
                module.VoiceSessionController(Client(Channel()), guild_id=value, channel_id='200', owner_check=lambda _: True, voice_factory=object(), sink_factory=object)
    async def test_status_reconciles_disconnected_transport(self):
        control = self.build()
        await control.handle('1', 'join')
        self.client.channel.voice.connected = False
        self.assertEqual((await control.handle('1', 'status'))['state'], 'disconnected')
        self.assertEqual((await control.handle('1', 'resume'))['reason'], 'not_connected')
    async def test_resume_failure_sanitized(self):
        control = self.build()
        await control.handle('1', 'join')
        await control.handle('1', 'pause')
        def fail(_): raise RuntimeError('private details')
        self.client.channel.voice.listen = fail
        result = await control.handle('1', 'resume')
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'voice_error')
        self.assertNotIn('private', str(result))
    async def test_leave_preserves_cancellation(self):
        control = self.build()
        await control.handle('1', 'join')
        async def cancelled(**kwargs): raise asyncio.CancelledError()
        self.client.channel.voice.disconnect = cancelled
        with self.assertRaises(asyncio.CancelledError):
            await control.handle('1', 'leave')
    async def test_pause_failure_must_not_claim_paused(self):
        control = self.build()
        await control.handle('1', 'join')
        def fail(): raise RuntimeError('private audio error')
        self.client.channel.voice.stop_listening = fail
        result = await control.handle('1', 'pause')
        self.assertFalse(result['ok'])
        self.assertNotEqual(result['state'], 'paused')
        self.assertNotIn('private', str(result))
    async def test_disconnect_failure_retains_transport_and_reports_failure(self):
        control = self.build()
        await control.handle('1', 'join')
        async def fail(**kwargs): raise RuntimeError('private details')
        self.client.channel.voice.disconnect = fail
        result = await control.handle('1', 'leave')
        self.assertFalse(result['ok'])
        self.assertNotEqual(result['state'], 'disconnected')
        self.assertNotIn('private', str(result))
    async def test_resume_failure_disconnects_before_forgetting_transport(self):
        control = self.build()
        await control.handle('1', 'join')
        await control.handle('1', 'pause')
        def fail(_): raise RuntimeError('private details')
        self.client.channel.voice.listen = fail
        await control.handle('1', 'resume')
        self.assertFalse(self.client.channel.voice.connected)
    async def test_cancelled_sink_cleans_up(self):
        def cancelled(): raise asyncio.CancelledError()
        control = self.build(sink_factory=cancelled)
        with self.assertRaises(asyncio.CancelledError):
            await control.handle('1', 'join')
        self.assertFalse(self.client.channel.voice.connected)

    async def test_failed_cleanup_blocks_duplicate_join(self):
        control = self.build()
        await control.handle('1', 'join')
        async def fail(**kwargs): raise RuntimeError('private details')
        self.client.channel.voice.disconnect = fail
        await control.handle('1', 'leave')
        result = await control.handle('1', 'join')
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.client.channel.calls), 1)

    async def test_sink_and_cleanup_failure_retains_recoverable_transport(self):
        def fail_sink(): raise RuntimeError('private sink')
        control = self.build(sink_factory=fail_sink)
        async def fail(**kwargs): raise RuntimeError('private disconnect')
        self.client.channel.voice.disconnect = fail
        result = await control.handle('1', 'join')
        self.assertFalse(result['ok'])
        self.assertEqual(result['state'], 'error')
        self.assertIs(control._voice, self.client.channel.voice)
        self.assertNotIn('private', str(result))


if __name__ == '__main__': unittest.main()
