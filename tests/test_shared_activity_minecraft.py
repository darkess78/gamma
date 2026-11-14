"""Real protocol coordinator with declared fake transport, never real play proof."""
import asyncio
from datetime import datetime, timezone

import pytest

from gamma.coding.durable_practice import PracticeRunner
from gamma.coordination.core import Core
from gamma.coordination.shared_activity import SharedActivities, invalidate_snapshot
from gamma.coordination.shared_activity_minecraft import SharedMinecraft
from gamma.coordination.store import Store
from gamma.schemas.response import AssistantResponse
from gamma.integrations.minecraft.protocol import TerminalOutcome
from test_minecraft_coordinator import (FakeClock, FakeTransport, MinecraftCoordinator,
                                         START, ack_message, handshake, terminal_message)


@pytest.fixture
def setup(tmp_path):
    clock = FakeClock()
    now = lambda: clock().timestamp()
    store = Store(tmp_path)
    core = Core(store, clock=now, draft=lambda *_: AssistantResponse(display_text='Here.'),
                runner=PracticeRunner(store, propose=lambda *_: {}, check=lambda *_: {}))
    service = SharedActivities(core, clock=now)
    service.control('select', 'select', {'source_id': 'test_world', 'label': 'Disposable development world',
                                      'kind': 'minecraft_state'})
    transport = FakeTransport()
    coordinator = MinecraftCoordinator(enabled=True, transport=transport, clock=clock)
    game = SharedMinecraft(service, coordinator, private_test_world=True, clock=now)
    try:
        yield core, service, game, coordinator, transport, clock
    finally:
        core.close()


def test_explicit_private_world_attestation_and_finite_grant(setup):
    core, service, game, coordinator, _, clock = setup
    unavailable = SharedMinecraft(service, coordinator, clock=lambda: clock().timestamp())
    with pytest.raises(ValueError, match='unavailable'):
        unavailable.grant('no_attestation')
    first = game.grant('grant', seconds=30, maximum_commands=1)
    assert first['permit']['remaining_commands'] == 1
    clock.advance(seconds=5)
    assert game.grant('grant', seconds=30, maximum_commands=1)['status'] == 'not_replayed'
    assert game.status()['permit']['expires_at'] == START.timestamp() + 30
    with pytest.raises(ValueError, match='reused'):
        game.grant('grant', seconds=40)


def test_canonical_command_real_ack_and_terminal_are_distinct(setup):
    core, _, game, coordinator, transport, _ = setup
    async def run():
        await handshake(coordinator)
        game.grant('grant', maximum_commands=1)
        row = await game.execute('follow', 'follow_owner', {'follow_distance': 3, 'lease_duration_seconds': 5})
        assert row['status'] == 'sent' and row['terminal'] is None
        count = len(transport.sent)
        assert await game.execute('follow', 'follow_owner', {'follow_distance': 3, 'lease_duration_seconds': 5}) == row
        assert len(transport.sent) == count
        await coordinator.receive(ack_message(coordinator))
        assert game.status()['commands'][0]['status'] == 'sent'
        message = terminal_message(coordinator, TerminalOutcome.COMPLETED)
        receipt = await coordinator.receive(message)
        assert game.status()['commands'][0]['evidence_kind'] == 'coordinator_terminal'
        assert game.record_received_terminal(message, receipt) is True
        observed = game.status()['commands'][0]
        assert observed['status'] == 'observed_terminal'
        assert observed['terminal']['outcome'] == 'completed'
        assert observed['evidence_kind'] == 'received_sidecar_terminal'
        assert observed['result_message_sha256']
        with pytest.raises(ValueError, match='unavailable'):
            await game.execute('again', 'come_here')
    asyncio.run(run())


@pytest.mark.parametrize('name,args', [('follow_owner', {}), ('follow_owner', {'lease_duration_seconds': 21}),
                                      ('look_at_owner', {'duration_seconds': 4}), ('come_here', {'x': 5}),
                                      ('attack', {}), ('emergency_stop', {'code': 'no'})])
def test_no_unbounded_or_unknown_controls(setup, name, args):
    _, _, game, _, _, _ = setup
    game.grant('grant')
    with pytest.raises(ValueError):
        asyncio.run(game.execute('bad', name, args))
    assert game.status()['commands'] == []


def test_sleep_or_source_change_prevents_new_movement(setup):
    core, service, game, _, _, _ = setup
    game.grant('grant')
    core.control('sleep', 'sleep')
    with pytest.raises(ValueError, match='unavailable'):
        asyncio.run(game.execute('sleep_move', 'come_here'))
    core.control('wake', 'wake')
    service.control('select_again', 'select', {'source_id': 'osrs', 'label': 'RuneLite', 'kind': 'osrs_screen'})
    with pytest.raises(ValueError, match='unavailable'):
        asyncio.run(game.execute('osrs_move', 'come_here'))


