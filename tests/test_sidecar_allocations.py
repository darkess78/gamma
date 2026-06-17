from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gamma.resources.allocations import latest_sidecar_allocations, recent_sidecar_allocation_entries


class SidecarAllocationTest(unittest.TestCase):
    def test_latest_allocations_prefers_latest_per_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "supervisor.jsonl"
            log_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": "2026-06-17T00:00:00+00:00",
                                "event": "resource.sidecar_allocation.observed",
                                "provider": "qwen-tts",
                                "kind": "qwen-tts",
                                "estimated_vram_mb": 9216,
                                "observed_vram_mb": 8800,
                            }
                        ),
                        "not-json",
                        json.dumps({"event": "resource.startup_admission.selected"}),
                        json.dumps(
                            {
                                "timestamp": "2026-06-17T00:01:00+00:00",
                                "event": "resource.sidecar_allocation.observed",
                                "provider": "qwen-tts",
                                "kind": "qwen-tts",
                                "estimated_vram_mb": 9216,
                                "observed_vram_mb": 8900,
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            allocations = latest_sidecar_allocations(
                log_path,
                ttl_seconds=300,
                now=datetime(2026, 6, 17, 0, 2, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].observed_vram_mb, 8900)
        self.assertFalse(allocations[0].stale)
        self.assertEqual(allocations[0].age_seconds, 60.0)

    def test_recent_allocations_marks_stale_when_ttl_expires(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "supervisor.jsonl"
            log_path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-06-17T00:00:00Z",
                        "event": "resource.sidecar_allocation.observed",
                        "provider": "audio-understanding",
                        "kind": "audio-understanding",
                        "estimated_vram_mb": 1536,
                        "observed_vram_mb": 900,
                    }
                ),
                encoding="utf-8",
            )

            allocations = recent_sidecar_allocation_entries(
                log_path,
                ttl_seconds=30,
                now=datetime(2026, 6, 17, 0, 2, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(len(allocations), 1)
        self.assertTrue(allocations[0].stale)
        self.assertFalse(allocations[0].fresh)
        self.assertEqual(allocations[0].allocation_delta_mb, -636)


if __name__ == "__main__":
    unittest.main()
