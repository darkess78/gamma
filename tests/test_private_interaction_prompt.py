"""Private address and delivery policy must survive optional history compaction."""
import pytest

from gamma.coordination.interaction_routes import interaction_instruction
from gamma.errors import ContextBudgetError
from gamma.llm.capabilities import estimate_text_tokens
from gamma.llm.context_budget import ContextBudgetManager, prompt_layers_from_system_prompt


def system_prompt(history):
    return ('# Core Persona\nOne continuing Shana.\n\n'
            '# Boundaries\nOwner-private only.\n\n'
            '# Private Coordination Context\nCurrent owner context.\n\n'
            '# Recent Conversation Turns\n' + history + interaction_instruction()
            + '\n\n# Live Voice Formatting\nUse the complete typed turn contract.')


def test_private_interaction_policy_survives_optional_history_compaction():
    user = 'Who am I?'
    essential = system_prompt('[]')
    system = system_prompt('fictional old exchange ' * 2500)
    budget = estimate_text_tokens(essential, user) + 64
    built = ContextBudgetManager().build(layers=prompt_layers_from_system_prompt(system),
                                        user_text=user, usable_input_tokens=budget)
    assert built.compaction_actions
    assert interaction_instruction().strip() in built.system_prompt
    assert 'Private interaction capability' in built.included_layers
    assert built.user_text == user
    assert '# Boundaries\nOwner-private only.' in built.system_prompt


def test_private_interaction_policy_cannot_be_dropped_to_make_budget_fit():
    system = system_prompt('fictional history ' * 1000)
    with pytest.raises(ContextBudgetError, match='Mandatory prompt layers'):
        ContextBudgetManager().build(layers=prompt_layers_from_system_prompt(system),
                                     user_text='Who am I?', usable_input_tokens=512)


def test_uncompacted_interaction_prompt_keeps_original_text_and_order():
    system = system_prompt('[]')
    built = ContextBudgetManager().build(layers=prompt_layers_from_system_prompt(system),
                                        user_text='A question.', usable_input_tokens=8192)
    assert not built.compaction_actions
    assert built.system_prompt == system


def test_legacy_prompt_without_interaction_policy_has_no_new_layer():
    system = '# Core Persona\nShana.\n\n# Recent Conversation Turns\nPrior exchange.'
    built = ContextBudgetManager().build(layers=prompt_layers_from_system_prompt(system),
                                        user_text='Hello', usable_input_tokens=8192)
    assert built.system_prompt == system
    assert built.included_layers == ('Core Persona', 'Recent Conversation Turns')
