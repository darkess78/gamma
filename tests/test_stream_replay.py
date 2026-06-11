from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gamma.stream.replay import StreamEvalReport, StreamReplayService
from gamma.stream.trace import StreamTraceStore


def _trace(
    *,
    trace_id: str,
    event_kind: str = "mic_transcript",
    decision: str = "reply",
    assistant_response: dict | None = None,
    output_events: list[dict] | None = None,
    action_items: list[dict] | None = None,
) -> dict:
    return {
        "recorded_at": "2026-05-09T00:00:00Z",
        "trace_id": trace_id,
        "input_event": {"event_id": trace_id, "kind": event_kind, "text": "hello"},
        "decision": {"decision": decision, "reason": "test"},
        "safety_decision": {},
        "action_plan": {"items": action_items or []},
        "assistant_response": assistant_response,
        "output_events": output_events or [],
        "timing_ms": {},
    }


class StreamReplayTest(unittest.TestCase):
    def _store(self, traces: list[dict]) -> StreamTraceStore:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "trace.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for trace in traces:
                handle.write(json.dumps(trace) + "\n")
        return StreamTraceStore(path)

    def test_recent_traces_reads_jsonl_tail(self) -> None:
        service = StreamReplayService(self._store([_trace(trace_id="a"), _trace(trace_id="b")]))

        recent = service.recent_traces(limit=1)

        self.assertEqual([item["trace_id"] for item in recent], ["b"])

    def test_eval_passes_valid_mic_reply(self) -> None:
        service = StreamReplayService(
            self._store([
                _trace(
                    trace_id="valid",
                    assistant_response={"spoken_text": "Hello."},
                    output_events=[{"type": "subtitle_line", "payload": {"text": "Hello."}}],
                )
            ])
        )

        report = service.evaluate_recent()

        self.assertTrue(report.passed)
        self.assertEqual(report.findings, [])

    def test_eval_flags_ignored_turn_with_output(self) -> None:
        service = StreamReplayService(
            self._store([
                _trace(
                    trace_id="bad-ignore",
                    event_kind="chat_message",
                    decision="ignore",
                    assistant_response={"spoken_text": "Nope."},
                    output_events=[{"type": "subtitle_line"}],
                )
            ])
        )

        report = service.evaluate_recent()

        self.assertFalse(report.passed)
        self.assertEqual(report.findings[0].rule, "ignored_turn_has_output")

    def test_eval_flags_high_risk_action_without_approval(self) -> None:
        service = StreamReplayService(
            self._store([
                _trace(
                    trace_id="bad-action",
                    assistant_response={"spoken_text": "Stored."},
                    output_events=[{"type": "subtitle_line"}],
                    action_items=[
                        {
                            "action_type": "tool.save_core_memory",
                            "risk_tier": "high",
                            "requires_approval": False,
                        }
                    ],
                )
            ])
        )

        report = service.evaluate_recent()

        self.assertFalse(report.passed)
        self.assertEqual(report.findings[0].rule, "high_risk_action_without_approval")

    def test_stream_replay_routes_delegate_to_service(self) -> None:
        from gamma.api.routes import stream_eval_recent, stream_recent_traces

        service = Mock()
        service.recent_traces.return_value = [{"trace_id": "trace-1"}]
        service.evaluate_recent.return_value = StreamEvalReport(checked_count=1, passed=True, findings=[])

        with patch("gamma.api.routes.get_stream_replay_service", return_value=service):
            recent = stream_recent_traces(limit=3)
            report = stream_eval_recent(limit=3)

        self.assertEqual(recent, {"items": [{"trace_id": "trace-1"}]})
        self.assertTrue(report.passed)
        service.recent_traces.assert_called_once_with(limit=3)
        service.evaluate_recent.assert_called_once_with(limit=3)

    def test_dashboard_stream_replay_routes_delegate_to_service(self) -> None:
        from gamma.dashboard.main import dashboard_stream_recent_eval, dashboard_stream_recent_traces

        service = Mock()
        service.stream_recent_traces.return_value = {"items": [{"trace_id": "trace-1"}]}
        service.stream_recent_eval.return_value = {"checked_count": 1, "passed": True, "findings": []}

        with patch("gamma.dashboard.main.get_dashboard_service", return_value=service):
            recent = dashboard_stream_recent_traces(limit=7)
            report = dashboard_stream_recent_eval(limit=7)

        self.assertEqual(recent, {"items": [{"trace_id": "trace-1"}]})
        self.assertEqual(report["passed"], True)
        service.stream_recent_traces.assert_called_once_with(limit=7)
        service.stream_recent_eval.assert_called_once_with(limit=7)


if __name__ == "__main__":
    unittest.main()
