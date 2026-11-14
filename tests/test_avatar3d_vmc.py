from __future__ import annotations

import asyncio
import contextlib
import base64
import io
import socket
import struct
import unittest
import wave

from gamma.config import settings
from gamma.conversation.shared import ALLOWED_EMOTIONS
from gamma.performer.avatar3d import (
    VRM0_EXPRESSIONS,
    VRM1_EXPRESSIONS,
    Avatar3DAdapter,
    Avatar3DAdapterConfig,
    Avatar3DRunner,
    VMCSender,
    text_to_syllables,
    vmc_blend_packets,
    wav_loudness,
    _expression_overrides,
    run_demo,
)
from gamma.performer.bus import PerformerEventBus
from gamma.performer.models import PerformerOutputEvent
from gamma.performer.osc import encode_osc_bundle, encode_osc_message


# --- a tiny OSC decoder, test-only -------------------------------------------


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    end = data.index(b"\x00", offset)
    value = data[offset:end].decode("utf-8")
    return value, (end + 4) & ~3


def _decode_message(data: bytes) -> tuple[str, list]:
    address, offset = _read_string(data, 0)
    tags, offset = _read_string(data, offset)
    args: list = []
    for tag in tags[1:]:
        if tag == "s":
            value, offset = _read_string(data, offset)
            args.append(value)
        elif tag == "f":
            args.append(struct.unpack(">f", data[offset : offset + 4])[0])
            offset += 4
        elif tag == "i":
            args.append(struct.unpack(">i", data[offset : offset + 4])[0])
            offset += 4
        elif tag in "TF":
            args.append(tag == "T")
    return address, args


def _decode_packet(data: bytes) -> list[tuple[str, list]]:
    if not data.startswith(b"#bundle\x00"):
        return [_decode_message(data)]
    offset = 16
    messages = []
    while offset < len(data):
        (size,) = struct.unpack(">i", data[offset : offset + 4])
        offset += 4
        messages.append(_decode_message(data[offset : offset + size]))
        offset += size
    return messages


def _frame_values(data: bytes) -> dict[str, float]:
    messages = _decode_packet(data)
    assert messages[-1][0] == "/VMC/Ext/Blend/Apply", messages[-1]
    return {args[0]: args[1] for address, args in messages if address == "/VMC/Ext/Blend/Val"}


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, address: tuple[str, int]) -> int:
        self.sent.append((data, address))
        return len(data)

    def close(self) -> None:
        self.closed = True


class _Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# --- OSC encoding --------------------------------------------------------------


class OscEncodingTest(unittest.TestCase):
    def test_blend_apply_has_no_arguments(self) -> None:
        self.assertEqual(
            encode_osc_message("/VMC/Ext/Blend/Apply"),
            b"/VMC/Ext/Blend/Apply\x00\x00\x00\x00" + b",\x00\x00\x00",
        )

    def test_blend_val_string_and_float(self) -> None:
        self.assertEqual(
            encode_osc_message("/VMC/Ext/Blend/Val", "Joy", 1.0),
            b"/VMC/Ext/Blend/Val\x00\x00" + b",sf\x00" + b"Joy\x00" + b"\x3f\x80\x00\x00",
        )
        # a 4-character name still needs a terminating null and a full pad block
        self.assertEqual(
            encode_osc_message("/VMC/Ext/Blend/Val", "Fun1", 0.5),
            b"/VMC/Ext/Blend/Val\x00\x00" + b",sf\x00" + b"Fun1\x00\x00\x00\x00" + b"\x3f\x00\x00\x00",
        )

    def test_int_bool_and_string_arguments(self) -> None:
        self.assertEqual(
            encode_osc_message("/a", 1, True, False, "ab"),
            b"/a\x00\x00" + b",iTFs\x00\x00\x00" + b"\x00\x00\x00\x01" + b"ab\x00\x00",
        )

    def test_bundle_layout(self) -> None:
        message = encode_osc_message("/VMC/Ext/Blend/Apply")
        self.assertEqual(
            encode_osc_bundle([message]),
            b"#bundle\x00" + b"\x00\x00\x00\x00\x00\x00\x00\x01" + b"\x00\x00\x00\x1c" + message,
        )

    def test_rejects_bad_address_and_types(self) -> None:
        with self.assertRaises(ValueError):
            encode_osc_message("VMC/no/slash")
        with self.assertRaises(TypeError):
            encode_osc_message("/a", b"bytes")  # type: ignore[arg-type]

    def test_frame_ends_with_apply(self) -> None:
        packets = vmc_blend_packets({"Joy": 1.0, "A": 0.25}, bundle=False)
        self.assertEqual(len(packets), 3)
        self.assertEqual(_decode_message(packets[0]), ("/VMC/Ext/Blend/Val", ["Joy", 1.0]))
        self.assertEqual(_decode_message(packets[1]), ("/VMC/Ext/Blend/Val", ["A", 0.25]))
        self.assertEqual(_decode_message(packets[2]), ("/VMC/Ext/Blend/Apply", []))
        bundled = vmc_blend_packets({"Joy": 1.0, "A": 0.25})
        self.assertEqual(len(bundled), 1)
        self.assertEqual(_frame_values(bundled[0]), {"Joy": 1.0, "A": 0.25})


