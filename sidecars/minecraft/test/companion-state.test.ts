import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CompanionStateMachine,
  CompanionStateTransitionError,
  type CompanionOperationalState,
  type CompanionStateTransitionOptions
} from '../src/companion-state.js';

const OPERATIONAL_STATES = [
  'DISCONNECTED',
  'IDLE',
  'FOLLOWING',
  'WAITING',
  'RETURNING',
  'DEAD',
  'STOPPED'
] as const satisfies readonly CompanionOperationalState[];

const ALLOWED_TRANSITIONS: Readonly<
  Record<CompanionOperationalState, readonly CompanionOperationalState[]>
> = Object.freeze({
  DISCONNECTED: ['DISCONNECTED', 'IDLE', 'STOPPED'],
  IDLE: [
    'IDLE',
    'FOLLOWING',
    'WAITING',
    'RETURNING',
    'DEAD',
    'STOPPED',
    'DISCONNECTED'
  ],
  FOLLOWING: [
    'FOLLOWING',
    'IDLE',
    'WAITING',
    'DEAD',
    'STOPPED',
    'DISCONNECTED'
  ],
  WAITING: [
    'WAITING',
    'IDLE',
    'FOLLOWING',
    'RETURNING',
    'DEAD',
    'STOPPED',
    'DISCONNECTED'
  ],
  RETURNING: [
    'RETURNING',
    'IDLE',
    'WAITING',
    'DEAD',
    'STOPPED',
    'DISCONNECTED'
  ],
  DEAD: ['DEAD', 'IDLE', 'STOPPED', 'DISCONNECTED'],
  STOPPED: ['STOPPED', 'DISCONNECTED', 'IDLE']
});

function recoveryOptions(
  current: CompanionOperationalState,
  next: CompanionOperationalState
): CompanionStateTransitionOptions {
  if (current === 'DEAD' && next === 'IDLE') {
    return { respawnRecovery: true };
  }
  if (current === 'STOPPED' && next === 'IDLE') {
    return { emergencyRecovery: true };
  }
  return {};
}

test('every allowed companion state transition succeeds', () => {
  assert.equal(new CompanionStateMachine().current(), 'DISCONNECTED');

  for (const current of OPERATIONAL_STATES) {
    for (const next of ALLOWED_TRANSITIONS[current]) {
      const state = new CompanionStateMachine(current);
      assert.equal(
        state.transition(next, recoveryOptions(current, next)),
        next,
        `${current} -> ${next}`
      );
      assert.equal(state.current(), next, `${current} -> ${next}`);
    }
  }
});

test('every invalid companion state transition is rejected without mutation', () => {
  for (const current of OPERATIONAL_STATES) {
    const allowed = new Set(ALLOWED_TRANSITIONS[current]);
    for (const next of OPERATIONAL_STATES) {
      if (allowed.has(next)) continue;
      const state = new CompanionStateMachine(current);
      assert.throws(
        () => state.transition(next),
        CompanionStateTransitionError,
        `${current} -> ${next}`
      );
      assert.equal(state.current(), current, `${current} -> ${next}`);

      const synchronized = new CompanionStateMachine(current);
      assert.throws(
        () => synchronized.synchronize(next),
        CompanionStateTransitionError,
        `synchronize ${current} -> ${next}`
      );
      assert.equal(
        synchronized.current(),
        current,
        `synchronize ${current} -> ${next}`
      );
    }
  }

  assert.equal(
    (OPERATIONAL_STATES as readonly string[]).includes('FLEEING'),
    false
  );
});

test('death returns to idle only through explicit respawn recovery', () => {
  const state = new CompanionStateMachine('FOLLOWING');
  assert.equal(state.transition('DEAD'), 'DEAD');
  assert.throws(() => state.transition('IDLE'), CompanionStateTransitionError);
  assert.throws(() => state.synchronize('IDLE'), CompanionStateTransitionError);
  assert.throws(
    () => state.transition('IDLE', { emergencyRecovery: true }),
    CompanionStateTransitionError
  );
  assert.equal(state.current(), 'DEAD');
  assert.equal(state.synchronize('IDLE', { respawnRecovery: true }), 'IDLE');
});

test('STOPPED remains latched until explicit emergency recovery', () => {
  const state = new CompanionStateMachine('IDLE');
  assert.equal(state.transition('STOPPED'), 'STOPPED');
  assert.equal(state.transition('STOPPED'), 'STOPPED');
  assert.equal(state.synchronize('STOPPED'), 'STOPPED');
  assert.throws(() => state.transition('IDLE'), CompanionStateTransitionError);
  assert.throws(() => state.synchronize('IDLE'), CompanionStateTransitionError);
  assert.throws(
    () => state.transition('IDLE', { respawnRecovery: true }),
    CompanionStateTransitionError
  );
  assert.equal(state.current(), 'STOPPED');
  assert.equal(
    state.synchronize('IDLE', { emergencyRecovery: true }),
    'IDLE'
  );
});

test('death, emergency, and disconnect recovery never restore a prior command', () => {
  const death = new CompanionStateMachine('FOLLOWING');
  death.transition('DEAD');
  death.transition('IDLE', { respawnRecovery: true });
  assert.equal(death.current(), 'IDLE');

  const emergency = new CompanionStateMachine('RETURNING');
  emergency.transition('STOPPED');
  emergency.transition('IDLE', { emergencyRecovery: true });
  assert.equal(emergency.current(), 'IDLE');

  const disconnected = new CompanionStateMachine('WAITING');
  disconnected.transition('DISCONNECTED');
  disconnected.transition('IDLE');
  assert.equal(disconnected.current(), 'IDLE');
});
