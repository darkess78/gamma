"""Frozen bounded Minecraft arrival trials score received outcomes, never self-report."""
from collections import Counter
from copy import deepcopy
import re

from .personal import digest, identifier
from .shared_activity_policy import bounded_identifier, finite_number, fresh_timestamp


PHASES = ('baseline', 'practice', 'held_out')


class ArrivalSpacingTrials:
    """One small outcome-trained action parameter, with held-out variants frozen first.

    Environment attestations enter only through a backend probe callback. A trial
    cannot execute from a browser-supplied claim that the desired world is ready.
    The probe and real-world consequences still need separate runtime evidence.
    """

    def __init__(self, game):
        self.game, self.core, self.store, self.clock = game, game.core, game.store, game.clock

    def prepare(self, request_id, spec):
        bounded_identifier(request_id)
        if not isinstance(spec, dict) or set(spec) != {'baseline_tactic', 'practice_tactics', 'scenarios'}:
            raise ValueError('invalid_follow_trial_spec')
        baseline = finite_number(spec['baseline_tactic'], minimum=2, maximum=4)
        tactics, scenarios = spec['practice_tactics'], spec['scenarios']
        if not isinstance(tactics, list) or not 2 <= len(tactics) <= 4:
            raise ValueError('invalid_follow_trial_tactics')
        tactics = [finite_number(value, minimum=2, maximum=4) for value in tactics]
        if len(set(tactics)) != len(tactics):
            raise ValueError('duplicate_follow_trial_tactics')
        if not isinstance(scenarios, list) or not 6 <= len(scenarios) <= 12:
            raise ValueError('invalid_follow_trial_scenarios')
        frozen, names, hashes = [], set(), set()
        for source in scenarios:
            if not isinstance(source, dict) or set(source) != {'id', 'partition', 'family', 'variant_sha256'}:
                raise ValueError('invalid_follow_trial_scenario')
            row = dict(source)
            bounded_identifier(row['id'])
            bounded_identifier(row['family'])
            if not isinstance(row['partition'], str) or row['partition'] not in PHASES:
                raise ValueError('invalid_follow_trial_partition')
            if not isinstance(row['variant_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', row['variant_sha256']):
                raise ValueError('invalid_follow_trial_variant')
            if row['id'] in names or row['variant_sha256'] in hashes:
                raise ValueError('follow_trial_variant_leakage')
            names.add(row['id']); hashes.add(row['variant_sha256'])
            frozen.append(row)
        counts = Counter(row['partition'] for row in frozen)
        if any(counts[phase] < 2 for phase in PHASES) or counts['practice'] != len(tactics):
            raise ValueError('follow_trial_requires_finite_comparable_cohorts')
        families = {phase: Counter(row['family'] for row in frozen if row['partition'] == phase) for phase in PHASES}
        if families['baseline'] != families['held_out']:
            raise ValueError('follow_trial_baseline_heldout_families_differ')
        normalized = {'baseline_tactic': baseline, 'practice_tactics': tactics, 'scenarios': frozen}
        fingerprint = digest(normalized)
        key = identifier('skill', 'arrival_spacing', request_id)
        with self.store.transaction(self.core.token) as db:
            previous = self.store.get(db, 'shared_skill_trial', key)
            if previous:
                if previous['spec_sha256'] != fingerprint:
                    raise ValueError('follow_trial_request_reused')
            else:
                if len(self.store.list(db, 'shared_skill_trial', None)) >= 24:
                    raise ValueError('follow_trial_capacity')
                self.store.put(db, 'shared_skill_trial', key, {'id': key, 'spec': normalized,
                               'spec_sha256': fingerprint, 'created_at': self.clock(), 'learned_tactic': None,
                               'learning_evidence': [], 'weight_training': False,
                               'mechanic': 'bounded_arrival_spacing', 'command_name': 'come_here'})
        return self.inspect(key)

    def attest_scenario(self, trial_id, scenario_id, *, variant_sha256, proof_sha256, observed_at):
        """Backend-only independent environment-probe callback, never an HTTP success field."""
        fresh_timestamp(observed_at, now=self.clock())
        if not isinstance(proof_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', proof_sha256):
            raise ValueError('invalid_environment_probe_hash')
        with self.store.transaction(self.core.token) as db:
            trial, scenario = self._scenario(db, trial_id, scenario_id)
            if variant_sha256 != scenario['variant_sha256']:
                raise ValueError('environment_variant_mismatch')
            key = trial_id + ':' + scenario_id
            self.store.put(db, 'shared_skill_probe', key, {'id': key, 'trial_id': trial_id,
                           'scenario_id': scenario_id, 'variant_sha256': variant_sha256,
                           'proof_sha256': proof_sha256, 'observed_at': observed_at,
                           'generation': self.core.token[0], 'evidence_kind': 'backend_environment_probe'})

    def _scenario(self, db, trial_id, scenario_id):
        trial = self.store.get(db, 'shared_skill_trial', trial_id)
        if not trial:
            raise ValueError('unknown_follow_trial')
        if trial.get('mechanic') != 'bounded_arrival_spacing' or trial.get('command_name') != 'come_here':
            raise ValueError('trial_contract_version_unavailable')
        scenario = next((row for row in trial['spec']['scenarios'] if row['id'] == scenario_id), None)
        if scenario is None:
            raise ValueError('unknown_follow_trial_scenario')
        return trial, scenario

    def _attempts(self, db, trial_id):
        return [row for row in self.store.list(db, 'shared_skill_attempt', None) if row['trial_id'] == trial_id]

    def _reconcile(self, db, trial_id):
        for row in self._attempts(db, trial_id):
            if row['status'] not in {'awaiting_received_result', 'unconfirmed'}:
                continue
            command = self.store.get(db, 'shared_game_command', row['request_id'])
            if (command and command.get('evidence_kind') == 'received_sidecar_terminal'
                    and command['status'] == 'observed_terminal' and command.get('result_message_sha256')
                    and command.get('authorized_completion', False)
                    and command['command_id'] == row['command_id'] and command['name'] == 'come_here'):
                row.update(status='observed', outcome=command['terminal']['outcome'],
                           result_message_sha256=command['result_message_sha256'],
                           command_result_sha256=digest(command), observed_at=command['observed_at'])
                self.store.put(db, 'shared_skill_attempt', row['id'], row)
            elif command and command['status'] in {'uncertain', 'adapter_result'}:
                row.update(status='unconfirmed', reason='no_received_environment_outcome')
                self.store.put(db, 'shared_skill_attempt', row['id'], row)

    async def run(self, trial_id, scenario_id, request_id):
        bounded_identifier(request_id)
        self.game.status()
        with self.store.transaction(self.core.token) as db:
            trial, scenario = self._scenario(db, trial_id, scenario_id)
            self._reconcile(db, trial_id)
            key = trial_id + ':' + scenario_id
            previous = self.store.get(db, 'shared_skill_attempt', key)
            if previous:
                if previous['request_id'] != request_id:
                    raise ValueError('follow_scenario_already_attempted')
                return deepcopy(previous)
            attempts = self._attempts(db, trial_id)
            phase_index = PHASES.index(scenario['partition'])
            for phase in PHASES[:phase_index]:
                expected = {row['id'] for row in trial['spec']['scenarios'] if row['partition'] == phase}
                observed = {row['scenario_id'] for row in attempts if row['partition'] == phase and row['status'] == 'observed'}
                if observed != expected:
                    raise ValueError('follow_trial_prior_partition_incomplete')
            if any(row['status'] != 'observed' for row in attempts):
                raise ValueError('follow_trial_unresolved_effect')
            probe = self.store.get(db, 'shared_skill_probe', key)
            if not probe or probe['generation'] != self.core.token[0]:
                raise ValueError('follow_trial_environment_not_observed')
            fresh_timestamp(probe['observed_at'], now=self.clock())
            if scenario['partition'] == 'baseline':
                tactic = trial['spec']['baseline_tactic']
            elif scenario['partition'] == 'practice':
                scenarios = [row['id'] for row in trial['spec']['scenarios'] if row['partition'] == 'practice']
                tactic = trial['spec']['practice_tactics'][scenarios.index(scenario_id)]
            else:
                if trial['learned_tactic'] is None:
                    practice = [row for row in attempts if row['partition'] == 'practice']
                    # Outcome-trained mechanics only. Held-out outcomes are never fed back.
                    scores = {tactic: sum(row['outcome'] == 'completed' for row in practice if row['tactic'] == tactic)
                              / sum(row['tactic'] == tactic for row in practice) for tactic in trial['spec']['practice_tactics']}
                    best = max(scores.values())
                    tactic = next(value for value in trial['spec']['practice_tactics'] if scores[value] == best)
                    trial.update(learned_tactic=tactic,
                                 learning_evidence=[row['result_message_sha256'] for row in practice])
                    self.store.put(db, 'shared_skill_trial', trial_id, trial)
                tactic = trial['learned_tactic']
            row = {'id': key, 'trial_id': trial_id, 'scenario_id': scenario_id, 'partition': scenario['partition'],
                   'request_id': request_id, 'command_id': 'shared-' + request_id, 'tactic': tactic,
                   'spec_sha256': trial['spec_sha256'], 'probe_sha256': digest(probe),
                   'status': 'awaiting_received_result', 'created_at': self.clock()}
            self.store.put(db, 'shared_skill_attempt', key, row)
        try:
            command = await self.game.execute(request_id, 'come_here', {'arrival_distance': tactic})
            if command['status'] not in {'sent', 'observed_terminal'}:
                with self.store.transaction(self.core.token) as db:
                    row.update(status='unconfirmed', reason='game_dispatch_unconfirmed')
                    self.store.put(db, 'shared_skill_attempt', key, row)
            return deepcopy(row)
        except BaseException:
            from .store import OwnershipLost
            try:
                with self.store.transaction(self.core.token) as db:
                    row.update(status='unconfirmed', reason='game_dispatch_unconfirmed')
                    self.store.put(db, 'shared_skill_attempt', key, row)
            except OwnershipLost:
                pass
            raise

    def inspect(self, trial_id):
        self.game.status()
        with self.store.transaction(self.core.token) as db:
            trial = self.store.get(db, 'shared_skill_trial', trial_id)
            if not trial:
                raise ValueError('unknown_follow_trial')
            self._reconcile(db, trial_id)
            attempts = self._attempts(db, trial_id)
            scores = {}
            for phase in PHASES:
                phase_rows = [row for row in attempts if row['partition'] == phase and row['status'] == 'observed']
                count = sum(row['partition'] == phase for row in trial['spec']['scenarios'])
                scores[phase] = {'observed': len(phase_rows), 'expected': count,
                                 'completed': sum(row['outcome'] == 'completed' for row in phase_rows)}
            complete = all(row['observed'] == row['expected'] for row in scores.values())
            baseline, heldout = scores['baseline'], scores['held_out']
            improved = complete and heldout['completed'] / heldout['expected'] > baseline['completed'] / baseline['expected']
            return {'trial': deepcopy(trial), 'attempts': attempts, 'scores': scores,
                    'complete': complete, 'observed_bounded_improvement': improved,
                    'general_game_learning_proven': False, 'weight_training': False,
                    'scope': 'One bounded arrival-spacing parameter; independent world checks and real play acceptance remain separate.'}


def invalidate_snapshot(db):
    from .store import Store
    for row in Store.list(db, 'shared_skill_probe', None):
        row['generation'] = -1
        Store.put(db, 'shared_skill_probe', row['id'], row)
    for row in Store.list(db, 'shared_skill_attempt', None):
        if row['status'] == 'awaiting_received_result':
            row.update(status='unconfirmed', reason='snapshot_no_replay')
            Store.put(db, 'shared_skill_attempt', row['id'], row)
