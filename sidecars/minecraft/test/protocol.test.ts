import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, parse, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  COMMAND_NAMES,
  COMPANION_STATES,
  FAILURE_CODES,
  MESSAGE_TYPES,
  TERMINAL_OUTCOMES,
  parseProtocolMessage
} from '../src/protocol.js';

const EXPECTED_FIXTURES = {
  'cancel-command.json': 'cancel_command',
  'command-ack.json': 'command_ack',
  'command-follow-owner.json': 'command',
  'command-progress.json': 'command_progress',
  'emergency-stop.json': 'emergency_stop',
  'heartbeat.json': 'heartbeat',
  'hello.json': 'hello',
  'minecraft-status.json': 'minecraft_status',
  'protocol-error.json': 'protocol_error',
  'shutdown.json': 'shutdown',
  'sidecar-status.json': 'sidecar_status',
  'state-snapshot.json': 'state_snapshot',
  'terminal-result.json': 'terminal_result',
  'welcome.json': 'welcome'
} as const;

const CANONICAL_CONTRACT = {
  messageTypes: [
    'hello',
    'welcome',
    'heartbeat',
    'sidecar_status',
    'minecraft_status',
    'command',
    'command_ack',
    'command_progress',
    'state_snapshot',
    'terminal_result',
    'cancel_command',
    'emergency_stop',
    'shutdown',
    'protocol_error'
  ],
  commandNames: [
    'join',
    'leave',
    'follow_owner',
    'wait_here',
    'come_here',
    'look_at_owner',
    'report_status',
    'stop',
    'emergency_stop'
  ],
  terminalOutcomes: ['completed', 'cancelled', 'failed', 'rejected', 'timed_out'],
  failureCodes: [
    'FEATURE_DISABLED',
    'SIDECAR_UNAVAILABLE',
    'PROTOCOL_MISMATCH',
    'MINECRAFT_NOT_CONNECTED',
    'OWNER_NOT_CONFIGURED',
    'OWNER_NOT_PRESENT',
    'UNAUTHORIZED_REQUESTER',
    'INVALID_COMMAND',
    'INVALID_STATE',
    'COMMAND_ALREADY_ACTIVE',
    'DESTINATION_UNAVAILABLE',
    'PATH_NOT_FOUND',
    'PATH_STALLED',
    'OWNER_TOO_FAR_AWAY',
    'DEADLINE_EXCEEDED',
    'SAFETY_POLICY_BLOCKED',
    'UNSUPPORTED_DIMENSION',
    'SIDECAR_DISCONNECTED',
    'MINECRAFT_SERVER_DISCONNECTED',
    'BOT_DEAD',
    'EMERGENCY_STOP_ACTIVE',
    'INTERNAL_SIDECAR_ERROR',
    'INTERNAL_GAMMA_ERROR'
  ],
  companionStates: [
    'DISCONNECTED',
    'IDLE',
    'FOLLOWING',
    'WAITING',
    'RETURNING',
    'FLEEING',
    'DEAD',
    'STOPPED'
  ]
} as const;

type JsonObject = Record<string, unknown>;

function findRepositoryRoot(): string {
  let current = dirname(fileURLToPath(import.meta.url));
  const filesystemRoot = parse(current).root;
  while (current !== filesystemRoot) {
    if (
      existsSync(join(current, 'specs', 'minecraft_companion.md')) &&
      existsSync(join(current, 'tests', 'fixtures', 'minecraft_protocol', 'v1'))
    ) {
      return current;
    }
    current = dirname(current);
  }
  throw new Error('Could not locate Gamma repository root from compiled protocol test');
}

const repositoryRoot = findRepositoryRoot();
const fixtureRoot = join(repositoryRoot, 'tests', 'fixtures', 'minecraft_protocol', 'v1');
const pythonProtocolPath = join(
  repositoryRoot,
  'src',
  'gamma',
  'integrations',
  'minecraft',
  'protocol.py'
);

