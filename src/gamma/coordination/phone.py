"""A private device transport and contact receipts for the existing Shana owner."""
from copy import deepcopy
import hashlib
import hmac
import json
import math
import re
import threading
import uuid

from .phone_policy import contact_block_reason
from .phone_models import CommitmentEvidence, RehearsalObservation, ReminderConsideration


ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
MEMORY = re.compile(r'^mem-[a-f0-9]{24}$')
SECRET = re.compile(r'^[A-Za-z0-9_-]{43,128}$')
DEFAULT_POLICY = {'enabled': False, 'authority_expires_at': 0., 'quiet_until': 0.,
                  'minimum_interval': 900, 'maximum_per_day': 3}
TERMINAL = {'acknowledged', 'uncertain', 'cancelled', 'expired', 'declined'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def _time(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('invalid_phone_identifier')
    return value


class PhoneAccess:
    """No second conversation service, background thread, push provider or local brain."""

    def __init__(self, core):
        self.core, self.store, self.token, self.clock = core, core.store, core.token, core.clock
        self._consider_lock = threading.Lock()
        with self.store.transaction(self.token) as db:
            if not self.store.get(db, 'phone_policy', 'state'):
                self.store.put(db, 'phone_policy', 'state', dict(DEFAULT_POLICY))
            for device in self.store.list(db, 'phone_device', None):
                device.update(connected=False, expires_at=0)
                self.store.put(db, 'phone_device', device['id'], device)
            for message in self.store.list(db, 'phone_message', None):
                if message['status'] == 'offered':
                    message.update(status='uncertain', reason='owner_restarted_after_offer')
                    self.store.put(db, 'phone_message', message['id'], message)
            self._sync(db)

    def _identity(self, db):
        return self.store.get(db, 'personal_meta', 'state')['identity_id']

    def _device(self, db, device_id, secret, epoch=None, *, connected=True):
        _identifier(device_id)
        if not isinstance(secret, str) or not SECRET.fullmatch(secret):
            raise ValueError('phone_device_authentication_required')
        row = self.store.get(db, 'phone_device', device_id)
        if not row or row.get('revoked') or not hmac.compare_digest(row['secret_sha256'], _digest(secret)):
            raise ValueError('phone_device_authentication_required')
        if connected and (not row['connected'] or self.clock() >= row['expires_at']):
            raise ValueError('phone_device_reconnect_required')
        if epoch is not None and (type(epoch) != int or epoch != row['epoch']):
            raise ValueError('phone_connection_changed')
        return row

    def _public_device(self, row):
        value = {k: row[k] for k in ('id', 'label', 'epoch', 'connected', 'availability',
                                     'registered_at', 'last_seen_at', 'expires_at', 'revoked')}
        value['connected'] = bool(row['connected'] and self.clock() < row['expires_at'] and not row['revoked'])
        return value

    def _valid_commitment(self, db, memory_id, revision=None):
        record = self.store.get(db, 'personal_record', memory_id)
        if (not record or record.get('kind') != 'commitment' or record.get('subject_id') != 'owner'
                or record.get('status') != 'active' or record.get('scope') != 'owner_private'
                or revision is not None and record.get('revision') != revision):
            return None
        source = self.store.get(db, 'personal_source', record['source_id'])
        if not source or source.get('status') not in {'active', 'administrative'}:
            return None
        return record

    def _sync(self, db):
        now = self.clock()
        activity_sha256 = self._activity_signature(db)
        for message in self.store.list(db, 'phone_message', None):
            invalid = not self._valid_commitment(db, message['commitment_id'], message['revision'])
            if invalid:
                message.update(status='cancelled', reason='commitment_changed_or_removed', text='')
                self.store.put(db, 'phone_message', message['id'], message)
            elif now >= message['expires_at'] and message['status'] not in TERMINAL:
                message.update(status='expired', reason='contact_expired', text='')
                self.store.put(db, 'phone_message', message['id'], message)
            elif message['status'] == 'offered' and now >= message.get('offer_deadline', 0):
                message.update(status='uncertain', reason='receipt_not_confirmed')
                self.store.put(db, 'phone_message', message['id'], message)
            elif message['status'] == 'ready' and (now >= message.get('ready_until', 0)
                    or message.get('generation') != self.token[0]
                    or message.get('sequence') != self.store.get(db, 'brain', 'state')['sequence']
                    or message.get('activity_sha256') != activity_sha256):
                message.update(status='cancelled', reason='review_context_changed_before_delivery', text='')
                self.store.put(db, 'phone_message', message['id'], message)

    def register(self, request_id, device_id, secret, label='My phone'):
        _identifier(request_id); _identifier(device_id)
        if not isinstance(secret, str) or not SECRET.fullmatch(secret):
            raise ValueError('invalid_phone_device_secret')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 60:
            raise ValueError('invalid_phone_device_label')
        with self.store.transaction(self.token) as db:
            previous = self.store.get(db, 'phone_device', device_id)
            if previous:
                if previous.get('registration_id') != request_id:
                    raise ValueError('phone_device_already_registered')
                row = self._device(db, device_id, secret, connected=False)
                return {'device': self._public_device(row), 'identity_id': self._identity(db)}
            devices = self.store.list(db, 'phone_device', None)
            if sum(not d.get('revoked') for d in devices) >= 8 or len(devices) >= 256:
                raise ValueError('phone_device_limit')
            now = self.clock()
            row = {'id': device_id, 'secret_sha256': _digest(secret), 'label': label.strip(),
                   'registration_id': request_id, 'registered_at': now, 'last_seen_at': now,
                   'connected': False, 'expires_at': 0, 'epoch': 0, 'availability': 'available', 'revoked': False}
            self.store.put(db, 'phone_device', device_id, row)
            return {'device': self._public_device(row), 'identity_id': self._identity(db)}

    def connect(self, device_id, secret, request_id):
        _identifier(request_id)
        with self.store.transaction(self.token) as db:
            row = self._device(db, device_id, secret, connected=False)
            # A retry of this specific connection does not invalidate its own epoch.
            if row.get('connect_request') != request_id:
                row['epoch'] += 1
                row['connect_request'] = request_id
                for message in self.store.list(db, 'phone_message', None):
                    if message.get('device_id') == device_id and message['status'] == 'offered':
                        message.update(status='uncertain', reason='connection_changed_after_offer')
                        self.store.put(db, 'phone_message', message['id'], message)
            now = self.clock()
            row.update(connected=True, last_seen_at=now, expires_at=now + 90)
            self.store.put(db, 'phone_device', device_id, row)
            return {'device': self._public_device(row), 'identity_id': self._identity(db)}

    def control(self, device_id, secret, epoch, action, value=None):
        with self.store.transaction(self.token) as db:
            row = self._device(db, device_id, secret, epoch)
            if action == 'availability':
                if not isinstance(value, str) or value not in {'available', 'busy', 'quiet'}:
                    raise ValueError('invalid_phone_availability')
                row['availability'] = value
            elif action in {'disconnect', 'revoke'}:
                row.update(connected=False, expires_at=0, epoch=row['epoch'] + 1)
                if action == 'revoke':
                    row.update(revoked=True, secret_sha256='')
                for message in self.store.list(db, 'phone_message', None):
                    if message.get('device_id') == device_id and message['status'] == 'offered':
                        message.update(status='uncertain', reason='device_disconnected_after_offer')
                        self.store.put(db, 'phone_message', message['id'], message)
            elif action == 'contact_policy':
                if not isinstance(value, dict) or set(value) != set(DEFAULT_POLICY):
                    raise ValueError('invalid_phone_contact_policy')
                if (type(value['enabled']) != bool or not _time(value['authority_expires_at'])
                        or value['authority_expires_at'] > self.clock() + 86400
                        or value['enabled'] and value['authority_expires_at'] <= self.clock()):
                    raise ValueError('invalid_phone_contact_authority')
                probe = contact_block_reason({'awake': True, 'focus': 'relaxed'},
                    {'connected': True, 'expires_at': self.clock() + 1, 'availability': 'available'}, value, self.clock())
                if probe == 'invalid_contact_state':
                    raise ValueError('invalid_phone_contact_policy')
                self.store.put(db, 'phone_policy', 'state', dict(value))
            else:
                raise ValueError('unsupported_phone_control')
            self.store.put(db, 'phone_device', device_id, row)
            return {'device': self._public_device(row), 'policy': self.store.get(db, 'phone_policy', 'state')}

    def _state(self, db, row):
        self._sync(db)
        brain = self.store.get(db, 'brain', 'state')
        messages = [m for m in self.store.list(db, 'phone_message', None) if m.get('device_id') == row['id']]
        return {'identity_id': self._identity(db), 'generation': self.token[0],
                'device': self._public_device(row), 'awake': brain['awake'], 'focus': brain['focus'],
                'policy': self.store.get(db, 'phone_policy', 'state'),
                'messages': [{k: v for k, v in m.items() if k != 'text' or m['status'] in {'received', 'acknowledged'}}
                             for m in messages[:40]],
                'commitments': [{'id': r['id'], 'revision': r['revision'], 'text': r['text']}
                                for r in self.store.list(db, 'personal_record', None)
                                if self._valid_commitment(db, r['id'])][:40],
                'notification_scope': 'generic_foreground_browser_only', 'scope': 'owner_private'}

    def state(self, device_id, secret, epoch):
        with self.store.transaction(self.token) as db:
            row = self._device(db, device_id, secret, epoch)
            now = self.clock()
            row.update(last_seen_at=now, expires_at=now + 90)
            self.store.put(db, 'phone_device', device_id, row)
            return self._state(db, row)

    def snapshot(self):
        with self.store.transaction(self.token) as db:
            self._sync(db)
            return {'identity_id': self._identity(db),
                    'devices': [self._public_device(d) for d in self.store.list(db, 'phone_device', None)],
                    'policy': self.store.get(db, 'phone_policy', 'state'),
                    'counts': {state: sum(m['status'] == state for m in self.store.list(db, 'phone_message', None))
                               for state in ('ready', 'offered', 'received', 'acknowledged', 'uncertain')}}

    def owner_devices(self):
        """The enclosing owner session authenticates this administrative view."""
        with self.store.transaction(self.token) as db:
            return {'scope': 'owner_private', 'devices': [
                {k: self._public_device(row)[k] for k in ('id', 'label', 'last_seen_at', 'connected', 'revoked')}
                for row in self.store.list(db, 'phone_device', None)]}

    def owner_revoke(self, request_id, device_id):
        """Owner-authenticated revocation also works after a device key was lost."""
        _identifier(request_id); _identifier(device_id)
        with self.store.transaction(self.token) as db:
            fingerprint = _digest({'action': 'revoke', 'device_id': device_id})
            old = self.store.get(db, 'phone_admin_control', request_id)
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('phone_admin_request_id_reused')
                return old['result']
            row = self.store.get(db, 'phone_device', device_id)
            if not row:
                raise ValueError('unknown_phone_device')
            if len(self.store.list(db, 'phone_admin_control', None)) >= 512:
                raise ValueError('phone_admin_control_capacity')
            if not row['revoked']:
                row.update(connected=False, expires_at=0, revoked=True, secret_sha256='',
                           epoch=row['epoch'] + 1, revoked_at=self.clock())
                self.store.put(db, 'phone_device', device_id, row)
            count = 0
            for message in self.store.list(db, 'phone_message', None):
                if message.get('device_id') != device_id:
                    continue
                message.update(text='', transport_revoked=True)
                if message['status'] == 'offered':
                    message.update(status='uncertain', reason='owner_revoked_unconfirmed_offer')
                elif message['status'] not in TERMINAL:
                    message.update(status='cancelled', reason='owner_revoked_device')
                self.store.put(db, 'phone_message', message['id'], message)
                count += 1
            result = {'id': device_id, 'revoked': True, 'pending_payloads_erased': count,
                      'canonical_history_changed': False}
            self.store.put(db, 'phone_admin_control', request_id, {'fingerprint': fingerprint, 'result': result})
            return result

    def schedule(self, device_id, secret, epoch, commitment_id, due_at, expires_at):
        if not MEMORY.fullmatch(str(commitment_id)) or not _time(due_at) or not _time(expires_at):
            raise ValueError('invalid_phone_schedule')
        now = self.clock()
        if not now <= due_at < expires_at <= now + 604800:
            raise ValueError('phone_schedule_out_of_bounds')
        with self.store.transaction(self.token) as db:
            self._device(db, device_id, secret, epoch)
            record = self._valid_commitment(db, commitment_id)
            if not record:
                raise ValueError('active_owner_commitment_required')
            self.store.put(db, 'phone_schedule', commitment_id, {'id': commitment_id, 'revision': record['revision'],
                           'due_at': due_at, 'expires_at': expires_at})
            return {'commitment_id': commitment_id, 'due_at': due_at, 'expires_at': expires_at,
                    'authority': 'consider_only', 'commitment_resolved': False}

    def _block(self, db, row):
        policy = self.store.get(db, 'phone_policy', 'state')
        contacts = [m['contact_at'] for m in self.store.list(db, 'phone_message', None)
                    if m.get('contact_at') is not None and m['contact_at'] > self.clock() - 86400]
        result = contact_block_reason(self.store.get(db, 'brain', 'state'), row, policy, self.clock(), contacts)
        if result is None and self.clock() >= policy.get('authority_expires_at', 0):
            return 'contact_authority_expired'
        return result

    def _candidates(self, db):
        self._sync(db)
        now = self.clock()
        rows = []
        for record in reversed(self.store.list(db, 'personal_record', None)):
            if not self._valid_commitment(db, record['id']):
                continue
            message_id = 'reminder-' + _digest([record['id'], record['revision']])[:32]
            previous = self.store.get(db, 'phone_message', message_id)
            if previous and (previous.get('contact_at') is not None or previous['status'] == 'ready'):
                continue
            schedule = self.store.get(db, 'phone_schedule', record['id'])
            if schedule and (schedule['revision'] != record['revision'] or schedule.get('status') == 'cancelled'):
                continue
            if schedule and (now < schedule['due_at'] or now >= schedule['expires_at']):
                continue
            rows.append(CommitmentEvidence(id=message_id, commitment_id=record['id'], revision=record['revision'],
                text=record['text'], source_id=record['source_id'], source_kind=record.get('source_kind', 'unknown'),
                certainty=record.get('certainty', 'reported'), record_sha256=_digest(record), created_at=now,
                expires_at=min(now + 86400, schedule['expires_at'] if schedule else now + 86400),
                explicit_schedule=bool(schedule), scheduled_for=schedule['due_at'] if schedule else None).model_dump())
        return rows[:8]

    def _activity_evidence(self, db):
        # Read controller facts, never infer real-world completion from a deadline or a stop.
        now = self.clock()
        return [RehearsalObservation(session_id=row['id'], status=row['status'], reason=row.get('reason', '')[:120],
            revision=row['revision'], owner_present=row['owner_present'], focused=row['focused'],
            generation_matches_current_owner=row['generation'] == self.token[0],
            deadline_passed=now >= row['expires_at'], started_at=row['created_at'],
            expires_at=row['expires_at'], observed_at=now).model_dump()
            for row in self.store.list(db, 'stream_session', 4)]

    def _activity_signature(self, db):
        return _digest([{k: v for k, v in row.items() if k != 'observed_at'}
                        for row in self._activity_evidence(db)])

    def due_candidates(self):
        with self.store.transaction(self.token) as db:
            return deepcopy(self._candidates(db))

    def consider_due(self):
        """A bounded call to the shared owner, followed by source and authority checks."""
        if not self._consider_lock.acquire(blocking=False):
            return {'status': 'busy'}
        try:
            with self.store.transaction(self.token) as db:
                devices = [d for d in self.store.list(db, 'phone_device', None) if self._block(db, d) is None]
                if not devices:
                    return {'status': 'deferred', 'reason': 'no_eligible_private_device'}
                row = devices[0]
                review = self.store.get(db, 'phone_review', 'state') or {'next_at': 0}
                if self.clock() < review['next_at']:
                    return {'status': 'deferred', 'reason': 'review_backoff'}
                candidates = self._candidates(db)
                if not candidates:
                    return {'status': 'idle'}
                candidate = candidates[0]
                policy_hash = _digest(self.store.get(db, 'phone_policy', 'state'))
                device_id, epoch = row['id'], row['epoch']
                sequence = self.store.get(db, 'brain', 'state')['sequence']
                activities = self._activity_evidence(db)
                activity_sha256 = _digest([{k: v for k, v in activity.items() if k != 'observed_at'}
                                           for activity in activities])
                self.store.put(db, 'phone_review', 'state', {'next_at': self.clock() + 300})
            review_channel = getattr(self.core, 'review_channel', None)
            if review_channel is None:
                return {'status': 'deferred', 'reason': 'shared_owner_review_unavailable'}
            request_id = 'phone-review-' + uuid.uuid4().hex
            context = ReminderConsideration(candidate=candidate, observed_activity=activities,
                unknowns=['Consideration time does not establish that a conditional prerequisite happened.',
                          'The commitment belongs to the owner. No task assignment to Shana is established.',
                          'Controller stop, expiry or elapsed time does not establish successful completion of the broad activity.'],
                instruction='Consider this unfinished owner commitment and present availability. Choose silence or a useful concise reminder. '
                            'The source text is reported or authored commitment evidence, not a newly observed event or your assigned task. '
                            'Explicit scheduling grants consideration eligibility only. A conditional prerequisite remains unknown unless '
                            'the supplied exact evidence proves it. Use conditional wording or silence when it is unknown or still active. '
                            'Do not claim the activity is over, the goal was accomplished or the task is overdue without supporting evidence. '
                            'No tools, personal writes, or other actions. A reply may be offered once to this authenticated private phone. '
                            'Acknowledgment ends this exchange.').model_dump()
            response = review_channel(context, request_id)
            value = response.model_dump() if hasattr(response, 'model_dump') else response
            return self.apply_consideration(candidate, value, device_id=device_id, epoch=epoch,
                                             policy_sha256=policy_hash, sequence=sequence,
                                             activity_sha256=activity_sha256)
        finally:
            self._consider_lock.release()

    def apply_consideration(self, candidate, response, *, device_id, epoch, policy_sha256, sequence, activity_sha256):
        # Server-only. Routes never accept a caller-supplied decision or candidate.
        with self.store.transaction(self.token) as db:
            self._sync(db)
            record = self._valid_commitment(db, candidate['commitment_id'], candidate['revision'])
            row = self.store.get(db, 'phone_device', device_id)
            if (not record or _digest(record) != candidate['record_sha256'] or not row or row['epoch'] != epoch
                    or self._block(db, row) is not None or self.clock() >= candidate['expires_at']
                    or self.store.get(db, 'brain', 'state')['sequence'] != sequence
                    or self._activity_signature(db) != activity_sha256
                    or _digest(self.store.get(db, 'phone_policy', 'state')) != policy_sha256):
                return {'status': 'withheld', 'reason': 'source_or_contact_authority_changed'}
            previous = self.store.get(db, 'phone_message', candidate['id'])
            if previous and (previous.get('contact_at') is not None or previous['status'] == 'ready'):
                return {'status': 'duplicate', 'message_id': candidate['id']}
            text = response.get('display_text', '') if isinstance(response, dict) else ''
            action = response.get('response_action') if isinstance(response, dict) else None
            if not isinstance(action, str) or action not in {'reply', 'acknowledge'} or not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
                return {'status': 'deferred', 'reason': 'shared_owner_chose_quiet'}
            message = {k: v for k, v in candidate.items() if k not in {'text', 'record_sha256'}}
            message.update(status='ready', text=text.strip(), device_id=device_id, device_epoch=epoch,
                           generation=self.token[0], sequence=sequence, ready_until=self.clock() + 60,
                           source_sha256=candidate['record_sha256'], activity_sha256=activity_sha256)
            if previous is None and len(self.store.list(db, 'phone_message', None)) >= 256:
                return {'status': 'deferred', 'reason': 'phone_message_capacity'}
            self.store.put(db, 'phone_message', message['id'], message)
            return {'status': 'ready', 'message_id': message['id']}

    def poll(self, device_id, secret, epoch):
        with self.store.transaction(self.token) as db:
            row = self._device(db, device_id, secret, epoch)
            self._sync(db)
            blocked = self._block(db, row)
            if blocked:
                return {'message': None, 'reason': blocked}
            for message in reversed(self.store.list(db, 'phone_message', None)):
                if message.get('device_id') != device_id or message['status'] != 'ready':
                    continue
                # Delivery is an effect. Verify the same live owner and current source at commit.
                message.update(status='offered', device_epoch=epoch, generation=self.token[0],
                               offered_at=self.clock(), contact_at=self.clock(), offer_deadline=self.clock() + 30)
                self.store.put(db, 'phone_message', message['id'], message)
                return {'message': {k: message[k] for k in ('id', 'text', 'commitment_id', 'status', 'generation')},
                        'notification': {'title': 'Shana', 'body': 'You have a private update.', 'content_included': False}}
            return {'message': None, 'reason': 'nothing_new'}

    def receipt(self, device_id, secret, epoch, message_id, status):
        if status not in {'received', 'acknowledged', 'uncertain'}:
            raise ValueError('invalid_phone_receipt')
        with self.store.transaction(self.token) as db:
            self._device(db, device_id, secret, epoch)
            self._sync(db)
            message = self.store.get(db, 'phone_message', message_id)
            if not message or message.get('device_id') != device_id or message.get('device_epoch') != epoch:
                raise ValueError('phone_message_not_available')
            if message['status'] == status:
                return {'status': status, 'commitment_resolved': False}
            if message['status'] in TERMINAL:
                return {'status': message['status'], 'commitment_resolved': False}
            allowed = {'offered': {'received', 'uncertain'}, 'received': {'acknowledged', 'uncertain'}}
            if status not in allowed.get(message['status'], set()):
                raise ValueError('phone_receipt_out_of_order')
            message.update(status=status, **{status + '_at': self.clock()})
            self.store.put(db, 'phone_message', message_id, message)
            return {'status': status, 'commitment_resolved': False}

    def send(self, device_id, secret, epoch, request_id, text, session_id='monitor-private'):
        _identifier(request_id); _identifier(session_id)
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError('invalid_phone_text')
        with self.store.transaction(self.token) as db:
            self._device(db, device_id, secret, epoch)
            fingerprint = _digest({'text': text, 'session_id': session_id})
            old = self.store.get(db, 'phone_request', request_id)
            if old and (old['fingerprint'] != fingerprint or old['device_id'] != device_id):
                raise ValueError('phone_request_id_reused')
            if not old:
                if len(self.store.list(db, 'phone_request', None)) >= 512:
                    raise ValueError('phone_request_capacity')
                self.store.put(db, 'phone_request', request_id, {'id': request_id, 'device_id': device_id,
                               'fingerprint': fingerprint, 'session_id': session_id, 'created_at': self.clock()})
        # Core owns deduplication, personal writes, queueing, recovery and final checked text.
        self.core.input(text, session_id, request_id)
        return self.result(device_id, secret, epoch, request_id)

    def result(self, device_id, secret, epoch, request_id):
        _identifier(request_id)
        with self.store.transaction(self.token) as db:
            self._device(db, device_id, secret, epoch)
            mapping = self.store.get(db, 'phone_request', request_id)
            if not mapping or mapping['device_id'] != device_id:
                raise ValueError('unknown_phone_request')
            turn = self.store.get(db, 'turn', request_id)
            if not turn:
                return {'request_id': request_id, 'status': 'uncertain', 'automatic_replay': False}
            value = {'request_id': request_id, 'status': turn['status'], 'generation': self.token[0],
                     'automatic_replay': False, 'identity_id': self._identity(db)}
            if turn.get('status') == 'completed' and not turn.get('redacted'):
                value['response'] = deepcopy(turn.get('response') or {})
            return value

    def conversation(self, device_id, secret, epoch, session_id='monitor-private'):
        _identifier(session_id)
        with self.store.transaction(self.token) as db:
            self._device(db, device_id, secret, epoch)
            turns = [t for t in self.store.list(db, 'turn', None) if t.get('session_id') == session_id
                     and t.get('status') == 'completed' and not t.get('redacted')][:30]
            return {'identity_id': self._identity(db), 'session_id': session_id,
                    'turns': [{'id': t['id'], 'user': t['text'], 'assistant': t.get('response', {}).get('display_text', '')}
                              for t in reversed(turns)]}


def invalidate_snapshot(db):
    """Disable copied operational authority without erasing canonical experience."""
    from .store import Store
    devices = Store.list(db, 'phone_device', None)
    messages = Store.list(db, 'phone_message', None)
    for row in devices:
        row.update(connected=False, expires_at=0, revoked=True, secret_sha256='', epoch=row['epoch'] + 1)
        Store.put(db, 'phone_device', row['id'], row)
    policy = Store.get(db, 'phone_policy', 'state')
    if policy:
        policy.update(enabled=False, authority_expires_at=0)
        Store.put(db, 'phone_policy', 'state', policy)
    for row in messages:
        row['text'] = ''
        if row['status'] not in TERMINAL:
            row.update(status='cancelled', reason='copied_state_requires_fresh_device_pairing')
        Store.put(db, 'phone_message', row['id'], row)
    return {'devices_invalidated': len(devices), 'message_payloads_removed': len(messages),
            'request_fingerprints_retained': len(Store.list(db, 'phone_request', None)),
            'commitments_changed': False}


def redact_sources(db, memory_ids=(), source_ids=()):
    """Erase derived payloads inside the caller's canonical source-removal transaction."""
    from .store import Store
    memories, sources = set(memory_ids), set(source_ids)
    count = 0
    for row in Store.list(db, 'phone_message', None):
        record = Store.get(db, 'personal_record', row['commitment_id'])
        if row['commitment_id'] in memories or record and record.get('source_id') in sources:
            row.update(text='', status='cancelled', reason='source_removed')
            Store.put(db, 'phone_message', row['id'], row)
            count += 1
    for row in Store.list(db, 'phone_schedule', None):
        record = Store.get(db, 'personal_record', row['id'])
        if row['id'] in memories or record and record.get('source_id') in sources:
            row.update(status='cancelled', reason='source_removed')
            Store.put(db, 'phone_schedule', row['id'], row)
    return {'phone_payloads_redacted': count}