# --- config and emotion mapping -------------------------------------------------


class Avatar3DMappingTest(unittest.TestCase):
    def test_settings_default_off_on_vmc_port(self) -> None:
        defaults = Avatar3DAdapterConfig()
        self.assertFalse(defaults.enabled)
        self.assertEqual(defaults.port, 39539)
        self.assertIsInstance(settings.avatar3d_enabled, bool)
        self.assertIsInstance(settings.avatar3d_port, int)

    def test_every_gamma_emotion_has_a_mapping(self) -> None:
        self.assertEqual(set(VRM0_EXPRESSIONS), set(ALLOWED_EMOTIONS))
        self.assertEqual(set(VRM1_EXPRESSIONS), set(ALLOWED_EMOTIONS))

    def test_expression_event_sets_vrm0_blendshapes(self) -> None:
        clock = _Clock()
        adapter = Avatar3DAdapter(Avatar3DAdapterConfig(enabled=True, auto_blink=False), clock=clock)
        action = adapter.handle_event(PerformerOutputEvent(type="expression_set", turn_id="t", payload={"expression": "Happy"}))
        self.assertEqual(action.action_type, "expression")
        clock.now += 1.0
        frame = adapter.frame()
        self.assertEqual(frame["Joy"], 1.0)
        self.assertEqual(frame["Angry"], 0.0)
        for name in ("A", "I", "U", "E", "O", "Blink"):
            self.assertEqual(frame[name], 0.0)

        adapter.handle_event(PerformerOutputEvent(type="expression_set", turn_id="t", payload={"expression": "annoyed"}))
        clock.now += 0.075  # halfway through the 150 ms fade
        frame = adapter.frame()
        self.assertAlmostEqual(frame["Joy"], 0.5, places=3)
        self.assertAlmostEqual(frame["Angry"], 0.4, places=3)
        clock.now += 1.0
        frame = adapter.frame()
        self.assertEqual(frame["Joy"], 0.0)
        self.assertEqual(frame["Angry"], 0.8)

    def test_vrm1_naming(self) -> None:
        clock = _Clock()
        adapter = Avatar3DAdapter(Avatar3DAdapterConfig(enabled=True, naming="vrm1", auto_blink=False), clock=clock)
        adapter.handle_event(PerformerOutputEvent(type="expression_set", turn_id="t", payload={"expression": "concerned"}))
        clock.now += 1.0
        frame = adapter.frame()
        self.assertEqual(frame["sad"], 0.7)
        self.assertIn("aa", frame)
        self.assertIn("blink", frame)
        self.assertNotIn("Joy", frame)

    def test_unknown_emotion_is_safe_noop_and_overrides_apply(self) -> None:
        overrides = _expression_overrides({"Teasing": {"Shana_Smug": 2, "Bad": "x"}, "bogus": "nope"})
        self.assertEqual(overrides, {"teasing": {"Shana_Smug": 1.0}})
        clock = _Clock()
        adapter = Avatar3DAdapter(Avatar3DAdapterConfig(enabled=True, auto_blink=False, expressions=overrides), clock=clock)
        action = adapter.handle_event(PerformerOutputEvent(type="expression_set", turn_id="t", payload={"expression": "sleepy"}))
        self.assertEqual(action.action_type, "no_mapping")
        adapter.handle_event(PerformerOutputEvent(type="expression_set", turn_id="t", payload={"expression": "teasing"}))
        clock.now += 1.0
        frame = adapter.frame()
        self.assertEqual(frame["Shana_Smug"], 1.0)
        self.assertEqual(frame.get("Fun", 0.0), 0.0)

    def test_disabled_adapter_ignores_events(self) -> None:
        adapter = Avatar3DAdapter(Avatar3DAdapterConfig(enabled=False, auto_blink=False))
        action = adapter.handle_event(PerformerOutputEvent(type="expression_set", turn_id="t", payload={"expression": "happy"}))
        self.assertEqual(action.action_type, "disabled")
        self.assertEqual(adapter.frame()["Joy"], 0.0)