function object(value: unknown, label: string): JsonObject {
  assert.equal(typeof value, 'object', `${label} must be an object`);
  assert.notEqual(value, null, `${label} must not be null`);
  assert.equal(Array.isArray(value), false, `${label} must not be an array`);
  return value as JsonObject;
}

function fixture(name: keyof typeof EXPECTED_FIXTURES): JsonObject {
  const path = join(fixtureRoot, name);
  assert.equal(existsSync(path), true, `Missing shared fixture: ${name}`);
  return structuredClone(object(JSON.parse(readFileSync(path, 'utf8')), name));
}

function payload(data: JsonObject): JsonObject {
  return object(data.payload, 'payload');
}

function argumentsObject(data: JsonObject): JsonObject {
  return object(payload(data).arguments, 'arguments');
}

function rejects(data: JsonObject): void {
  assert.throws(() => parseProtocolMessage(data));
}

function pythonEnumValues(source: string, className: string): string[] {
  const marker = `class ${className}(str, Enum):`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `Missing Python enum ${className}`);
  const remainder = source.slice(start + marker.length);
  const nextClass = remainder.search(/\n\nclass /);
  const block = nextClass === -1 ? remainder : remainder.slice(0, nextClass);
  return [...block.matchAll(/^\s+[A-Z][A-Z0-9_]*\s*=\s*"([^"]+)"/gm)].map(
    (match) => match[1] ?? ''
  );
}

test('shared fixture inventory is exact and every fixture parses', () => {
  const actualFiles = readdirSync(fixtureRoot)
    .filter((name) => name.endsWith('.json'))
    .sort();
  const expectedFiles = Object.keys(EXPECTED_FIXTURES).sort();
  assert.equal(actualFiles.length, 14);
  assert.deepEqual(actualFiles, expectedFiles);

  for (const name of expectedFiles) {
    const fixtureName = name as keyof typeof EXPECTED_FIXTURES;
    const parsed = parseProtocolMessage(fixture(fixtureName));
    assert.equal(parsed.type, EXPECTED_FIXTURES[fixtureName], `${name} parsed as wrong type`);
    if (name === 'command-follow-owner.json') {
      assert.equal(parsed.type, 'command');
      assert.equal(parsed.payload.name, 'follow_owner');
    }
  }
});

test('TypeScript tables match fixtures and canonical Python enums', () => {
  assert.deepEqual(MESSAGE_TYPES, CANONICAL_CONTRACT.messageTypes);
  assert.deepEqual(COMMAND_NAMES, CANONICAL_CONTRACT.commandNames);
  assert.deepEqual(TERMINAL_OUTCOMES, CANONICAL_CONTRACT.terminalOutcomes);
  assert.deepEqual(FAILURE_CODES, CANONICAL_CONTRACT.failureCodes);
  assert.deepEqual(COMPANION_STATES, CANONICAL_CONTRACT.companionStates);

  const fixtureTypes = Object.values(EXPECTED_FIXTURES).sort();
  assert.deepEqual(fixtureTypes, [...CANONICAL_CONTRACT.messageTypes].sort());

  const pythonSource = readFileSync(pythonProtocolPath, 'utf8');
  assert.deepEqual(pythonEnumValues(pythonSource, 'CommandName'), [...COMMAND_NAMES]);
  assert.deepEqual(pythonEnumValues(pythonSource, 'TerminalOutcome'), [...TERMINAL_OUTCOMES]);
  assert.deepEqual(pythonEnumValues(pythonSource, 'FailureCode'), [...FAILURE_CODES]);
  assert.deepEqual(pythonEnumValues(pythonSource, 'CompanionState'), [...COMPANION_STATES]);
});

