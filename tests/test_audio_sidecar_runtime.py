from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from gamma.config import settings
from gamma.resources.models import PlacementCandidate, PlacementDecision, RuntimeTarget, WorkloadSpec
from gamma.resources.runtime_registry import ResourceRoutingPolicy, ResourceRoutingRegistry
from gamma.supervisor.manager import ProcessManager, SidecarVramEstimate


class AudioSidecarRuntimeTest(unittest.TestCase):
    def test_manager_prefers_isolated_audio_python(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            python_path = project_root / ".venv-audio" / "bin" / "python"
            python_path.parent.mkdir(parents=True)
            python_path.write_text("", encoding="utf-8")
            with (
                patch.object(settings, "project_root", project_root),
                patch.object(settings, "audio_understanding_python", None),
            ):
                manager = ProcessManager()
                resolved = manager._service_python("audio-understanding")

        self.assertEqual(resolved, str(python_path))

    def test_manager_honors_explicit_audio_python(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            python_path = Path(temp_dir) / "python"
            python_path.write_text("", encoding="utf-8")
            with patch.object(settings, "audio_understanding_python", str(python_path)):
                manager = ProcessManager()
                resolved = manager._service_python("audio-understanding")

        self.assertEqual(resolved, str(python_path))

    def test_audio_understanding_startup_admission_only_sets_auto_devices(self) -> None:
        manager = ProcessManager()
        with (
            patch.object(settings, "audio_analysis_device", "cpu"),
            patch.object(settings, "speaker_emotion_device", "auto"),
            patch.object(settings, "audio_event_device", "cuda:0"),
            patch.object(manager, "_sidecar_vram_estimate", return_value=SidecarVramEstimate(vram_mb=1234, source="configured_fallback")),
            patch.object(manager, "_admitted_sidecar_device", return_value="cuda:1") as admitted,
        ):
            env = manager._audio_understanding_admission_env()

        admitted.assert_called_once_with(
            provider="audio-understanding",
            kind="audio-understanding",
            modality="audio",
            model=None,
            estimated_vram_mb=1234,
            estimate_source="configured_fallback",
            estimate_observed_age_seconds=None,
            estimate_ttl_seconds=None,
        )
        self.assertEqual(env, {"SHANA_SPEAKER_EMOTION_DEVICE": "cuda:1"})

    def test_audio_understanding_explicit_devices_skip_startup_admission(self) -> None:
        manager = ProcessManager()
        with (
            patch.object(settings, "audio_analysis_device", "cpu"),
            patch.object(settings, "speaker_emotion_device", "cuda:0"),
            patch.object(settings, "audio_event_device", "cpu"),
            patch.object(manager, "_admitted_sidecar_device", return_value="cuda:1") as admitted,
            patch("gamma.supervisor.manager.log_event"),
        ):
            env = manager._audio_understanding_admission_env()

        admitted.assert_not_called()
        self.assertEqual(env, {})

    def test_qwen_startup_admission_sets_auto_device_only(self) -> None:
        manager = ProcessManager()
        with (
            patch.dict("os.environ", {"QWEN_TTS_DEVICE": "auto"}, clear=False),
            patch.object(settings, "qwen_tts_device", ""),
            patch.object(manager, "_sidecar_vram_estimate", return_value=SidecarVramEstimate(vram_mb=4321, source="configured_fallback")),
            patch.object(manager, "_admitted_sidecar_device", return_value="cuda:1") as admitted,
        ):
            env = manager._qwen_tts_admission_env()

        admitted.assert_called_once_with(
            provider="qwen-tts",
            kind="qwen-tts",
            modality="speech",
            model="qwen-tts",
            estimated_vram_mb=4321,
            estimate_source="configured_fallback",
            estimate_observed_age_seconds=None,
            estimate_ttl_seconds=None,
        )
        self.assertEqual(env, {"QWEN_TTS_DEVICE": "cuda:1"})

        with (
            patch.dict("os.environ", {"QWEN_TTS_DEVICE": "cuda:0"}, clear=False),
            patch.object(settings, "qwen_tts_device", "auto"),
            patch.object(manager, "_admitted_sidecar_device", return_value="cuda:1") as admitted,
            patch("gamma.supervisor.manager.log_event"),
        ):
            env = manager._qwen_tts_admission_env()

        admitted.assert_not_called()
        self.assertEqual(env, {})

    def test_qwen_startup_admission_reads_auto_from_app_config(self) -> None:
        manager = ProcessManager()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(settings, "qwen_tts_device", "auto"),
            patch.object(manager, "_sidecar_vram_estimate", return_value=SidecarVramEstimate(vram_mb=4321, source="configured_fallback")),
            patch.object(manager, "_admitted_sidecar_device", return_value="cuda:1") as admitted,
        ):
            env = manager._qwen_tts_admission_env()

        admitted.assert_called_once_with(
            provider="qwen-tts",
            kind="qwen-tts",
            modality="speech",
            model="qwen-tts",
            estimated_vram_mb=4321,
            estimate_source="configured_fallback",
            estimate_observed_age_seconds=None,
            estimate_ttl_seconds=None,
        )
        self.assertEqual(env, {"QWEN_TTS_DEVICE": "cuda:1"})

    def test_qwen_explicit_app_config_device_skips_startup_admission(self) -> None:
        manager = ProcessManager()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(settings, "qwen_tts_device", "cpu"),
            patch.object(manager, "_admitted_sidecar_device", return_value="cuda:1") as admitted,
            patch("gamma.supervisor.manager.log_event"),
        ):
            env = manager._qwen_tts_admission_env()

        admitted.assert_not_called()
        self.assertEqual(env, {})

    def test_startup_admission_disabled_does_not_select_sidecar_device(self) -> None:
        manager = ProcessManager()
        registry = ResourceRoutingRegistry(policy=ResourceRoutingPolicy(startup_admission=False), targets=())
        with (
            patch("gamma.supervisor.manager.load_resource_routing_registry", return_value=registry),
            patch("gamma.supervisor.manager.ResourcePlacementCoordinator") as coordinator,
            patch("gamma.supervisor.manager.log_event"),
        ):
            device = manager._admitted_sidecar_device(
                provider="qwen-tts",
                kind="qwen-tts",
                modality="speech",
                model="qwen-tts",
            )

        coordinator.assert_not_called()
        self.assertIsNone(device)

    def test_startup_admission_logs_selected_decision_with_estimate(self) -> None:
        manager = ProcessManager()
        target = RuntimeTarget(id="qwen-gpu", kind="qwen-tts", provider="qwen-tts", device="cuda:1", models=("qwen-tts",), modalities=("speech",))
        decision = PlacementDecision(
            workload=WorkloadSpec(id="qwen-tts:startup", kind="qwen-tts", provider="qwen-tts", model="qwen-tts", modality="speech", estimated_vram_mb=9000, minimum_headroom_mb=1024),
            selected=PlacementCandidate(target=target, score=1.0, reason="gpu-headroom", gpu_index=1, free_vram_mb=12000, projected_headroom_mb=2000),
            status="selected",
            snapshot_age_seconds=0.1,
        )
        registry = ResourceRoutingRegistry(
            policy=ResourceRoutingPolicy(startup_admission=True, minimum_headroom_mb=1024),
            targets=(target,),
        )
        with (
            patch("gamma.supervisor.manager.load_resource_routing_registry", return_value=registry),
            patch("gamma.supervisor.manager.ResourcePlacementCoordinator") as coordinator,
            patch("gamma.supervisor.manager.log_event") as log_event,
        ):
            coordinator.return_value.rank.return_value = decision
            device = manager._admitted_sidecar_device(
                provider="qwen-tts",
                kind="qwen-tts",
                modality="speech",
                model="qwen-tts",
                estimated_vram_mb=9000,
                estimate_source="observed_fresh",
                estimate_observed_age_seconds=12.5,
                estimate_ttl_seconds=300,
            )

        self.assertEqual(device, "cuda:1")
        coordinator.return_value.rank.assert_called_once()
        workload = coordinator.return_value.rank.call_args.args[0]
        self.assertEqual(workload.estimated_vram_mb, 9000)
        self.assertEqual(log_event.call_args.args[2], "resource.startup_admission.selected")
        self.assertEqual(log_event.call_args.kwargs["estimated_vram_mb"], 9000)
        self.assertEqual(log_event.call_args.kwargs["estimate_source"], "observed_fresh")
        self.assertEqual(log_event.call_args.kwargs["estimate_observed_age_seconds"], 12.5)
        self.assertEqual(log_event.call_args.kwargs["estimate_ttl_seconds"], 300)
        self.assertEqual(log_event.call_args.kwargs["selected"]["target_id"], "qwen-gpu")

    def test_startup_admission_logs_rejected_decision(self) -> None:
        manager = ProcessManager()
        decision = PlacementDecision(
            workload=WorkloadSpec(id="qwen-tts:startup", kind="qwen-tts", provider="qwen-tts", model="qwen-tts", modality="speech", estimated_vram_mb=9000, minimum_headroom_mb=1024),
            selected=None,
            rejected={"qwen-gpu": "insufficient_vram_headroom"},
            status="no_fit",
            snapshot_age_seconds=0.2,
        )
        registry = ResourceRoutingRegistry(
            policy=ResourceRoutingPolicy(startup_admission=True, minimum_headroom_mb=1024),
            targets=(),
        )
        with (
            patch("gamma.supervisor.manager.load_resource_routing_registry", return_value=registry),
            patch("gamma.supervisor.manager.ResourcePlacementCoordinator") as coordinator,
            patch("gamma.supervisor.manager.log_event") as log_event,
        ):
            coordinator.return_value.rank.return_value = decision
            device = manager._admitted_sidecar_device(
                provider="qwen-tts",
                kind="qwen-tts",
                modality="speech",
                model="qwen-tts",
                estimated_vram_mb=9000,
            )

        self.assertIsNone(device)
        self.assertEqual(log_event.call_args.args[2], "resource.startup_admission.rejected")
        self.assertEqual(log_event.call_args.kwargs["estimate_source"], "configured_fallback")
        self.assertEqual(log_event.call_args.kwargs["rejected"], {"qwen-gpu": "insufficient_vram_headroom"})

    def test_sidecar_estimate_uses_fresh_observed_allocation(self) -> None:
        manager = ProcessManager()
        registry = ResourceRoutingRegistry(
            policy=ResourceRoutingPolicy(
                qwen_tts_estimated_vram_mb=9216,
                sidecar_allocation_ttl_seconds=300,
            ),
            targets=(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            log_path = data_dir / "runtime" / "logs" / "supervisor.jsonl"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "event": "resource.sidecar_allocation.observed",
                        "provider": "qwen-tts",
                        "kind": "qwen-tts",
                        "estimated_vram_mb": 9216,
                        "observed_vram_mb": 8900,
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(settings, "data_dir", data_dir),
                patch("gamma.supervisor.manager.load_resource_routing_registry", return_value=registry),
            ):
                estimate = manager._sidecar_vram_estimate("qwen-tts")

        self.assertEqual(estimate.vram_mb, 8900)
        self.assertEqual(estimate.source, "observed_fresh")
        self.assertEqual(estimate.ttl_seconds, 300)

    def test_sidecar_estimate_falls_back_when_observation_is_stale(self) -> None:
        manager = ProcessManager()
        registry = ResourceRoutingRegistry(
            policy=ResourceRoutingPolicy(
                audio_understanding_estimated_vram_mb=1536,
                sidecar_allocation_ttl_seconds=1,
            ),
            targets=(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            log_path = data_dir / "runtime" / "logs" / "supervisor.jsonl"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-06-17T00:00:00+00:00",
                        "event": "resource.sidecar_allocation.observed",
                        "provider": "audio-understanding",
                        "kind": "audio-understanding",
                        "estimated_vram_mb": 1536,
                        "observed_vram_mb": 900,
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(settings, "data_dir", data_dir),
                patch("gamma.supervisor.manager.load_resource_routing_registry", return_value=registry),
            ):
                estimate = manager._sidecar_vram_estimate("audio-understanding")

        self.assertEqual(estimate.vram_mb, 1536)
        self.assertEqual(estimate.source, "configured_fallback")

    def test_sidecar_allocation_payload_matches_gpu_process_by_pid(self) -> None:
        manager = ProcessManager()
        process = SimpleNamespace(pid=1234)
        snapshot = SimpleNamespace(
            sampled_at="2026-06-17T00:00:00Z",
            gpu={
                "ok": True,
                "gpus": [
                    {
                        "index": 0,
                        "uuid": "GPU-0",
                        "processes": [{"pid": 1234, "gpu_uuid": "GPU-0", "used_memory_mb": 8900}],
                    },
                    {
                        "index": 1,
                        "uuid": "GPU-1",
                        "processes": [{"pid": 5678, "gpu_uuid": "GPU-1", "used_memory_mb": 1024}],
                    },
                ],
            },
        )
        with (
            patch("gamma.supervisor.manager.collect_resource_snapshot", return_value=snapshot),
            patch.object(manager, "process_payload", return_value={"running": True, "pid": 1234}),
        ):
            payload = manager._sidecar_allocation_payload(
                provider="qwen-tts",
                kind="qwen-tts",
                process=process,  # type: ignore[arg-type]
                estimated_vram_mb=9216,
            )

        self.assertEqual(payload["pid"], 1234)
        self.assertTrue(payload["process_running"])
        self.assertEqual(payload["estimated_vram_mb"], 9216)
        self.assertEqual(payload["observed_vram_mb"], 8900)
        self.assertEqual(payload["allocation_delta_mb"], -316)
        self.assertEqual(payload["gpu_allocations"], [{"gpu_index": 0, "gpu_uuid": "GPU-0", "used_memory_mb": 8900}])

    def test_sidecar_allocation_logs_observed_event(self) -> None:
        manager = ProcessManager()
        with (
            patch.object(manager, "_sidecar_allocation_payload", return_value={"provider": "qwen-tts", "kind": "qwen-tts"}) as payload,
            patch("gamma.supervisor.manager.log_event") as log_event,
        ):
            result = manager._log_sidecar_allocation(
                provider="qwen-tts",
                kind="qwen-tts",
                process=None,
                estimated_vram_mb=9216,
            )

        payload.assert_called_once()
        self.assertEqual(result, {"provider": "qwen-tts", "kind": "qwen-tts"})
        self.assertEqual(log_event.call_args.args[2], "resource.sidecar_allocation.observed")

    def test_health_url_uses_endpoint_origin(self) -> None:
        self.assertEqual(
            ProcessManager._health_url("http://127.0.0.1:9883/analyze"),
            "http://127.0.0.1:9883/health",
        )
        self.assertIsNone(ProcessManager._health_url("not-a-url"))


if __name__ == "__main__":
    unittest.main()
