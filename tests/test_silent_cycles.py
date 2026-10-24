from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import Mock

import pytest

from gamma.brain.silent import SilentCycles


def test_disabled_budget_cooldown_and_restart():
    clock = [0]
    commit = Mock()
    cycles = SilentCycles(lambda: commit, lambda: True, clock=lambda: clock[0])
    cycles.tick()
    commit.assert_not_called()
    cycles.enable(budget=2, cooldown_seconds=5)
    assert cycles.tick()["completed"] == 1
    assert cycles.tick()["last_reason"] == "cooldown"
    clock[0] = 5
    state = cycles.tick()
    assert state["completed"] == 2 and not state["enabled"]
    cycles.tick()
    assert commit.call_count == 2
    assert not SilentCycles(lambda: commit, lambda: True).status()["enabled"]


def test_disable_during_inference_retains_capacity_and_discards():
    entered, release = threading.Event(), threading.Event()
    commit = Mock()
    def prepare():
        entered.set()
        assert release.wait(5)
        return commit
    cycles = SilentCycles(prepare, lambda: True)
    cycles.enable(budget=1, cooldown_seconds=5)
    with ThreadPoolExecutor(1) as executor:
        job = executor.submit(cycles.tick)
        assert entered.wait(5)
        try:
            assert cycles.disable()["active"]
            with pytest.raises(RuntimeError):
                cycles.enable(budget=1, cooldown_seconds=5)
        finally:
            release.set()
        assert job.result(5)["last_reason"] == "discarded"
    commit.assert_not_called()
    commit.discard.assert_called_once()


def test_policy_and_failures_consume_no_unbounded_budget():
    permitted = [False]
    prepare = Mock(side_effect=RuntimeError("private provider failure"))
    cycles = SilentCycles(prepare, lambda: permitted[0])
    cycles.enable(budget=1, cooldown_seconds=5)
    assert cycles.tick()["remaining"] == 1
    prepare.assert_not_called()
    permitted[0] = True
    with pytest.raises(RuntimeError):
        cycles.tick()
    state = cycles.status()
    assert not state["enabled"] and not state["active"] and state["remaining"] == 0
    assert state["last_reason"] == "cycle_failed"


@pytest.mark.parametrize("budget,cooldown", [(True, 5), (0, 5), (9, 5), (1, 4), (1, 3601)])
def test_invalid_budgets_rejected(budget, cooldown):
    with pytest.raises(ValueError):
        SilentCycles(Mock(), lambda: True).enable(budget=budget, cooldown_seconds=cooldown)


def test_late_provider_result_discards_without_commit():
    clock = [0]
    commit = Mock()
    def prepare():
        clock[0] = 61
        return commit
    cycles = SilentCycles(prepare, lambda: True, clock=lambda: clock[0])
    cycles.enable(budget=1, cooldown_seconds=5)
    assert cycles.tick()["last_reason"] == "deadline_exceeded"
    commit.assert_not_called()
    commit.discard.assert_called_once()