test('all command discriminators select a dedicated bounded argument object', () => {
  const validArguments: Record<(typeof COMMAND_NAMES)[number], JsonObject> = {
    join: { connection_profile_id: 'private-dev' },
    leave: { reason: 'Owner request.' },
    follow_owner: { follow_distance: 3, lease_duration_seconds: 300 },
    wait_here: { reason: 'Hold this position.' },
    come_here: { arrival_distance: 3 },
    look_at_owner: { duration_seconds: 2 },
    report_status: { detail_level: 'standard' },
    stop: { reason: 'Owner request.' },
    emergency_stop: { reason: 'Immediate operator stop.' }
  };
  for (const name of COMMAND_NAMES) {
    const data = fixture('command-follow-owner.json');
    data.payload = {
      name,
      deadline_at: '2026-07-10T18:06:00Z',
      arguments: validArguments[name]
    };
    const parsed = parseProtocolMessage(data);
    assert.equal(parsed.type, 'command');
    assert.equal(parsed.payload.name, name);
  }
});

test('unknown protocol version, message type, command, outcome, and failure code are rejected', () => {
  const version = fixture('heartbeat.json');
  version.version = 2;
  rejects(version);

  const messageType = fixture('heartbeat.json');
  messageType.type = 'execute_javascript';
  rejects(messageType);

  const command = fixture('command-follow-owner.json');
  payload(command).name = 'mine_diamonds';
  rejects(command);

  const outcome = fixture('terminal-result.json');
  payload(outcome).outcome = 'partially_completed';
  rejects(outcome);

  const failureCode = fixture('protocol-error.json');
  payload(failureCode).code = 'UNKNOWN_FAILURE';
  rejects(failureCode);
});

test('unknown fields are rejected at envelope, payload, and command argument levels', () => {
  const envelope = fixture('command-follow-owner.json');
  envelope.extra = true;
  rejects(envelope);

  const commandPayload = fixture('command-follow-owner.json');
  payload(commandPayload).extra = true;
  rejects(commandPayload);

  const commandArguments = fixture('command-follow-owner.json');
  argumentsObject(commandArguments).extra = true;
  rejects(commandArguments);
});

test('required envelope and correlation identifiers are enforced', () => {
  const message = fixture('hello.json');
  delete message.message_id;
  rejects(message);

  const connected = fixture('heartbeat.json');
  delete connected.connection_id;
  rejects(connected);

  const command = fixture('command-ack.json');
  delete command.command_id;
  rejects(command);

  const correlated = fixture('command-progress.json');
  delete correlated.trace_id;
  rejects(correlated);
});

test('sequence and timestamps enforce canonical scalar rules', () => {
  const negativeSequence = fixture('heartbeat.json');
  negativeSequence.sequence = -1;
  rejects(negativeSequence);

  const naiveTimestamp = fixture('heartbeat.json');
  naiveTimestamp.sent_at = '2026-07-10T18:00:00';
  rejects(naiveTimestamp);

  const malformedTimestamp = fixture('heartbeat.json');
  malformedTimestamp.sent_at = 'not-a-timestamp';
  rejects(malformedTimestamp);

  const nonUtcTimestamp = fixture('heartbeat.json');
  nonUtcTimestamp.sent_at = '2026-07-10T12:00:00-06:00';
  rejects(nonUtcTimestamp);
});

test('command deadlines must follow sent_at and remain within 900 seconds', () => {
  const expired = fixture('command-follow-owner.json');
  payload(expired).deadline_at = '2026-07-10T18:01:00Z';
  rejects(expired);

  const tooFar = fixture('command-follow-owner.json');
  payload(tooFar).deadline_at = '2026-07-10T18:16:01Z';
  rejects(tooFar);

  const malformed = fixture('command-follow-owner.json');
  payload(malformed).deadline_at = 'tomorrow';
  rejects(malformed);
});

