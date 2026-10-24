import asyncio
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("playback_relay", Path(__file__).resolve().parents[1] / "src/gamma/dashboard/playback_relay.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Browser:
    def __init__(self, messages):
        self.messages, self.sent, self.closed = messages, [], []
        self.blocked = asyncio.Event()
        self.cancelled = False
    async def receive_text(self):
        if self.messages:
            return self.messages.pop(0)
        try:
            await self.blocked.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
    async def send_text(self, message):
        self.sent.append(message)
    async def close(self, code):
        self.closed.append(code)


class Upstream:
    def __init__(self):
        self.sent = []
        self.blocked = asyncio.Event()
        self.cancelled = False
    async def send(self, message):
        self.sent.append(message)
    async def recv(self):
        try:
            await self.blocked.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class RelayTest(unittest.TestCase):
    def test_valid_message_then_invalid_action_closes_without_forwarding_action(self):
        async def run():
            browser = Browser(['{"type":"playback_heartbeat"}', '{"type":"delete_state"}'])
            upstream = Upstream()
            await asyncio.wait_for(module.relay(browser, upstream), 2)
            self.assertEqual(upstream.sent, ['{"type":"playback_heartbeat"}'])
            self.assertEqual(browser.closed, [1008])
        asyncio.run(run())

    def test_oversize_and_malformed_envelopes_are_rejected(self):
        async def run(message):
            browser, upstream = Browser([message]), Upstream()
            await asyncio.wait_for(module.relay(browser, upstream), 2)
            self.assertFalse(upstream.sent)
            self.assertEqual(browser.closed, [1008])
        for message in ('x' * 8193, 'not json', '[]', '{"type":42}'):
            asyncio.run(run(message))

    def test_parent_cancellation_cleans_both_pumps(self):
        async def run():
            browser, upstream = Browser([]), Upstream()
            task = asyncio.create_task(module.relay(browser, upstream))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(browser.cancelled and upstream.cancelled)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
