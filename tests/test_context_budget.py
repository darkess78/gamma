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


def test_structured_contract_survives_history_compaction():
    from gamma.llm.context_budget import prompt_layers_from_system_prompt
    from gamma.conversation.turn_contract import structured_turn_instruction

    contract = structured_turn_instruction(default_delivery="text_only")
    prompt = "# Core Persona\nYou are Shana.\n\n# Recent Conversation Turns\n" + "old turn " * 2000 + contract
    layers = prompt_layers_from_system_prompt(prompt)
    assert next(layer for layer in layers if layer.name == "Turn Decision Contract").required
    built = ContextBudgetManager().build(layers=layers, user_text="hello", usable_input_tokens=1500)
    assert contract.strip() in built.system_prompt
    assert any(action.endswith("Recent Conversation Turns") for action in built.compaction_actions)


def test_stream_speaker_is_required_separately_from_chat():
    from gamma.llm.context_budget import prompt_layers_from_system_prompt
    layers = prompt_layers_from_system_prompt(
        "# Recent Stream Chat\n" + "chat " * 3000 + "\n\n# Speaking Now\nviewer42 (as shown in chat)")
    built = ContextBudgetManager().build(layers=layers, user_text="hello", usable_input_tokens=512)
    assert "viewer42" in built.system_prompt
    assert "drop:Recent Stream Chat" in built.compaction_actions


def test_viewer_notes_do_not_become_mandatory_speaker_context():
    from gamma.llm.context_budget import prompt_layers_from_system_prompt
    layers = prompt_layers_from_system_prompt(
        "# Speaking Now\nviewer42\n\n# What viewer42 Has Told You Before\n" + "old claim " * 2000)
    built = ContextBudgetManager().build(layers=layers, user_text="hello", usable_input_tokens=512)
    assert "viewer42" in built.system_prompt
    assert "drop:Viewer Self Claims" in built.compaction_actions