# --- lip sync -------------------------------------------------------------------


def _max_mouth(adapter: Avatar3DAdapter, clock: _Clock, seconds: float, names=("A", "I", "U", "E", "O")) -> dict[str, float]:
    peaks = {name: 0.0 for name in names}
    start = clock.now
    while clock.now < start + seconds:
        frame = adapter.frame()
        for name in names:
            peaks[name] = max(peaks[name], frame[name])
        clock.now += 1 / 60
    return peaks


class Avatar3DLipSyncTest(unittest.TestCase):
    def test_text_to_syllables(self) -> None:
        syllables, duration = text_to_syllables("Hello, ok!", 10.0)
        self.assertEqual([s.vowel for s in syllables], ["e", "o", "o"])
        self.assertAlmostEqual(syllables[0].start, 0.0)
        self.assertAlmostEqual(syllables[1].end, 0.5)  # five letters at 10 cps
        self.assertAlmostEqual(duration, 0.5 + 0.1 + 0.25 + 0.2 + 0.1 + 0.4)

    def test_voiced_subtitle_moves_mouth(self) -> None:
        clock = _Clock()
        adapter = Avatar3DAdapter(Avatar3DAdapterConfig(enabled=True, auto_blink=False), clock=clock)
        adapter.handle_event(PerformerOutputEvent(type="speech_started", turn_id="t1"))
        action = adapter.handle_event(PerformerOutputEvent(type="subtitle_update", turn_id="t1", payload={"text": "Haha so"}))
        self.assertEqual(action.action_type, "lipsync")
        adapter.handle_event(PerformerOutputEvent(type="speech_ended", turn_id="t1"))
        self.assertTrue(adapter.status()["speaking"])
        peaks = _max_mouth(adapter, clock, 1.0)
        self.assertGreater(peaks["A"], 0.5)
        self.assertGreater(peaks["O"], 0.5)
        self.assertEqual(peaks["E"], 0.0)
        self.assertEqual(adapter.frame()["A"], 0.0)  # finished
        self.assertFalse(adapter.status()["speaking"])

    def test_text_only_subtitle_does_not_move_mouth(self) -> None:
        clock = _Clock()
        adapter = Avatar3DAdapter(Avatar3DAdapterConfig(enabled=True, auto_blink=False), clock=clock)
        action = adapter.handle_event(PerformerOutputEvent(type="subtitle_update", turn_id="t1", payload={"text": "Haha"}))
        self.assertEqual(action.action_type, "ignored")
        self.assertEqual(max(_max_mouth(adapter, clock, 0.5).values()), 0.0)

    def test_lipsync_off_leaves_mouth_to_the_app(self) -> None:
        clock = _Clock()
        adapter = Avatar3DAdapter(Avatar3DAdapterConfig(enabled=True, auto_blink=False, lipsync="off"), clock=clock)
        adapter.handle_event(PerformerOutputEvent(type="speech_started", turn_id="t1"))
        adapter.handle_event(PerformerOutputEvent(type="subtitle_update", turn_id="t1", payload={"text": "Haha"}))
        self.assertEqual(max(_max_mouth(adapter, clock, 0.5).values()), 0.0)

    def test_chunks_after_subtitle_do_not_double_and_clear_stops(self) -> None:
        clock = _Clock()
        adapter = Avatar3DAdapter(Avatar3DAdapterConfig(enabled=True, auto_blink=False), clock=clock)
        adapter.handle_event(PerformerOutputEvent(type="speech_started", turn_id="t1"))
        adapter.handle_event(PerformerOutputEvent(type="subtitle_update", turn_id="t1", payload={"text": "aaa aaa"}))
        action = adapter.handle_event(PerformerOutputEvent(type="speech_chunk_ready", turn_id="t1", payload={"text": "aaa aaa"}))
        self.assertEqual(action.action_type, "ignored")
        self.assertEqual(adapter.status()["queued_speech_segments"], 1)
        adapter.handle_event(PerformerOutputEvent(type="output_cleared", turn_id="t1"))
        self.assertEqual(max(_max_mouth(adapter, clock, 0.3).values()), 0.0)

    def test_speech_chunks_queue_and_use_wav_duration(self) -> None:
        rate = 8000
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            writer.writeframes(struct.pack("<h", 12000) * rate * 2)  # 2 s of constant loudness
        audio = buffer.getvalue()
        duration, envelope = wav_loudness(audio)  # type: ignore[misc]
        self.assertAlmostEqual(duration, 2.0)
        self.assertTrue(envelope and max(envelope) == 1.0)

        clock = _Clock()
        adapter = Avatar3DAdapter(Avatar3DAdapterConfig(enabled=True, auto_blink=False), clock=clock)
        payload = {"text": "ha", "audio_base64": base64.b64encode(audio).decode(), "audio_content_type": "audio/wav"}
        adapter.handle_event(PerformerOutputEvent(type="speech_chunk_ready", turn_id="t2", payload=payload))
        adapter.handle_event(PerformerOutputEvent(type="speech_chunk_ready", turn_id="t2", payload={"text": "oh"}))
        segments = adapter._segments
        self.assertEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0].end - segments[0].start, 2.0)
        self.assertAlmostEqual(segments[1].start, segments[0].end)  # queued, not overlapping

    def test_auto_blink(self) -> None:
        clock = _Clock()
        import random

        adapter = Avatar3DAdapter(Avatar3DAdapterConfig(enabled=True), clock=clock, rng=random.Random(7))
        peaks = _max_mouth(adapter, clock, 7.0, names=("Blink",))
        self.assertGreater(peaks["Blink"], 0.9)


