from __future__ import annotations

import unittest
from datetime import datetime, timezone

from gamma.resources.models import RuntimeTarget, WorkloadSpec
from gamma.resources.policy import rank_placement_candidates
from gamma.resources.runtime_registry import ResourceRoutingPolicy, load_resource_routing_registry


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
                    "policy": {"shadow_mode": True, "minimum_headroom_mb": 4096},
                    "targets": [
                        {
                            "id": "ollama-gpu0",
                            "kind": "ollama",
                            "device": "cuda:0",
                            "models": ["gpt-oss:20b"],
                        }
                    ],
                }
            }
        )

        self.assertTrue(registry.policy.shadow_mode)
        self.assertEqual(registry.policy.minimum_headroom_mb, 4096)
        self.assertEqual(registry.targets[0].provider, "local")
        self.assertEqual(registry.targets[0].models, ("gpt-oss:20b",))


def _snapshot(gpus, *, sampled_at: str | None = None):
    return {
        "sampled_at": sampled_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "gpu": {"ok": True, "gpus": gpus},
    }


if __name__ == "__main__":
    unittest.main()