test('dangerous command arguments and arbitrary coordinates are rejected', () => {
  const dangerousFields: Record<string, unknown>[] = [
    { javascript: "bot.chat('op me')" },
    { method_name: 'setControlState' },
    { server_command: '/op Shana' },
    { pathfinder_goal: { kind: 'GoalBlock' } },
    { x: 10, y: 64, z: 10 }
  ];
  for (const dangerous of dangerousFields) {
    const data = fixture('command-follow-owner.json');
    Object.assign(argumentsObject(data), dangerous);
    rejects(data);
  }
});

test('reason and safe detail fields enforce canonical bounds', () => {
  const reason = fixture('cancel-command.json');
  payload(reason).reason = 'x'.repeat(161);
  rejects(reason);

  const detail = fixture('protocol-error.json');
  payload(detail).safe_detail = 'x'.repeat(257);
  rejects(detail);
});

test('follow and arrival command bounds are enforced', () => {
  for (const distance of [1.99, 6.01]) {
    const data = fixture('command-follow-owner.json');
    argumentsObject(data).follow_distance = distance;
    rejects(data);
  }
  for (const lease of [4, 901]) {
    const data = fixture('command-follow-owner.json');
    argumentsObject(data).lease_duration_seconds = lease;
    rejects(data);
  }
  for (const arrival of [1.99, 4.01]) {
    const data = fixture('command-follow-owner.json');
    data.payload = {
      name: 'come_here',
      deadline_at: '2026-07-10T18:06:00Z',
      arguments: { arrival_distance: arrival }
    };
    rejects(data);
  }
});

test('snapshot health, hunger, pairing, and bounded fields are enforced', () => {
  for (const [field, value] of [
    ['health', -0.1],
    ['health', 20.1],
    ['hunger', -1],
    ['hunger', 21]
  ] as const) {
    const data = fixture('state-snapshot.json');
    payload(data)[field] = value;
    rejects(data);
  }

  const unpaired = fixture('state-snapshot.json');
  delete payload(unpaired).active_command_name;
  rejects(unpaired);

  for (const forbidden of [
    'raw_chat',
    'sign',
    'book',
    'server_motd',
    'authentication',
    'secret',
    'entities',
    'mineflayer_object'
  ]) {
    const data = fixture('state-snapshot.json');
    payload(data)[forbidden] = 'unsafe';
    rejects(data);
  }
});

test('emergency stop accepts only its bounded reason payload', () => {
  for (const forbidden of ['javascript', 'method_name', 'coordinates', 'raw_chat', 'secret']) {
    const data = fixture('emergency-stop.json');
    payload(data)[forbidden] = 'unsafe';
    rejects(data);
  }
  const oversized = fixture('emergency-stop.json');
  payload(oversized).reason = 'x'.repeat(161);
  rejects(oversized);

  for (const requiredField of ['connection_id', 'command_id', 'trace_id']) {
    const data = fixture('emergency-stop.json');
    delete data[requiredField];
    rejects(data);
  }
});

test('every terminal outcome is accepted with canonical failure semantics', () => {
  for (const outcome of TERMINAL_OUTCOMES) {
    const data = fixture('terminal-result.json');
    payload(data).outcome = outcome;
    payload(data).failure = ['failed', 'rejected', 'timed_out'].includes(outcome)
      ? {
          code: 'PATH_NOT_FOUND',
          safe_detail: 'No safe bounded path was found.',
          retriable: true
        }
      : null;
    const parsed = parseProtocolMessage(data);
    assert.equal(parsed.type, 'terminal_result');
    assert.equal(parsed.payload.outcome, outcome);
  }
});

test('every stable failure code is accepted', () => {
  for (const code of FAILURE_CODES) {
    const data = fixture('terminal-result.json');
    payload(data).outcome = 'failed';
    payload(data).failure = {
      code,
      safe_detail: 'Bounded operator-safe detail.',
      retriable: false
    };
    const parsed = parseProtocolMessage(data);
    assert.equal(parsed.type, 'terminal_result');
    assert.equal(parsed.payload.failure?.code, code);
  }
});
