from __future__ import annotations

import tomllib
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gamma.resources.models import RuntimeTarget, WorkloadSpec
from gamma.resources.policy import rank_placement_candidates
from gamma.resources.coordinator import ResourcePlacementCoordinator
from gamma.resources.runtime_registry import ResourceRoutingPolicy, ResourceRoutingRegistry, load_resource_routing_registry


class ResourcePolicyTest(unittest.TestCase):
    def test_policy_ranks_gpu_targets_by_projected_headroom_and_warm_bonus(self) -> None:
        snapshot = _snapshot(
            [
                {"index": 0, "uuid": "GPU-0", "memory_free_mb": 8000, "utilization_percent": 10},
                {"index": 1, "uuid": "GPU-1", "memory_free_mb": 7000, "utilization_percent": 0},
            ]
        )
        targets = (
            RuntimeTarget(id="cold", kind="ollama", provider="local", device="cuda:0", models=("gpt-oss:20b",)),
            RuntimeTarget(id="warm", kind="ollama", provider="local", device="cuda:1", models=("gpt-oss:20b",), warm_models=("gpt-oss:20b",)),
        )
        workload = WorkloadSpec(id="llm:chat", kind="llm", provider="local", model="gpt-oss:20b", estimated_vram_mb=1000, minimum_headroom_mb=2048)

        decision = rank_placement_candidates(
            snapshot=snapshot,
            workload=workload,
            targets=targets,
            policy=ResourceRoutingPolicy(shadow_mode=True),
        )

        self.assertEqual(decision.status, "selected")
        self.assertEqual(decision.selected.target.id, "warm")  # type: ignore[union-attr]
        self.assertTrue(decision.selected.warm)  # type: ignore[union-attr]

    def test_policy_rejects_unhealthy_model_mismatch_and_insufficient_headroom(self) -> None:
        snapshot = _snapshot([{"index": 0, "uuid": "GPU-0", "memory_free_mb": 2500, "utilization_percent": 0}])
        targets = (
            RuntimeTarget(id="missing-models", kind="ollama", provider="local", device="cuda:0"),
            RuntimeTarget(id="bad-model", kind="ollama", provider="local", device="cuda:0", models=("other",)),
            RuntimeTarget(id="unhealthy", kind="ollama", provider="local", device="cuda:0", models=("model",), healthy=False),
            RuntimeTarget(id="small", kind="ollama", provider="local", device="cuda:0", models=("model",)),
        )
        workload = WorkloadSpec(id="llm:chat", kind="llm", provider="local", model="model", estimated_vram_mb=1000, minimum_headroom_mb=2048)

        decision = rank_placement_candidates(
            snapshot=snapshot,
            workload=workload,
            targets=targets,
            policy=ResourceRoutingPolicy(shadow_mode=True),
        )

        self.assertEqual(decision.status, "no_fit")
        self.assertEqual(decision.rejected["missing-models"], "models_missing")
        self.assertEqual(decision.rejected["bad-model"], "model_unavailable")
        self.assertEqual(decision.rejected["unhealthy"], "target_unhealthy")
        self.assertEqual(decision.rejected["small"], "insufficient_vram_headroom")

    def test_policy_rejects_stale_snapshot_before_scoring(self) -> None:
        snapshot = _snapshot([], sampled_at="2020-01-01T00:00:00Z")
        target = RuntimeTarget(id="local", kind="ollama", provider="local", device="cuda:0")

        decision = rank_placement_candidates(
            snapshot=snapshot,
            workload=WorkloadSpec(id="llm:chat", kind="llm", provider="local"),
            targets=(target,),
            policy=ResourceRoutingPolicy(shadow_mode=True, snapshot_max_age_seconds=1),
        )

        self.assertEqual(decision.status, "snapshot_stale")
        self.assertEqual(decision.rejected["local"], "snapshot_stale")

    def test_registry_parses_shadow_policy_and_targets(self) -> None:
        registry = load_resource_routing_registry(
            {
                "resource_routing": {
                    "policy": {"shadow_mode": True, "minimum_headroom_mb": 4096, "reservation_ttl_seconds": 15},
                    "endpoints": {"local_ollama_gpu_0": "http://127.0.0.1:11434"},
                    "targets": [
                        {
                            "id": "ollama-gpu0",
                            "kind": "ollama",
                            "endpoint_ref": "local_ollama_gpu_0",
                            "device": "cuda:0",
                            "models": ["gpt-oss:20b"],
                        }
                    ],
                }
            }
        )

        self.assertTrue(registry.policy.shadow_mode)
        self.assertEqual(registry.policy.minimum_headroom_mb, 4096)
        self.assertEqual(registry.policy.reservation_ttl_seconds, 15)
        self.assertEqual(registry.targets[0].provider, "local")
        self.assertEqual(registry.endpoint_for_target(registry.targets[0]).url, "http://127.0.0.1:11434")  # type: ignore[union-attr]
        self.assertEqual(registry.targets[0].models, ("gpt-oss:20b",))
        self.assertEqual(registry.validation_errors, ())

    def test_registry_reports_and_omits_malformed_targets(self) -> None:
        registry = load_resource_routing_registry(
            {
                "resource_routing": {
                    "endpoints": {
                        "valid_endpoint": "http://127.0.0.1:11434",
                        "bad_endpoint": "not-a-url",
                    },
                    "targets": [
                        {"id": "", "kind": "ollama", "device": "cuda:0", "modalities": ["text"]},
                        {
                            "id": "valid",
                            "kind": "ollama",
                            "endpoint_ref": "valid_endpoint",
                            "device": "cuda:0",
                            "modalities": ["text"],
                            "models": ["model"],
                        },
                        {"id": "valid", "kind": "ollama", "device": "cuda:1", "modalities": ["text"], "models": ["model"]},
                        {"id": "bad-device", "kind": "ollama", "device": "gpu:0", "modalities": ["text"], "models": ["model"]},
                        {"id": "bad-modality", "kind": "ollama", "device": "cpu", "modalities": ["images"], "models": ["model"]},
                        {"id": "bad-endpoint-ref", "kind": "ollama", "endpoint_ref": "missing_endpoint", "device": "cpu", "modalities": ["text"], "models": ["model"]},
                        "not-an-object",
                    ],
                }
            }
        )

        self.assertEqual([target.id for target in registry.targets], ["valid"])
        self.assertEqual([endpoint.id for endpoint in registry.endpoints], ["valid_endpoint"])
        self.assertTrue(any(".url is unsupported: not-a-url" in error for error in registry.validation_errors))
        self.assertTrue(any(".id is required" in error for error in registry.validation_errors))
        self.assertTrue(any(".id duplicates valid" in error for error in registry.validation_errors))
        self.assertTrue(any(".device is unsupported: gpu:0" in error for error in registry.validation_errors))
        self.assertTrue(any(".endpoint_ref is unknown: missing_endpoint" in error for error in registry.validation_errors))
        self.assertTrue(any(".modalities contains unsupported values: images" in error for error in registry.validation_errors))
        self.assertTrue(any("must be an object" in error for error in registry.validation_errors))

    def test_app_example_includes_disabled_portable_resource_target_examples(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config" / "app.example.toml"
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
        registry = load_resource_routing_registry(payload)

        targets = {target.id: target for target in registry.targets}
        endpoints = {endpoint.id: endpoint for endpoint in registry.endpoints}
        self.assertFalse(registry.policy.shadow_mode)
        self.assertFalse(registry.policy.active_llm_routing)
        self.assertFalse(registry.policy.startup_admission)
        self.assertEqual(registry.validation_errors, ())
        for endpoint_id in ("local_ollama_gpu_0", "local_ollama_gpu_1", "local_ollama_cpu", "qwen_tts_local", "audio_understanding_local"):
            self.assertIn(endpoint_id, endpoints)
        for target_id in ("ollama_gpu_0", "ollama_gpu_1", "cpu_llm_sidecar", "qwen_tts_cpu", "audio_understanding_cpu"):
            self.assertIn(target_id, targets)
            self.assertFalse(targets[target_id].enabled)
            self.assertFalse(targets[target_id].managed)

        self.assertEqual(targets["ollama_gpu_0"].device, "cuda:0")
        self.assertEqual(targets["ollama_gpu_1"].endpoint_ref, "local_ollama_gpu_1")
        self.assertEqual(registry.endpoint_for_target(targets["ollama_gpu_1"]).url, "http://127.0.0.1:11435")  # type: ignore[union-attr]
        self.assertEqual(targets["cpu_llm_sidecar"].device, "cpu")
        self.assertEqual(registry.endpoint_for_target(targets["qwen_tts_cpu"]).url, "http://127.0.0.1:9882")  # type: ignore[union-attr]
        self.assertEqual(targets["qwen_tts_cpu"].modalities, ("speech",))
        self.assertEqual(targets["audio_understanding_cpu"].provider, "audio-understanding")
        self.assertEqual(payload["qwen_tts_device"], "")

    def test_registry_accepts_local_startup_admission_sidecar_targets(self) -> None:
        registry = load_resource_routing_registry(
            {
                "resource_routing": {
                    "policy": {
                        "shadow_mode": True,
                        "active_llm_routing": False,
                        "startup_admission": False,
                        "minimum_headroom_mb": 1024,
                    },
                    "endpoints": {
                        "qwen_tts_local": "http://127.0.0.1:9882",
                        "audio_understanding_local": "http://127.0.0.1:9883",
                    },
                    "targets": [
                        {
                            "id": "qwen_tts_gpu_0",
                            "kind": "qwen-tts",
                            "provider": "qwen-tts",
                            "endpoint_ref": "qwen_tts_local",
                            "device": "cuda:0",
                            "models": ["qwen-tts"],
                            "modalities": ["speech"],
                            "enabled": True,
                            "managed": False,
                        },
                        {
                            "id": "audio_understanding_gpu_1",
                            "kind": "audio-understanding",
                            "provider": "audio-understanding",
                            "endpoint_ref": "audio_understanding_local",
                            "device": "cuda:1",
                            "models": ["superb/wav2vec2-base-superb-er", "MIT/ast-finetuned-audioset-10-10-0.4593"],
                            "modalities": ["audio"],
                            "enabled": True,
                            "managed": False,
                        },
                    ],
                }
            }
        )

        self.assertFalse(registry.policy.startup_admission)
        self.assertEqual(registry.validation_errors, ())
        targets = {target.id: target for target in registry.targets}
        self.assertEqual(targets["qwen_tts_gpu_0"].provider, "qwen-tts")
        self.assertEqual(targets["qwen_tts_gpu_0"].modalities, ("speech",))
        self.assertEqual(targets["audio_understanding_gpu_1"].provider, "audio-understanding")
        self.assertEqual(targets["audio_understanding_gpu_1"].modalities, ("audio",))

    def test_shadow_advisory_reservations_influence_later_rankings_only_until_release(self) -> None:
        ResourcePlacementCoordinator.clear_advisory_reservations()
        registry = ResourceRoutingRegistry(
            policy=ResourceRoutingPolicy(shadow_mode=True, minimum_headroom_mb=1000, reservation_ttl_seconds=30),
            targets=(
                RuntimeTarget(id="gpu-a", kind="ollama", provider="local", device="cuda:0", models=("model",)),
                RuntimeTarget(id="gpu-b", kind="ollama", provider="local", device="cuda:1", models=("model",)),
            ),
        )
        coordinator = ResourcePlacementCoordinator(
            registry=registry,
            monitor=_FakeMonitor(
                _snapshot(
                    [
                        {"index": 0, "uuid": "GPU-0", "memory_free_mb": 8000, "utilization_percent": 0},
                        {"index": 1, "uuid": "GPU-1", "memory_free_mb": 8000, "utilization_percent": 0},
                    ]
                )
            ),
        )
        workload = WorkloadSpec(id="llm:chat", kind="llm", provider="local", model="model", estimated_vram_mb=4000)

        first = coordinator.rank_and_reserve(workload)
        second = coordinator.rank(workload)
        ResourcePlacementCoordinator.release_advisory_reservation(first.reservation_id)
        third = coordinator.rank(workload)
        ResourcePlacementCoordinator.clear_advisory_reservations()

        self.assertEqual(first.status, "selected")
        self.assertEqual(first.selected.target.id, "gpu-a")  # type: ignore[union-attr]
        self.assertTrue(first.reservation_id)
        self.assertEqual(second.selected.target.id, "gpu-b")  # type: ignore[union-attr]
        self.assertEqual(second.rejected["gpu-a"], "insufficient_vram_headroom")
        self.assertEqual(third.selected.target.id, "gpu-a")  # type: ignore[union-attr]


def _snapshot(gpus, *, sampled_at: str | None = None):
    return {
        "sampled_at": sampled_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "gpu": {"ok": True, "gpus": gpus},
    }


class _FakeMonitor:
    def __init__(self, payload):
        self._payload = payload

    def dashboard_payload(self):
        return self._payload


if __name__ == "__main__":
    unittest.main()
