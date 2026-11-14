"""Frozen skill bookkeeping with declared protocol fixtures, not live Minecraft proof."""
import asyncio
from copy import deepcopy
import hashlib

import pytest

from gamma.integrations.minecraft.protocol import TerminalOutcome
from test_minecraft_coordinator import ack_message, handshake, terminal_message
from test_shared_activity_minecraft import setup


def spec():
    rows = []
    for partition, families in [('baseline', ['clear', 'tight']), ('practice', ['training_a', 'training_b']),
                                 ('held_out', ['clear', 'tight'])]:
        for index, family in enumerate(families):
            name = partition + '_' + str(index)
            rows.append({'id': name, 'partition': partition, 'family': family,
                         'variant_sha256': hashlib.sha256(name.encode()).hexdigest()})
    return {'baseline_tactic': 2, 'practice_tactics': [2, 4], 'scenarios': rows}


def test_manifest_freezes_partitions_and_blocks_variant_leakage(setup):
    _, _, game, _, _, _ = setup
    original = spec()
    prepared = game.skills.prepare('trial', original)
    assert prepared['complete'] is False and prepared['weight_training'] is False
    assert game.skills.prepare('trial', original)['trial']['spec_sha256'] == prepared['trial']['spec_sha256']
    for change in ('duplicate', 'family', 'partition'):
        value = deepcopy(original)
        if change == 'duplicate':
            value['scenarios'][-1]['variant_sha256'] = value['scenarios'][0]['variant_sha256']
        elif change == 'family':
            value['scenarios'][-1]['family'] = 'unmatched'
        else:
            value['scenarios'][0]['partition'] = 'held_out'
        with pytest.raises(ValueError):
            game.skills.prepare('invalid_' + change, value)
    altered = deepcopy(original); altered['baseline_tactic'] = 3
    with pytest.raises(ValueError, match='reused'):
        game.skills.prepare('trial', altered)


def test_client_cannot_skip_environment_probe_or_training_partition(setup):
    _, _, game, _, _, _ = setup
    trial = game.skills.prepare('trial', spec())['trial']
    with pytest.raises(ValueError, match='environment_not_observed'):
        asyncio.run(game.skills.run(trial['id'], 'baseline_0', 'action'))
    with pytest.raises(ValueError, match='prior_partition_incomplete'):
        asyncio.run(game.skills.run(trial['id'], 'held_out_0', 'heldout'))
    assert game.skills.inspect(trial['id'])['attempts'] == []


def test_only_received_outcomes_train_then_heldout_tactic_stays_frozen(setup):
    _, _, game, coordinator, _, clock = setup
    specification = spec()
    trial = game.skills.prepare('trial', specification)['trial']
    async def run():
        await handshake(coordinator)
        game.grant('grant', maximum_commands=6)
        for index, scenario in enumerate(specification['scenarios']):
            game.skills.attest_scenario(trial['id'], scenario['id'], variant_sha256=scenario['variant_sha256'],
                                       proof_sha256=hashlib.sha256(('fixture_probe_' + scenario['id']).encode()).hexdigest(),
                                       observed_at=clock().timestamp())
            row = await game.skills.run(trial['id'], scenario['id'], 'action_' + str(index))
            assert row['status'] == 'awaiting_received_result'
            assert coordinator.status().active_command.command_name.value == 'come_here'
            ack = ack_message(coordinator).model_copy(update={'sequence': 2 + index * 2, 'message_id': 'ack_' + str(index)})
            await coordinator.receive(ack)
            # Fixture outcomes exercise learning bookkeeping, not actual world consequences.
            completed = scenario['id'] == 'baseline_0' or row['tactic'] == 4
            message = terminal_message(coordinator, TerminalOutcome.COMPLETED if completed else TerminalOutcome.FAILED)
            message = message.model_copy(update={'sequence': 3 + index * 2, 'message_id': 'terminal_' + str(index)})
            receipt = await coordinator.receive(message)
            before = game.skills.inspect(trial['id'])
            assert before['scores'][scenario['partition']]['observed'] == sum(
                item['partition'] == scenario['partition'] for item in specification['scenarios'][:index])
            assert game.record_received_terminal(message, receipt)
            current = game.skills.inspect(trial['id'])
            if scenario['partition'] == 'held_out':
                assert current['trial']['learned_tactic'] == row['tactic'] == 4
        return game.skills.inspect(trial['id'])
    result = asyncio.run(run())
    assert result['complete'] and result['observed_bounded_improvement']
    assert result['scores']['baseline']['completed'] == 1
    assert result['scores']['held_out']['completed'] == 2
    assert result['general_game_learning_proven'] is False and result['weight_training'] is False
    assert len(result['trial']['learning_evidence']) == 2


def test_spacing_tactics_obey_actual_come_here_argument_bounds(setup):
    _, _, game, _, _, _ = setup
    invalid = spec()
    invalid['practice_tactics'] = [2, 5]
    with pytest.raises(ValueError):
        game.skills.prepare('outside_real_command_bounds', invalid)


def test_unknown_or_local_only_outcome_is_not_improvement(setup):
    _, _, game, coordinator, _, clock = setup
    trial = game.skills.prepare('trial', spec())['trial']
    scenario = trial['spec']['scenarios'][0]
    game.skills.attest_scenario(trial['id'], scenario['id'], variant_sha256=scenario['variant_sha256'],
                               proof_sha256='f' * 64, observed_at=clock().timestamp())
    async def run():
        await handshake(coordinator)
        game.grant('grant')
        await game.skills.run(trial['id'], scenario['id'], 'action')
        clock.advance(seconds=30)
        coordinator.check_deadlines()
    asyncio.run(run())
    result = game.skills.inspect(trial['id'])
    assert result['scores']['baseline']['observed'] == 0
    assert result['complete'] is False and result['observed_bounded_improvement'] is False


def test_environment_probe_is_fresh_exact_and_invalidated_by_snapshot(setup):
    core, _, game, _, _, clock = setup
    trial = game.skills.prepare('trial', spec())['trial']
    scenario = trial['spec']['scenarios'][0]
    with pytest.raises(ValueError, match='variant'):
        game.skills.attest_scenario(trial['id'], scenario['id'], variant_sha256='0' * 64,
                                   proof_sha256='f' * 64, observed_at=clock().timestamp())
    game.skills.attest_scenario(trial['id'], scenario['id'], variant_sha256=scenario['variant_sha256'],
                               proof_sha256='f' * 64, observed_at=clock().timestamp())
    from gamma.coordination.shared_activity import invalidate_snapshot
    with core.store.transaction(core.token) as db:
        invalidate_snapshot(db)
    with pytest.raises(ValueError, match='environment_not_observed'):
        asyncio.run(game.skills.run(trial['id'], scenario['id'], 'action'))