def test_emergency_revocation_and_snapshot_never_restore_actions(setup):
    core, _, game, coordinator, _, _ = setup
    async def run():
        await handshake(coordinator)
        game.grant('grant')
        await game.execute('follow', 'follow_owner', {'follow_distance': 3, 'lease_duration_seconds': 5})
        result = await game.revoke()
        assert result['status'] == 'revoked' and coordinator.status().emergency_stop_active
    asyncio.run(run())
    with core.store.transaction(core.token) as db:
        invalidate_snapshot(db)
    assert core.store.read('shared_game_meta', 'state')['permit'] is None
    assert core.store.read('shared_game_command', 'follow')['status'] == 'uncertain'
    assert core.store.read('shared_game_grant', 'grant')['fingerprint']


def test_fast_received_terminal_during_send_is_not_lost_or_overwritten(setup):
    _, _, game, coordinator, transport, _ = setup
    received = []
    original_send = transport.send
    async def send(message):
        await original_send(message)
        if message.type == 'command' and message.payload.name == 'follow_owner':
            await coordinator.receive(ack_message(coordinator))
            terminal = terminal_message(coordinator, TerminalOutcome.COMPLETED)
            receipt = await coordinator.receive(terminal)
            received.append(game.record_received_terminal(terminal, receipt))
    async def run():
        await handshake(coordinator)
        transport.send = send
        game.grant('grant')
        return await game.execute('fast', 'follow_owner', {'follow_distance': 3, 'lease_duration_seconds': 5})
    result = asyncio.run(run())
    assert received == [True]
    assert result['status'] == 'observed_terminal'
    assert result['evidence_kind'] == 'received_sidecar_terminal'
    assert game.status()['commands'][0]['status'] == 'observed_terminal'


def test_game_journal_capacity_preserves_hashes_without_disabling_stop(setup):
    core, _, game, coordinator, _, _ = setup
    with core.store.transaction(core.token) as db:
        for index in range(256):
            core.store.put(db, 'shared_game_command', 'retained_' + str(index),
                           {'id': 'retained_' + str(index), 'fingerprint': 'preserved', 'status': 'uncertain'})
    with pytest.raises(ValueError, match='capacity'):
        asyncio.run(game.execute('new_move', 'come_here'))
    result = asyncio.run(game.execute('full_stop', 'stop'))
    assert result['status'] == 'safety_stop' and coordinator.status().emergency_stop_active
    assert core.store.read('shared_game_command', 'retained_0')['fingerprint'] == 'preserved'


def test_game_authority_journal_does_not_evict_old_grants(setup):
    core, _, game, _, _, _ = setup
    with core.store.transaction(core.token) as db:
        for index in range(256):
            core.store.put(db, 'shared_game_grant', 'retained_' + str(index),
                           {'id': 'retained_' + str(index), 'fingerprint': 'preserved'})
    with pytest.raises(ValueError, match='capacity'):
        game.grant('new_grant')
    assert core.store.read('shared_game_grant', 'retained_0')['fingerprint'] == 'preserved'


def test_ordinary_stop_cancels_active_command_without_emergency_latch(setup):
    _, _, game, coordinator, transport, _ = setup
    async def run():
        await handshake(coordinator)
        game.grant('grant')
        await game.execute('follow', 'follow_owner', {'follow_distance': 3, 'lease_duration_seconds': 5})
        return await game.execute('ordinary_stop', 'stop')
    result = asyncio.run(run())
    assert result['status'] == 'cancel_requested'
    assert transport.sent[-1].type == 'cancel_command'
    assert not coordinator.status().emergency_stop_active


@pytest.mark.parametrize('change', ['sleep', 'source', 'expiry'])
def test_active_movement_stops_when_its_authority_is_lost(setup, change):
    core, service, game, coordinator, _, clock = setup
    async def run():
        await handshake(coordinator)
        game.grant('grant', seconds=10)
        await game.execute('follow', 'follow_owner', {'follow_distance': 3, 'lease_duration_seconds': 5})
        if change == 'sleep':
            core.control('sleep', 'sleep')
        elif change == 'source':
            service.control('stop', 'stop', {})
        else:
            clock.advance(seconds=11)
        return await game.ensure_authority()
    result = asyncio.run(run())
    assert result['status'] == 'stopped' and coordinator.status().emergency_stop_active
    assert game.status()['permit'] is None
