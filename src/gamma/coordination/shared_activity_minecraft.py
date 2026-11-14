"""Finite private-world commands reuse the existing Minecraft protocol owner."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
import time

from .personal import digest
from .shared_activity_policy import bounded_identifier
from .store import OwnershipLost, Store
from ..integrations.minecraft.protocol import (
    ComeHereCommandPayload, FollowOwnerCommandPayload, JoinCommandPayload,
    LeaveCommandPayload, LookAtOwnerCommandPayload, ReportStatusCommandPayload,
    StopCommandPayload, TerminalResultMessage, WaitHereCommandPayload,
)
from ..integrations.minecraft.coordinator import ReceiveDisposition


PAYLOADS = {'join': JoinCommandPayload, 'leave': LeaveCommandPayload,
            'follow_owner': FollowOwnerCommandPayload, 'wait_here': WaitHereCommandPayload,
            'come_here': ComeHereCommandPayload, 'look_at_owner': LookAtOwnerCommandPayload,
            'report_status': ReportStatusCommandPayload, 'stop': StopCommandPayload}


def _json(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return value


class SharedMinecraft:
    """No endpoint/account selection, connection startup or autonomous retry here.

    The backend must explicitly attest that the injected existing coordinator is
    connected only to an authorized private disposable development world. A model,
    game message, uploaded screenshot or browser field cannot set that attestation.
    """

    def __init__(self, activity, coordinator, *, private_test_world=False, clock=time.time):
        self.activity, self.core, self.store = activity, activity.core, activity.store
        self.coordinator, self.clock = coordinator, clock
        self.private_test_world = private_test_world is True
        self._lock = asyncio.Lock()
        with self.store.transaction(self.core.token) as db:
            self.store.put(db, 'shared_game_meta', 'state', {'permit': None, 'reason': 'owner_restart'})
            for row in self.store.list(db, 'shared_game_command', None):
                if row['status'] in {'dispatching', 'sent'}:
                    row.update(status='uncertain', reason='owner_restart_no_replay')
                    self.store.put(db, 'shared_game_command', row['id'], row)
        from .shared_activity_skill import ArrivalSpacingTrials
        self.skills = ArrivalSpacingTrials(self)

    def grant(self, request_id, *, seconds=60, maximum_commands=6):
        bounded_identifier(request_id)
        if type(seconds) is not int or not 10 <= seconds <= 120:
            raise ValueError('invalid_game_authority_duration')
        if type(maximum_commands) is not int or not 1 <= maximum_commands <= 12:
            raise ValueError('invalid_game_command_budget')
        fingerprint = digest({'seconds': seconds, 'maximum_commands': maximum_commands})
        with self.store.transaction(self.core.token) as db:
            old = self.store.get(db, 'shared_game_grant', request_id)
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('game_grant_id_reused')
                return {'ok': True, 'status': 'not_replayed'}
            if len(self.store.list(db, 'shared_game_grant', None)) >= 256:
                raise ValueError('game_authority_capacity_requires_maintenance')
            selected = self.activity._meta(db)
            brain = self.store.get(db, 'brain', 'state')
            if not self.private_test_world or not selected['enabled'] or selected['kind'] != 'minecraft_state' or not brain['awake']:
                raise ValueError('private_game_authority_unavailable')
            permit = {'id': request_id, 'generation': self.core.token[0], 'source_epoch': selected['source_epoch'],
                      'expires_at': min(self.clock() + seconds, selected['expires_at']),
                      'remaining_commands': maximum_commands}
            self.store.put(db, 'shared_game_meta', 'state', {'permit': permit, 'reason': 'owner_authorized'})
            self.store.put(db, 'shared_game_grant', request_id, {'id': request_id, 'fingerprint': fingerprint})
            return {'ok': True, 'status': 'authorized', 'permit': permit}

    def _reconcile(self, db):
        status = self.coordinator.status()
        terminal = status.last_terminal
        if terminal:
            for row in self.store.list(db, 'shared_game_command', None):
                if row.get('command_id') == terminal.command_id and row['status'] == 'sent':
                    row.update(status='coordinator_terminal', terminal=_json(asdict(terminal)),
                               observed_at=self.clock(), evidence_kind='coordinator_terminal')
                    self.store.put(db, 'shared_game_command', row['id'], row)
        return status

    def record_received_terminal(self, message, receipt):
        """Backend-only callback after an authenticated protocol receive.

        A coordinator can synthesize failure on local timeout/disconnect. Its
        status is insufficient to score observed-world practice. This callback
        distinguishes a validated sidecar terminal from those local decisions.
        It is deliberately unavailable as an HTTP request accepting success.
        """
        if not isinstance(message, TerminalResultMessage) or receipt.disposition != ReceiveDisposition.ACCEPTED:
            return False
        terminal = receipt.terminal
        if (terminal is None or terminal.command_id != message.command_id
                or terminal.trace_id != message.trace_id
                or terminal.command_name != message.payload.command_name
                or terminal.outcome != message.payload.outcome):
            raise ValueError('game_terminal_receipt_mismatch')
        with self.store.transaction(self.core.token) as db:
            row = next((item for item in self.store.list(db, 'shared_game_command', None)
                        if item['command_id'] == message.command_id), None)
            if row is None:
                return False
            if row['generation'] != self.core.token[0]:
                return False
            if row['status'] not in {'dispatching', 'sent', 'coordinator_terminal'}:
                return False
            row.update(status='observed_terminal', terminal=_json(asdict(terminal)), observed_at=self.clock(),
                       evidence_kind='received_sidecar_terminal',
                       result_message_sha256=digest(message.model_dump(mode='json')))
            self.store.put(db, 'shared_game_command', row['id'], row)
            return True

    def status(self):
        self.coordinator.check_liveness()
        with self.store.transaction(self.core.token) as db:
            observed = self._reconcile(db)
            meta = self.store.get(db, 'shared_game_meta', 'state')
            rows = self.store.list(db, 'shared_game_command', 24)
            return {'scope': 'owner_private', 'private_test_world': self.private_test_world,
                    'permit': meta['permit'], 'coordinator': _json(asdict(observed)), 'commands': rows,
                    'learning_status': 'Terminal outcomes alone do not establish learned mechanics or held-out improvement.'}

    def _live(self, db, permit):
        selected = self.activity._meta(db)
        current = self.store.get(db, 'shared_game_meta', 'state')['permit']
        brain = self.store.get(db, 'brain', 'state')
        return (self.private_test_world and current == permit and permit is not None and brain['awake']
                and selected['enabled'] and selected['kind'] == 'minecraft_state'
                and selected['source_epoch'] == permit['source_epoch']
                and permit['generation'] == self.core.token[0] and self.clock() < permit['expires_at'])

    async def execute(self, request_id, name, arguments=None):
        bounded_identifier(request_id)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict) or not isinstance(name, str) or name not in {*PAYLOADS, 'emergency_stop'}:
            raise ValueError('invalid_private_game_action')
        safe_stop = name in {'stop', 'emergency_stop', 'leave'}
        fingerprint = digest({'name': name, 'arguments': arguments})
        async with self._lock:
            with self.store.transaction(self.core.token) as db:
                capacity = (not self.store.get(db, 'shared_game_command', request_id)
                            and len(self.store.list(db, 'shared_game_command', None)) >= 256)
            if capacity:
                if not safe_stop:
                    raise ValueError('game_command_capacity_requires_maintenance')
                if not self.private_test_world:
                    raise ValueError('private_game_authority_unavailable')
                result = await self.coordinator.activate_emergency_stop(reason='Safety stop at journal capacity.')
                return {'ok': True, 'status': 'safety_stop', 'reason': 'game_journal_capacity',
                        'adapter_result': _json(asdict(result))}
            with self.store.transaction(self.core.token) as db:
                self._reconcile(db)
                previous = self.store.get(db, 'shared_game_command', request_id)
                if previous:
                    if previous['fingerprint'] != fingerprint:
                        raise ValueError('game_action_id_reused')
                    return previous
                permit = self.store.get(db, 'shared_game_meta', 'state')['permit']
                if not self.private_test_world or not safe_stop and (not self._live(db, permit) or permit['remaining_commands'] <= 0):
                    raise ValueError('private_game_authority_unavailable')
                if any(row['status'] in {'dispatching', 'sent'} for row in self.store.list(db, 'shared_game_command', None)) and not safe_stop:
                    return {'ok': True, 'status': 'busy', 'reason': 'game_action_pending'}
                deadline = self.clock() + 20
                if not safe_stop:
                    deadline = min(deadline, permit['expires_at'])
                if name == 'emergency_stop':
                    if arguments:
                        raise ValueError('emergency_stop_takes_no_arguments')
                    payload = None
                else:
                    payload = PAYLOADS[name](name=name, deadline_at=datetime.fromtimestamp(deadline, timezone.utc), arguments=arguments)
                    if name == 'follow_owner' and payload.arguments.lease_duration_seconds > 20:
                        raise ValueError('private_game_follow_lease_too_long')
                    if name == 'look_at_owner' and payload.arguments.duration_seconds > 3:
                        raise ValueError('private_game_look_too_long')
                if not safe_stop:
                    permit['remaining_commands'] -= 1
                    self.store.put(db, 'shared_game_meta', 'state', {'permit': permit, 'reason': 'owner_authorized'})
                row = {'id': request_id, 'ok': True, 'name': name, 'fingerprint': fingerprint,
                       'generation': self.core.token[0], 'source_epoch': permit['source_epoch'] if permit else None,
                       'command_id': 'shared-' + request_id, 'status': 'dispatching',
                       'created_at': self.clock(), 'deadline': deadline, 'terminal': None}
                self.store.put(db, 'shared_game_command', request_id, row)
            try:
                cancel_requested = False
                if name == 'emergency_stop':
                    result = await self.coordinator.activate_emergency_stop(reason='Owner private-test stop.')
                elif name == 'stop' and self.coordinator.status().active_command is not None:
                    active = self.coordinator.status().active_command
                    result = await self.coordinator.cancel_active_command(active.command_id, reason='Owner private-test stop.')
                    cancel_requested = True
                else:
                    result = await self.coordinator.submit_command(payload, command_id=row['command_id'], trace_id='trace-' + request_id)
                evidence = _json(asdict(result))
                with self.store.transaction(self.core.token) as db:
                    current = safe_stop or self._live(db, permit)
                    saved = self.store.get(db, 'shared_game_command', request_id)
                    if saved.get('evidence_kind') == 'received_sidecar_terminal' and saved['status'] == 'observed_terminal':
                        row = saved
                    elif cancel_requested:
                        row['status'] = 'cancel_requested'
                    else:
                        row['status'] = 'sent' if evidence.get('disposition') == 'sent' else 'adapter_result'
                    row.update(adapter_result=evidence, authorized_completion=current,
                               reason='submitted' if current else 'authority_changed_in_flight')
                    if not current and row['status'] != 'observed_terminal':
                        row['status'] = 'uncertain'
                    self.store.put(db, 'shared_game_command', request_id, row)
                if not current:
                    await self.coordinator.activate_emergency_stop(reason='Private-test authority changed.')
                return row
            except BaseException:
                try:
                    await self.coordinator.activate_emergency_stop(reason='Private-test command outcome uncertain.')
                except Exception:
                    pass
                try:
                    with self.store.transaction(self.core.token) as db:
                        row.update(status='uncertain', reason='dispatch_effect_unconfirmed')
                        self.store.put(db, 'shared_game_command', request_id, row)
                except OwnershipLost:
                    pass
                raise

    async def revoke(self):
        with self.store.transaction(self.core.token) as db:
            self.store.put(db, 'shared_game_meta', 'state', {'permit': None, 'reason': 'owner_revoked'})
        result = await self.coordinator.activate_emergency_stop(reason='Private-test permission revoked.')
        return {'ok': True, 'status': 'revoked', 'stop': _json(asdict(result))}

    async def ensure_authority(self):
        """Use on the app loop to stop issued movement after sleep/source/lease loss."""
        with self.store.transaction(self.core.token) as db:
            permit = self.store.get(db, 'shared_game_meta', 'state')['permit']
            live = self._live(db, permit)
            if live or permit is None and self.coordinator.status().active_command is None:
                return {'ok': True, 'status': 'unchanged'}
            self.store.put(db, 'shared_game_meta', 'state', {'permit': None, 'reason': 'authority_expired'})
        if self.coordinator.status().active_command is not None:
            result = await self.coordinator.activate_emergency_stop(reason='Private-test source or authority expired.')
            return {'ok': True, 'status': 'stopped', 'stop': _json(asdict(result))}
        return {'ok': True, 'status': 'expired'}


def invalidate_snapshot(db):
    if Store.get(db, 'shared_game_meta', 'state'):
        Store.put(db, 'shared_game_meta', 'state', {'permit': None, 'reason': 'snapshot_no_replay'})
    for row in Store.list(db, 'shared_game_command', None):
        if row['status'] in {'dispatching', 'sent'}:
            row.update(status='uncertain', reason='snapshot_no_replay')
            Store.put(db, 'shared_game_command', row['id'], row)