# --- transport ------------------------------------------------------------------


class Avatar3DTransportTest(unittest.TestCase):
    def test_sender_reaches_a_localhost_udp_socket(self) -> None:
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(2.0)
            port = receiver.getsockname()[1]
            sender = VMCSender("127.0.0.1", port)
            try:
                sender.send_blendshapes({"Joy": 1.0})
            finally:
                sender.close()
            data, _ = receiver.recvfrom(65535)
        finally:
            receiver.close()
        self.assertEqual(_frame_values(data), {"Joy": 1.0})

    def test_runner_streams_frames_for_bus_events(self) -> None:
        fake = _FakeSocket()

        async def scenario() -> Avatar3DRunner:
            bus = PerformerEventBus()
            adapter = Avatar3DAdapter(
                Avatar3DAdapterConfig(enabled=True, auto_blink=False, expression_fade_ms=0, fps=60, chars_per_second=20.0)
            )
            runner = Avatar3DRunner(bus, adapter, VMCSender("192.0.2.10", 39539, socket_factory=lambda: fake))
            task = asyncio.create_task(runner.run_until_stopped())
            for _ in range(50):
                await asyncio.sleep(0.01)
                if runner.status()["subscriber_id"]:
                    break
            bus.publish(PerformerOutputEvent(type="expression_set", turn_id="t1", payload={"expression": "happy"}))
            bus.publish(PerformerOutputEvent(type="speech_started", turn_id="t1"))
            bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="t1", payload={"text": "Haaaa haaaa"}))
            bus.publish(PerformerOutputEvent(type="speech_ended", turn_id="t1"))
            await asyncio.sleep(0.4)
            runner.stop()
            await asyncio.wait_for(task, timeout=2.0)
            return runner

        runner = asyncio.run(scenario())
        self.assertTrue(fake.closed)
        self.assertGreater(len(fake.sent), 5)
        self.assertTrue(all(address == ("192.0.2.10", 39539) for _, address in fake.sent))
        frames = [_frame_values(data) for data, _ in fake.sent]
        self.assertTrue(any(frame.get("Joy") == 1.0 and frame.get("A", 0.0) > 0.3 for frame in frames))
        final = frames[-1]
        self.assertTrue(all(value == 0.0 for value in final.values()))  # neutral on stop
        self.assertEqual(runner.status()["handled_count"], 4)
        self.assertFalse(runner.status()["running"])

    def test_demo_cycles_every_emotion(self) -> None:
        fake = _FakeSocket()
        sender = VMCSender("192.0.2.10", 39539, socket_factory=lambda: fake)
        with contextlib.redirect_stdout(io.StringIO()):
            sent = run_demo("192.0.2.10", 39539, fps=50, seconds_per_emotion=0.05, sender=sender)
        self.assertEqual(sent, len(fake.sent))
        frames = [_frame_values(data) for data, _ in fake.sent]
        for shape in ("Joy", "Fun", "Sorrow", "Angry", "Surprised"):
            self.assertTrue(any(frame.get(shape, 0.0) > 0.0 for frame in frames), shape)
        self.assertTrue(all(value == 0.0 for value in frames[-1].values()))

    def test_runner_does_not_start_when_disabled(self) -> None:
        fake = _FakeSocket()
        runner = Avatar3DRunner(
            PerformerEventBus(),
            Avatar3DAdapter(Avatar3DAdapterConfig(enabled=False)),
            VMCSender("127.0.0.1", 39539, socket_factory=lambda: fake),
        )
        asyncio.run(runner.run_until_stopped())
        self.assertEqual(fake.sent, [])
        self.assertFalse(runner.status()["running"])


if __name__ == "__main__":
    unittest.main()
