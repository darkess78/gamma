import type { CompanionState } from './protocol.js';

export type CompanionOperationalState = Exclude<CompanionState, 'FLEEING'>;

export type CompanionStateTransitionOptions = Readonly<{
  emergencyRecovery?: boolean;
  respawnRecovery?: boolean;
}>;

export class CompanionStateTransitionError extends Error {
  constructor() {
    super('Companion state transition is not allowed');
    this.name = 'CompanionStateTransitionError';
  }
}

const ALLOWED_TRANSITIONS: Readonly<
  Record<CompanionOperationalState, ReadonlySet<CompanionOperationalState>>
> = Object.freeze({
  DISCONNECTED: states('DISCONNECTED', 'IDLE', 'STOPPED'),
  IDLE: states(
    'IDLE',
    'FOLLOWING',
    'WAITING',
    'RETURNING',
    'DEAD',
    'STOPPED',
    'DISCONNECTED'
  ),
  FOLLOWING: states(
    'FOLLOWING',
    'IDLE',
    'WAITING',
    'DEAD',
    'STOPPED',
    'DISCONNECTED'
  ),
  WAITING: states(
    'WAITING',
    'IDLE',
    'FOLLOWING',
    'RETURNING',
    'DEAD',
    'STOPPED',
    'DISCONNECTED'
  ),
  RETURNING: states(
    'RETURNING',
    'IDLE',
    'WAITING',
    'DEAD',
    'STOPPED',
    'DISCONNECTED'
  ),
  DEAD: states('DEAD', 'IDLE', 'STOPPED', 'DISCONNECTED'),
  STOPPED: states('STOPPED', 'DISCONNECTED', 'IDLE')
});

function states(
  ...values: CompanionOperationalState[]
): ReadonlySet<CompanionOperationalState> {
  return new Set(values);
}

export class CompanionStateMachine {
  #state: CompanionOperationalState;

  constructor(initialState: CompanionOperationalState = 'DISCONNECTED') {
    this.#state = initialState;
  }

  current(): CompanionOperationalState {
    return this.#state;
  }

  transition(
    next: CompanionOperationalState,
    options: CompanionStateTransitionOptions = {}
  ): CompanionOperationalState {
    if (!ALLOWED_TRANSITIONS[this.#state].has(next)) {
      throw new CompanionStateTransitionError();
    }
    if (
      this.#state === 'STOPPED' &&
      next === 'IDLE' &&
      options.emergencyRecovery !== true
    ) {
      throw new CompanionStateTransitionError();
    }
    if (
      this.#state === 'DEAD' &&
      next === 'IDLE' &&
      options.respawnRecovery !== true
    ) {
      throw new CompanionStateTransitionError();
    }
    this.#state = next;
    return this.#state;
  }

  synchronize(
    next: CompanionOperationalState,
    options: CompanionStateTransitionOptions = {}
  ): CompanionOperationalState {
    return this.transition(next, options);
  }
}
