from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gamma.resources.probe import MachineResourceMonitor, collect_resource_snapshot, gpu_status


class ResourceProbeTest(unittest.TestCase):
    def test_gpu_status_parses_gpu_and_process_snapshots(self) -> None:
        def run(command, **_kwargs):
            if "--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="0, GPU-a, NVIDIA RTX, 24576, 1024, 23552, 12, 45\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(command, 0, stdout="GPU-a, 1234, 512\n", stderr="")

        with patch("gamma.resources.probe.subprocess.run", side_effect=run):
            payload = gpu_status()

        self.assertTrue(payload["ok"])
        gpu = payload["gpus"][0]
        self.assertEqual(gpu["index"], 0)
        self.assertEqual(gpu["uuid"], "GPU-a")
        self.assertEqual(gpu["memory_free_mb"], 23552)
        self.assertEqual(gpu["processes"][0]["pid"], 1234)
        self.assertEqual(gpu["processes"][0]["used_memory_mb"], 512)

    def test_gpu_status_handles_missing_nvidia_smi(self) -> None:
        with patch("gamma.resources.probe.subprocess.run", side_effect=FileNotFoundError()):
            payload = gpu_status()

        self.assertEqual(payload, {"ok": False, "detail": "nvidia-smi-not-found"})

    def test_collect_resource_snapshot_preserves_dashboard_shape_when_gpu_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("gamma.resources.probe.psutil.cpu_percent", return_value=3.5),
                patch(
                    "gamma.resources.probe.psutil.virtual_memory",
                    return_value=SimpleNamespace(total=100, available=60, used=40, percent=40.0),
                ),
            ):
                snapshot = collect_resource_snapshot(project_root=Path(temp_dir), include_gpu=False)

        payload = snapshot.as_dashboard_payload(gpu_enabled=False, refresh_interval_seconds=10)
        self.assertEqual(payload["cpu_percent"], 3.5)
        self.assertEqual(payload["memory"]["available_bytes"], 60)
        self.assertEqual(payload["gpu"], {"ok": False, "detail": "disabled"})
        self.assertFalse(payload["gpu_enabled"])
        self.assertEqual(payload["refresh_interval_seconds"], 10)

    def test_machine_resource_monitor_reuses_fresh_snapshot(self) -> None:
        calls = 0

        def collect(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return collect_resource_snapshot(project_root=Path(temp_dir), include_gpu=False)

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("gamma.resources.probe.psutil.cpu_percent", return_value=1.0),
                patch(
                    "gamma.resources.probe.psutil.virtual_memory",
                    return_value=SimpleNamespace(total=100, available=90, used=10, percent=10.0),
                ),
                patch("gamma.resources.probe.collect_resource_snapshot", side_effect=collect),
            ):
                monitor = MachineResourceMonitor(
                    project_root=Path(temp_dir),
                    enable_gpu=lambda: False,
                    refresh_interval_seconds=lambda: 60,
                )
                first = monitor.dashboard_payload()
                second = monitor.dashboard_payload()

        self.assertEqual(calls, 1)
        self.assertEqual(first["sampled_at"], second["sampled_at"])


if __name__ == "__main__":
    unittest.main()
