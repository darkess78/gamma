from __future__ import annotations

import pytest

from gamma.errors import ContextBudgetError
from gamma.llm.context_budget import ContextBudgetManager, PromptLayer


def test_context_budget_drops_low_priority_layers_before_required_context() -> None:
    manager = ContextBudgetManager()
    layers = [
        PromptLayer("Core Persona", "persona " * 200, priority=1, required=True),
        PromptLayer("Boundaries", "privacy safety " * 100, priority=1, required=True),
        PromptLayer("Current Speaker", "speaker owner", priority=2, required=True),
        PromptLayer("Working", "objective " * 100, priority=4),
        PromptLayer("Memories", "memory " * 500, priority=6),
        PromptLayer("Background", "background " * 1000, priority=9),
    ]

    built = manager.build(layers=layers, user_text="current event", usable_input_tokens=1000)

    assert "persona" in built.system_prompt
    assert "privacy safety" in built.system_prompt
    assert "speaker owner" in built.system_prompt
    assert "background" not in built.system_prompt
    assert built.compaction_actions[0] == "drop:Background"


def test_context_budget_refuses_to_remove_mandatory_layers() -> None:
    manager = ContextBudgetManager()
    layers = [PromptLayer("Core Persona", "persona " * 2000, priority=1, required=True)]

    with pytest.raises(ContextBudgetError):
        manager.build(layers=layers, user_text="current event", usable_input_tokens=512)
