import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import {
  existsSync,
  readFileSync,
  readdirSync,
  type Dirent
} from 'node:fs';
import { join, relative } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import * as ts from 'typescript/unstable/ast';
import { API, type Snapshot } from 'typescript/unstable/sync';

import { CompanionExecutor } from '../src/companion-executor.js';
import { loadMinecraftSidecarRuntimeConfig } from '../src/config.js';
import type {
  MinecraftDimension,
  ObservedPlayer,
  SafePosition
} from '../src/minecraft-adapter.js';
import {
  MinecraftAdapterError,
  MineflayerMinecraftAdapter
} from '../src/mineflayer-runtime.js';
import {
  classifyDirectSteeringSpace,
  classifyDirectSteeringSupport,
  type DirectSteeringBlock
} from '../src/safety.js';

const SIDECAR_ROOT = fileURLToPath(new URL('../..', import.meta.url));
const SOURCE_ROOT = join(SIDECAR_ROOT, 'src');
const FULL_CUBE = Object.freeze([
  Object.freeze([0, 0, 0, 1, 1, 1])
]);
const AIR = block('air', 'empty', []);
const STONE = block('stone', 'block', FULL_CUBE);

test('direct-steering classifiers accept literal air over loaded full-cube support', () => {
  assert.equal(classifyDirectSteeringSpace(AIR), 'safe');
  assert.equal(
    classifyDirectSteeringSpace(block('cave_air', 'empty', [])),
    'safe'
  );
  assert.equal(
    classifyDirectSteeringSpace(block('void_air', 'empty', [])),
    'safe'
  );
  assert.equal(classifyDirectSteeringSupport(STONE), 'safe');
  for (const name of [
    'cobbled_deepslate',
    'cobblestone',
    'coarse_dirt',
    'deepslate',
    'dirt',
    'grass_block',
    'podzol'
  ]) {
    assert.equal(
      classifyDirectSteeringSupport(block(name, 'block', FULL_CUBE)),
      'safe',
      `${name} is a deliberately supported inert full cube`
    );
  }
});

test('direct-steering classifiers reject every required hazard and liquid', () => {
  for (const name of [
    'fire',
    'soul_fire',
    'cactus',
    'campfire',
    'soul_campfire',
    'magma_block',
    'powder_snow',
    'nether_portal',
    'end_portal',
    'end_gateway',
    'sweet_berry_bush',
    'cobweb'
  ]) {
    const value = block(name, 'empty', []);
    assert.equal(
      classifyDirectSteeringSpace(value),
      'hazard',
      `${name} must be hazardous space`
    );
    assert.equal(
      classifyDirectSteeringSupport(value),
      'hazard',
      `${name} must be hazardous support`
    );
  }

  for (const name of ['water', 'lava', 'bubble_column']) {
    const value = block(name, 'empty', []);
    assert.equal(
      classifyDirectSteeringSpace(value),
      'liquid',
      `${name} must be rejected as liquid space`
    );
    assert.equal(
      classifyDirectSteeringSupport(value),
      'liquid',
      `${name} must be rejected as liquid support`
    );
  }

  const waterlogged = Object.freeze({
    name: 'stone',
    boundingBox: 'block',
    shapes: FULL_CUBE,
    isWaterlogged: true
  });
  assert.equal(classifyDirectSteeringSpace(waterlogged), 'liquid');
  assert.equal(classifyDirectSteeringSupport(waterlogged), 'liquid');
});

test('direct-steering classifiers fail closed on unloaded, blocked, dropped, partial, and unknown terrain', () => {
  assert.equal(classifyDirectSteeringSpace(null), 'unloaded');
  assert.equal(classifyDirectSteeringSupport(null), 'unloaded');
  assert.equal(classifyDirectSteeringSpace(Object.freeze({})), 'unloaded');
  assert.equal(classifyDirectSteeringSupport(Object.freeze({})), 'unloaded');

  assert.equal(classifyDirectSteeringSpace(STONE), 'blocked');
  assert.equal(
    classifyDirectSteeringSpace(block('oak_door', 'empty', [])),
    'blocked'
  );
  assert.equal(
    classifyDirectSteeringSpace(block('unknown_modded_plant', 'empty', [])),
    'blocked'
  );

  assert.equal(classifyDirectSteeringSupport(AIR), 'unsupported_drop');
  assert.equal(
    classifyDirectSteeringSupport(
      block('stone_slab', 'block', [[0, 0, 0, 1, 0.5, 1]])
    ),
    'unsupported_drop'
  );
  assert.equal(
    classifyDirectSteeringSupport(block('unknown_shape', 'block', [])),
    'unsupported_drop'
  );
  assert.equal(
    classifyDirectSteeringSupport(block('stone', 'empty', FULL_CUBE)),
    'unsupported_drop'
  );
  assert.equal(
    classifyDirectSteeringSupport(
      block('unknown_modded_full_cube', 'block', FULL_CUBE)
    ),
    'unsupported_drop'
  );
  for (const name of [
    'stone_slab',
    'oak_stairs',
    'oak_fence',
    'cobblestone_wall',
    'oak_trapdoor',
    'snow',
    'white_carpet'
  ]) {
    assert.equal(
      classifyDirectSteeringSupport(block(name, 'block', FULL_CUBE)),
      'unsupported_drop',
      `${name} is not a deliberately supported full-cube surface`
    );
  }
});

test('owner configuration is optional for join and strictly bounded for movement', () => {
  const base = loadMinecraftSidecarRuntimeConfig({
    SHANA_MINECRAFT_CONTROL_TOKEN: 'owner-config-token-8Ts4'
  });
  assert.equal(base.minecraftOwnerUsername, null);

  const configured = loadMinecraftSidecarRuntimeConfig({
    SHANA_MINECRAFT_CONTROL_TOKEN: 'owner-config-token-8Ts4',
    SHANA_MINECRAFT_OWNER_USERNAME: 'Neety_1'
  });
  assert.equal(configured.minecraftOwnerUsername, 'Neety_1');

  for (const owner of [
    '',
    'ab',
    'space name',
    'dash-name',
    'x'.repeat(17),
    'éOwner'
  ]) {
    assert.throws(
      () =>
        loadMinecraftSidecarRuntimeConfig({
          SHANA_MINECRAFT_CONTROL_TOKEN: 'owner-config-token-8Ts4',
          SHANA_MINECRAFT_OWNER_USERNAME: owner
        }),
      /owner username/u
    );
  }
});

test('production adapter owner matching is lowercase-normalized, exact, unique, and copy-only', async () => {
  const harness = await productionHarness();
  try {
    const lower = harness.adapter.getPlayer('neety');
    const upper = harness.adapter.getPlayer('NEETY');
    assert.notEqual(lower, undefined);
    assert.notEqual(upper, undefined);
    assert.equal(lower?.username, 'NeEtY');
    assert.deepEqual(lower?.position, { x: 8, y: 64, z: 0 });
    assert.equal(Object.isFrozen(lower), true);
    assert.equal(Object.isFrozen(lower?.position), true);
    assert.notEqual(lower?.position, harness.bot.ownerPosition());
    assert.equal(harness.adapter.getPlayer('neet'), undefined);
    assert.equal(harness.adapter.getPlayer('stranger')?.username, 'Stranger');
    assert.equal(harness.executor.owner()?.username, 'NeEtY');
    assert.equal(harness.adapter.getPlayer('bad name'), undefined);

    assert.throws(() => {
      (lower?.position as { x: number }).x = 99;
    }, TypeError);
    assert.deepEqual(harness.adapter.getPlayer('neety')?.position, {
      x: 8,
      y: 64,
      z: 0
    });

    harness.bot.setDuplicateOwner(true);
    assert.equal(harness.adapter.getPlayer('neety'), undefined);
    harness.bot.setDuplicateOwner(false);
    harness.bot.hideOwner();
    assert.equal(harness.adapter.getPlayer('neety'), undefined);
  } finally {
    await harness.close();
  }
});

test('production adapter forward inspection mirrors every fail-closed terrain category', async () => {
  const harness = await productionHarness();
  try {
    const owner = harness.adapter.getPlayer('NEETY');
    assert.notEqual(owner, undefined);
    const observed = owner as ObservedPlayer;

    harness.bot.terrain = 'safe';
    const safe = harness.adapter.inspectForwardStep(observed);
    assert.equal(safe.kind, 'safe');
    if (safe.kind === 'safe') {
      assert.equal(Object.isFrozen(safe.candidate), true);
      assert.equal(safe.candidate.y, 64);
      assert.equal(Math.hypot(safe.candidate.x, safe.candidate.z) <= 0.45, true);
    }

    for (const [terrain, expected] of [
      ['blocked', 'blocked'],
      ['drop', 'unsupported_drop'],
      ['partial', 'unsupported_drop'],
      ['hazard', 'hazard'],
      ['liquid', 'liquid'],
      ['unloaded', 'unloaded'],
      ['unknown', 'blocked']
    ] as const) {
      harness.bot.terrain = terrain;
      assert.equal(
        harness.adapter.inspectForwardStep(observed).kind,
        expected,
        `${terrain} terrain must fail as ${expected}`
      );
    }

    harness.bot.terrain = 'safe';
    const wrongDimension = Object.freeze({
      ...observed,
      dimension: 'minecraft:the_nether' as MinecraftDimension
    });
    assert.equal(
      harness.adapter.inspectForwardStep(wrongDimension).kind,
      'dimension_mismatch'
    );
    const nonFinite = Object.freeze({
      ...observed,
      position: Object.freeze({ x: Number.NaN, y: 64, z: 0 })
    });
    assert.equal(
      harness.adapter.inspectForwardStep(nonFinite).kind,
      'unloaded'
    );
  } finally {
    await harness.close();
  }
});

test('setForward never activates forward when clearing prior controls throws', async () => {
  const harness = await productionHarness();
  try {
    const activationsBefore = harness.bot.forwardActivations;
    harness.bot.throwOnClear = true;
    assert.doesNotThrow(() => harness.adapter.setForward(true));
    assert.equal(harness.bot.forwardActivations, activationsBefore);
    assert.equal(harness.bot.forwardEnabled, false);
    assert.equal(harness.bot.clearCalls > 0, true);
    harness.bot.throwOnClear = false;
  } finally {
    await harness.close();
  }
});

test('0.31 player footprint catches adjacent hazard and collision cells that 0.29 misses', async () => {
  assert.equal(Math.floor(0.3 - 0.29), 0);
  assert.equal(Math.floor(0.3 - 0.31), -1);
  const harness = await productionHarness({ botZ: 0.3 });
  try {
    const owner = harness.adapter.getPlayer('neety');
    assert.notEqual(owner, undefined);
    if (owner === undefined) return;

    harness.bot.terrain = 'edge_hazard';
    assert.equal(harness.adapter.inspectForwardStep(owner).kind, 'hazard');

    harness.bot.terrain = 'edge_blocked';
    assert.equal(harness.adapter.inspectForwardStep(owner).kind, 'blocked');
  } finally {
    await harness.close();
  }
});

test('diagonal inspection checks every transient cell in the swept player footprint', async () => {
  const cases = [
    ['swept_corner_blocked', 'blocked'],
    ['swept_corner_head_blocked', 'blocked'],
    ['swept_corner_hazard', 'hazard'],
    ['swept_corner_drop', 'unsupported_drop']
  ] as const;
  for (const [terrain, expected] of cases) {
    const harness = await productionHarness({
      botX: 0.64,
      botZ: 1.16,
      ownerX: 8.64,
      ownerZ: 9.16
    });
    try {
      const owner = harness.adapter.getPlayer('neety');
      assert.notEqual(owner, undefined);
      if (owner === undefined) continue;
      harness.bot.terrain = terrain;
      assert.equal(
        harness.adapter.inspectForwardStep(owner).kind,
        expected,
        `${terrain} must fail closed in the transient diagonal corner cell`
      );
      assert.equal(
        harness.bot.blockQueries.some(
          (query) => query.x === 1 && query.z === 0
        ),
        true,
        'the swept-only corner cell must be inspected'
      );
    } finally {
      await harness.close();
    }
  }
});

test('forward inspection and look reject non-finite, zero, overflowed, and non-level coordinates', async () => {
  const harness = await productionHarness();
  try {
    const dimension = 'minecraft:overworld' as const;
    for (const coordinate of ['x', 'y', 'z'] as const) {
      for (const invalid of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
        const position = { x: 8, y: 64, z: 0 };
        position[coordinate] = invalid;
        assert.equal(
          harness.adapter.inspectForwardStep(
            Object.freeze({
              username: 'Neety',
              position: Object.freeze(position),
              dimension
            })
          ).kind,
          'unloaded'
        );
        await assert.rejects(
          harness.adapter.lookAt(Object.freeze(position)),
          MinecraftAdapterError
        );
      }
    }

    assert.equal(
      harness.adapter.inspectForwardStep(
        Object.freeze({
          username: 'Neety',
          position: Object.freeze({ x: 0, y: 64, z: 0 }),
          dimension
        })
      ).kind,
      'blocked'
    );
    assert.equal(
      harness.adapter.inspectForwardStep(
        Object.freeze({
          username: 'Neety',
          position: Object.freeze({
            x: Number.MAX_VALUE,
            y: 64,
            z: Number.MAX_VALUE
          }),
          dimension
        })
      ).kind,
      'unloaded'
    );
  } finally {
    await harness.close();
  }

  const nonLevel = await productionHarness({ botY: 64.1 });
  try {
    const owner = nonLevel.adapter.getPlayer('neety');
    assert.notEqual(owner, undefined);
    if (owner !== undefined) {
      assert.equal(
        nonLevel.adapter.inspectForwardStep(owner).kind,
        'unsupported_drop'
      );
    }
  } finally {
    await nonLevel.close();
  }

  const outOfBoundsBot = await productionHarness({
    botX: Number.MAX_VALUE
  });
  try {
    const owner = outOfBoundsBot.adapter.getPlayer('neety');
    assert.notEqual(owner, undefined);
    if (owner !== undefined) {
      assert.equal(
        outOfBoundsBot.adapter.inspectForwardStep(owner).kind,
        'unloaded'
      );
    }
  } finally {
    await outOfBoundsBot.close();
  }
});

test('adapter permits only one active physics listener and cleanup is idempotent', async () => {
  const harness = await productionHarness();
  try {
    let firstTicks = 0;
    let rejectedTicks = 0;
    const cleanupFirst = harness.adapter.onMovementTick(() => {
      firstTicks += 1;
    });
    assert.equal(harness.bot.listenerCount('physicsTick'), 1);
    const clearsBefore = harness.bot.clearCalls;
    assert.throws(
      () =>
        harness.adapter.onMovementTick(() => {
          rejectedTicks += 1;
        }),
      MinecraftAdapterError
    );
    assert.equal(harness.bot.clearCalls > clearsBefore, true);
    assert.equal(harness.bot.listenerCount('physicsTick'), 1);
    harness.bot.emit('physicsTick');
    assert.equal(firstTicks, 1);
    assert.equal(rejectedTicks, 0);

    cleanupFirst();
    cleanupFirst();
    assert.equal(harness.bot.listenerCount('physicsTick'), 0);
    harness.bot.emit('physicsTick');
    assert.equal(firstTicks, 1);

    const cleanupReplacement = harness.adapter.onMovementTick(() => {
      firstTicks += 1;
    });
    assert.equal(harness.bot.listenerCount('physicsTick'), 1);
    cleanupReplacement();
    cleanupReplacement();
    assert.equal(harness.bot.listenerCount('physicsTick'), 0);
  } finally {
    await harness.close();
  }
});

test('connect waits for a prior transport close before creating a replacement bot', async () => {
  const first = new SafetyTrapBot();
  first.autoEndOnQuit = false;
  const second = new SafetyTrapBot();
  let createCalls = 0;
  const adapter = new MineflayerMinecraftAdapter({
    createBot: () => {
      createCalls += 1;
      return createCalls === 1 ? first : second;
    },
    createVector: (x, y, z) => Object.freeze({ x, y, z })
  });
  const config = loadMinecraftSidecarRuntimeConfig({
    SHANA_MINECRAFT_CONTROL_TOKEN: 'pending-close-token-5Jm9'
  });

  const initialConnection = adapter.connect(
    config,
    new AbortController().signal
  );
  first.emit('spawn');
  await initialConnection;
  assert.equal(createCalls, 1);

  const disconnecting = adapter.disconnect();
  const replacementConnection = adapter.connect(
    config,
    new AbortController().signal
  );
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(createCalls, 1, 'replacement creation must wait for prior close');

  first.emit('end');
  await disconnecting;
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(createCalls, 2);
  second.emit('spawn');
  await replacementConnection;
  await adapter.disconnect();
  assertTrapProof(first);
  assertTrapProof(second);
});

test('production source AST contains no forbidden movement, mutation, interaction, or execution construct', () => {
  const violations = scanProductionSafetyViolations();
  assert.deepEqual(violations, []);
});

test('pathfinder is absent from manifest, lockfile, installed tree, and production imports', () => {
  const manifest = JSON.parse(
    readFileSync(join(SIDECAR_ROOT, 'package.json'), 'utf8')
  ) as Readonly<Record<string, unknown>>;
  for (const section of [
    'dependencies',
    'devDependencies',
    'optionalDependencies',
    'peerDependencies',
    'overrides'
  ]) {
    const entries = manifest[section];
    if (typeof entries !== 'object' || entries === null) continue;
    assert.equal(
      Object.prototype.hasOwnProperty.call(entries, 'mineflayer-pathfinder'),
      false,
      `${section} must not contain mineflayer-pathfinder`
    );
  }
  assert.doesNotMatch(
    readFileSync(join(SIDECAR_ROOT, 'package-lock.json'), 'utf8'),
    /mineflayer-pathfinder/u
  );
  assert.equal(
    existsSync(join(SIDECAR_ROOT, 'node_modules', 'mineflayer-pathfinder')),
    false
  );
  assert.equal(
    scanProductionSafetyViolations().some((value) =>
      value.includes('mineflayer-pathfinder')
    ),
    false
  );
});

test('production adapter and executor commands never mutate entity position or velocity or call forbidden methods', async (context) => {
  await context.test('follow, wait, come, look, stop, and emergency stop', async () => {
    const harness = await productionHarness();
    try {
      const followController = new AbortController();
      const follow = harness.executor.followOwner(
        3,
        300,
        harness.deadline(60_000),
        followController.signal
      );
      await harness.tick();
      assert.equal(harness.bot.forwardActivations > 0, true);
      assert.equal(harness.bot.listenerCount('physicsTick'), 1);
      const wait = harness.executor.waitHere();
      assert.equal(wait.outcome, 'completed');
      assert.equal((await follow).outcome, 'cancelled');
      assert.equal(harness.bot.listenerCount('physicsTick'), 0);

      harness.bot.setOwnerX(8);
      const come = harness.executor.comeHere(
        3,
        harness.deadline(60_000),
        new AbortController().signal
      );
      await harness.tick();
      harness.bot.setOwnerX(2);
      await harness.tick();
      assert.equal((await come).outcome, 'completed');
      assert.equal(harness.bot.listenerCount('physicsTick'), 0);

      harness.bot.setOwnerX(8);
      const forwardBeforeLook = harness.bot.forwardActivations;
      const look = await harness.executor.lookAtOwner(
        2,
        harness.deadline(10_000),
        new AbortController().signal
      );
      assert.equal(look.outcome, 'completed');
      assert.equal(harness.bot.forwardActivations, forwardBeforeLook);

      const stoppedFollow = harness.executor.followOwner(
        3,
        300,
        harness.deadline(60_000),
        new AbortController().signal
      );
      await harness.tick();
      assert.equal(harness.executor.stop().outcome, 'completed');
      assert.equal((await stoppedFollow).outcome, 'cancelled');

      const emergencyFollow = harness.executor.followOwner(
        3,
        300,
        harness.deadline(60_000),
        new AbortController().signal
      );
      await harness.tick();
      harness.executor.emergencyStop();
      assert.equal((await emergencyFollow).outcome, 'cancelled');
      assert.equal(harness.executor.state(), 'STOPPED');
      assert.equal(harness.bot.listenerCount('physicsTick'), 0);
      assertTrapProof(harness.bot);
    } finally {
      await harness.close();
    }
  });

  await context.test('owner loss clears on the first missing-owner tick', async () => {
    const harness = await productionHarness();
    try {
      const follow = harness.executor.followOwner(
        3,
        300,
        harness.deadline(60_000),
        new AbortController().signal
      );
      await harness.tick();
      assert.notEqual(harness.executor.activeMovementTarget(), null);
      harness.bot.hideOwner();
      const clearsBefore = harness.bot.clearCalls;
      await harness.tick();
      assert.equal(harness.bot.clearCalls > clearsBefore, true);
      assert.equal(harness.executor.activeMovementTarget(), null);
      assert.equal(harness.bot.forwardEnabled, false);
      harness.executor.stop();
      assert.equal((await follow).outcome, 'cancelled');
      assertTrapProof(harness.bot);
    } finally {
      await harness.close();
    }
  });

  await context.test('blocked terrain remains stationary and stops after three retries', async () => {
    const harness = await productionHarness();
    try {
      harness.bot.terrain = 'blocked';
      const forwardBefore = harness.bot.forwardActivations;
      const follow = harness.executor.followOwner(
        3,
        300,
        harness.deadline(60_000),
        new AbortController().signal
      );
      await harness.tick();
      harness.advance(1_000);
      await harness.tick();
      harness.advance(1_000);
      await harness.tick();
      const result = await follow;
      assert.equal(result.outcome, 'failed');
      assert.equal(result.failureCode, 'PATH_NOT_FOUND');
      assert.equal(harness.bot.forwardActivations, forwardBefore);
      assert.equal(harness.bot.forwardEnabled, false);
      assert.equal(harness.bot.listenerCount('physicsTick'), 0);
      assertTrapProof(harness.bot);
    } finally {
      await harness.close();
    }
  });

  await context.test('death and disconnect clear controls and remove physics listeners', async () => {
    const deathHarness = await productionHarness();
    try {
      const follow = deathHarness.executor.followOwner(
        3,
        300,
        deathHarness.deadline(60_000),
        new AbortController().signal
      );
      await deathHarness.tick();
      const clearsBefore = deathHarness.bot.clearCalls;
      deathHarness.bot.emit('death');
      assert.equal(deathHarness.bot.clearCalls > clearsBefore, true);
      assert.equal(deathHarness.bot.forwardEnabled, false);
      assert.equal(deathHarness.bot.listenerCount('physicsTick'), 0);
      deathHarness.executor.cancelActiveMovement();
      assert.equal((await follow).outcome, 'cancelled');
      assert.equal(deathHarness.executor.state(), 'DEAD');
      assertTrapProof(deathHarness.bot);
    } finally {
      await deathHarness.close();
    }

    const disconnectHarness = await productionHarness();
    const follow = disconnectHarness.executor.followOwner(
      3,
      300,
      disconnectHarness.deadline(60_000),
      new AbortController().signal
    );
    await disconnectHarness.tick();
    await disconnectHarness.adapter.disconnect();
    assert.equal(disconnectHarness.bot.forwardEnabled, false);
    assert.equal(disconnectHarness.bot.listenerCount('physicsTick'), 0);
    disconnectHarness.executor.cancelActiveMovement();
    assert.equal((await follow).outcome, 'cancelled');
    assert.equal(disconnectHarness.executor.state(), 'DISCONNECTED');
    assertTrapProof(disconnectHarness.bot);
  });
});

type TerrainMode =
  | 'safe'
  | 'blocked'
  | 'drop'
  | 'partial'
  | 'hazard'
  | 'liquid'
  | 'unloaded'
  | 'unknown'
  | 'edge_hazard'
  | 'edge_blocked'
  | 'swept_corner_blocked'
  | 'swept_corner_head_blocked'
  | 'swept_corner_hazard'
  | 'swept_corner_drop';

class SafetyTrapBot extends EventEmitter {
  readonly version = '1.21.11';
  readonly entity: Readonly<{
    position: SafePosition;
    velocity: SafePosition;
  }>;
  readonly game: { dimension: string } = { dimension: 'overworld' };
  readonly _client = Object.freeze({
    socket: Object.freeze({ destroy: () => undefined })
  });
  health = 20;
  food = 20;
  targetDigBlock: unknown = null;
  usingHeldItem = false;
  players: Record<string, PlayerRecord> = {};
  terrain: TerrainMode = 'safe';
  mutationAttempts = 0;
  forbiddenCalls = 0;
  clearCalls = 0;
  forwardActivations = 0;
  forwardEnabled = false;
  lookTargets: unknown[] = [];
  blockQueries: Array<Readonly<{ x: number; y: number; z: number }>> = [];
  throwOnClear = false;
  autoEndOnQuit = true;

  #ownerX: number;
  #ownerY: number;
  #ownerZ: number;
  #ownerPresent = true;
  #duplicateOwner = false;

  constructor(
    botX = 0,
    botY = 64,
    botZ = 0,
    ownerX = 8,
    ownerY = 64,
    ownerZ = botZ
  ) {
    super();
    this.#ownerX = ownerX;
    this.#ownerY = ownerY;
    this.#ownerZ = ownerZ;
    this.entity = Object.freeze({
      position: trappedVector(
        { x: botX, y: botY, z: botZ },
        () => {
          this.mutationAttempts += 1;
        }
      ),
      velocity: trappedVector(
        { x: 0, y: 0, z: 0 },
        () => {
          this.mutationAttempts += 1;
        }
      )
    });
    this.#refreshPlayers();
  }

  ownerPosition(): SafePosition | undefined {
    return this.players.owner?.entity.position;
  }

  setOwnerX(value: number): void {
    this.#ownerX = value;
    this.#ownerPresent = true;
    this.#refreshPlayers();
  }

  hideOwner(): void {
    this.#ownerPresent = false;
    this.#refreshPlayers();
  }

  setDuplicateOwner(active: boolean): void {
    this.#duplicateOwner = active;
    this.#refreshPlayers();
  }

  clearControlStates(): void {
    this.clearCalls += 1;
    this.forwardEnabled = false;
    if (this.throwOnClear) throw new Error('control clear failed');
  }

  setControlState(control: string, active: boolean): void {
    if (control === 'jump' || control === 'sprint') {
      this.forbiddenCalls += 1;
      throw new Error(`forbidden control: ${control}`);
    }
    assert.equal(control, 'forward');
    assert.equal(active, true);
    this.forwardActivations += 1;
    this.forwardEnabled = true;
  }

  stopDigging(): void {}

  deactivateItem(): void {}

  quit(): void {
    if (this.autoEndOnQuit) this.emit('end');
  }

  end(): void {
    this.emit('end');
  }

  async lookAt(target: unknown): Promise<void> {
    this.lookTargets.push(target);
  }

  blockAt(position: unknown): DirectSteeringBlock | null {
    const candidate = position as Readonly<{
      x?: unknown;
      y?: unknown;
      z?: unknown;
    }>;
    const x = candidate.x;
    const y = candidate.y;
    const z = candidate.z;
    if (
      typeof x !== 'number' ||
      typeof y !== 'number' ||
      typeof z !== 'number'
    ) {
      return null;
    }
    this.blockQueries.push(Object.freeze({ x, y, z }));
    if (this.terrain === 'unloaded') return null;
    const sweptCorner = x === 1 && z === 0;
    if (y === 63) {
      if (
        this.terrain === 'drop' ||
        (this.terrain === 'swept_corner_drop' && sweptCorner)
      ) {
        return AIR;
      }
      if (this.terrain === 'partial') {
        return block('stone_slab', 'block', [[0, 0, 0, 1, 0.5, 1]]);
      }
      return STONE;
    }
    if (y === 64) {
      if (sweptCorner) {
        if (this.terrain === 'swept_corner_blocked') return STONE;
        if (this.terrain === 'swept_corner_hazard') {
          return block('cactus', 'block', FULL_CUBE);
        }
      }
      if (
        z === -1 &&
        (this.terrain === 'edge_hazard' || this.terrain === 'edge_blocked')
      ) {
        return this.terrain === 'edge_hazard'
          ? block('cactus', 'block', FULL_CUBE)
          : STONE;
      }
      if (this.terrain === 'blocked') return STONE;
      if (this.terrain === 'hazard') return block('cactus', 'block', FULL_CUBE);
      if (this.terrain === 'liquid') return block('water', 'empty', []);
      if (this.terrain === 'unknown') {
        return block('unknown_modded_plant', 'empty', []);
      }
    }
    if (
      y === 65 &&
      sweptCorner &&
      this.terrain === 'swept_corner_head_blocked'
    ) {
      return STONE;
    }
    return AIR;
  }

  dig(): never {
    return this.#forbidden('dig');
  }

  placeBlock(): never {
    return this.#forbidden('placeBlock');
  }

  attack(): never {
    return this.#forbidden('attack');
  }

  activateBlock(): never {
    return this.#forbidden('activateBlock');
  }

  openContainer(): never {
    return this.#forbidden('openContainer');
  }

  openChest(): never {
    return this.#forbidden('openChest');
  }

  openFurnace(): never {
    return this.#forbidden('openFurnace');
  }

  chat(): never {
    return this.#forbidden('chat');
  }

  equip(): never {
    return this.#forbidden('equip');
  }

  teleport(): never {
    return this.#forbidden('teleport');
  }

  #forbidden(name: string): never {
    this.forbiddenCalls += 1;
    throw new Error(`forbidden Mineflayer method: ${name}`);
  }

  #refreshPlayers(): void {
    const ownerPosition = Object.freeze({
      x: this.#ownerX,
      y: this.#ownerY,
      z: this.#ownerZ
    });
    this.players = {
      stranger: Object.freeze({
        username: 'Stranger',
        entity: Object.freeze({
          position: Object.freeze({ x: 1, y: 64, z: 1 })
        })
      })
    };
    if (this.#ownerPresent) {
      this.players.owner = Object.freeze({
        username: 'NeEtY',
        entity: Object.freeze({ position: ownerPosition })
      });
    }
    if (this.#duplicateOwner) {
      this.players.duplicate = Object.freeze({
        username: 'neety',
        entity: Object.freeze({
          position: Object.freeze({ x: this.#ownerX + 1, y: 64, z: 0 })
        })
      });
    }
  }
}

type PlayerRecord = Readonly<{
  username: string;
  entity: Readonly<{ position: SafePosition }>;
}>;

type ProductionHarness = Readonly<{
  bot: SafetyTrapBot;
  adapter: MineflayerMinecraftAdapter;
  executor: CompanionExecutor;
  deadline: (milliseconds: number) => number;
  advance: (milliseconds: number) => void;
  tick: () => Promise<void>;
  close: () => Promise<void>;
}>;

type ProductionHarnessOptions = Readonly<{
  botX?: number;
  botY?: number;
  botZ?: number;
  ownerX?: number;
  ownerY?: number;
  ownerZ?: number;
}>;

async function productionHarness(
  options: ProductionHarnessOptions = {}
): Promise<ProductionHarness> {
  const bot = new SafetyTrapBot(
    options.botX,
    options.botY,
    options.botZ,
    options.ownerX,
    options.ownerY,
    options.ownerZ
  );
  const adapter = new MineflayerMinecraftAdapter({
    createBot: () => bot,
    createVector: (x, y, z) => Object.freeze({ x, y, z })
  });
  const config = loadMinecraftSidecarRuntimeConfig({
    SHANA_MINECRAFT_CONTROL_TOKEN: 'safety-control-token-4Km8',
    SHANA_MINECRAFT_OWNER_USERNAME: 'NEETY'
  });
  const connection = adapter.connect(config, new AbortController().signal);
  bot.emit('spawn');
  await connection;

  let wallMs = Date.parse('2026-07-11T18:00:00.000Z');
  let monotonicMs = 1_000;
  const executor = new CompanionExecutor(adapter, config.minecraftOwnerUsername, {
    now: () => new Date(wallMs),
    monotonicNowMs: () => monotonicMs
  });
  executor.synchronizeState('IDLE');

  return Object.freeze({
    bot,
    adapter,
    executor,
    deadline: (milliseconds) => wallMs + milliseconds,
    advance: (milliseconds) => {
      wallMs += milliseconds;
      monotonicMs += milliseconds;
    },
    tick: async () => {
      bot.emit('physicsTick');
      await Promise.resolve();
      await Promise.resolve();
    },
    close: async () => {
      executor.cancelActiveMovement();
      await adapter.disconnect();
    }
  });
}

function block(
  name: string,
  boundingBox: string,
  shapes: unknown
): DirectSteeringBlock {
  return Object.freeze({ name, boundingBox, shapes });
}

function trappedVector(
  initial: SafePosition,
  onAssignment: () => void
): SafePosition {
  const value: Record<string, unknown> = {};
  for (const coordinate of ['x', 'y', 'z'] as const) {
    Object.defineProperty(value, coordinate, {
      enumerable: true,
      configurable: false,
      get: () => initial[coordinate],
      set: () => {
        onAssignment();
        throw new Error(`entity ${coordinate} mutation is forbidden`);
      }
    });
  }
  return value as SafePosition;
}

function assertTrapProof(bot: SafetyTrapBot): void {
  assert.equal(bot.mutationAttempts, 0, 'entity position/velocity was not assigned');
  assert.equal(bot.forbiddenCalls, 0, 'forbidden Mineflayer method was not called');
  assert.equal(bot.forwardEnabled, false, 'controls finish cleared');
}

const FORBIDDEN_MODULES = new Set([
  'mineflayer-pathfinder',
  'child_process',
  'node:child_process'
]);
const FORBIDDEN_PROPERTIES = new Set([
  'pathfinder',
  'setGoal',
  'goto',
  'GoalFollow',
  'GoalNear'
]);
const FORBIDDEN_CALLS = new Set([
  'setGoal',
  'goto',
  'GoalFollow',
  'GoalNear',
  'dig',
  'placeBlock',
  'attack',
  'activateBlock',
  'openContainer',
  'openChest',
  'openFurnace',
  'chat',
  'equip',
  'teleport',
  'eval',
  'jump',
  'sprint'
]);

let cachedProductionSafetyViolations: readonly string[] | undefined;

function scanProductionSafetyViolations(): string[] {
  if (cachedProductionSafetyViolations !== undefined) {
    return [...cachedProductionSafetyViolations];
  }
  const violations: string[] = [];
  const api = new API({ cwd: SIDECAR_ROOT });
  let snapshot: Snapshot | undefined;
  try {
    const configPath = join(SIDECAR_ROOT, 'tsconfig.json');
    snapshot = api.updateSnapshot({ openProjects: [configPath] });
    const project = snapshot.getProject(configPath);
    assert.notEqual(project, undefined, 'TypeScript safety scan project must load');
    for (const path of typescriptFiles(SOURCE_ROOT)) {
      const source = project?.program.getSourceFile(path);
      assert.notEqual(source, undefined, `TypeScript AST must load ${path}`);
      if (source === undefined) continue;
      const report = (node: ts.Node, detail: string): void => {
        const location = source.getLineAndCharacterOfPosition(node.getStart(source));
        violations.push(
          `${relative(SIDECAR_ROOT, path)}:${location.line + 1}:${location.character + 1} ${detail}`
        );
      };
      const visit = (node: ts.Node): void => {
        if (
          (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
          node.moduleSpecifier !== undefined &&
          ts.isStringLiteralLikeNode(node.moduleSpecifier) &&
          FORBIDDEN_MODULES.has(node.moduleSpecifier.text)
        ) {
          report(node, `forbidden module ${node.moduleSpecifier.text}`);
        }
        if (
          ts.isImportEqualsDeclaration(node) &&
          ts.isExternalModuleReference(node.moduleReference) &&
          node.moduleReference.expression !== undefined &&
          ts.isStringLiteralLikeNode(node.moduleReference.expression) &&
          FORBIDDEN_MODULES.has(node.moduleReference.expression.text)
        ) {
          report(node, `forbidden module ${node.moduleReference.expression.text}`);
        }

        if (ts.isPropertyAccessExpression(node)) {
          if (FORBIDDEN_PROPERTIES.has(node.name.text)) {
            report(node, `forbidden property access .${node.name.text}`);
          }
        } else if (ts.isElementAccessExpression(node)) {
          const property = stringValue(node.argumentExpression);
          if (property !== undefined && FORBIDDEN_PROPERTIES.has(property)) {
            report(node, `forbidden property access [${property}]`);
          }
        }

        if (ts.isCallExpression(node)) {
          const name = memberName(node.expression);
          if (name !== undefined && FORBIDDEN_CALLS.has(name)) {
            report(node, `forbidden call ${name}()`);
          }
          if (
            name === 'require' ||
            node.expression.kind === ts.SyntaxKind.ImportKeyword
          ) {
            const moduleName = stringValue(node.arguments[0]);
            if (moduleName !== undefined && FORBIDDEN_MODULES.has(moduleName)) {
              report(node, `forbidden dynamic module ${moduleName}`);
            }
          }
          if (name === 'setControlState') {
            const control = stringValue(node.arguments[0]);
            if (control === 'jump' || control === 'sprint') {
              report(node, `forbidden ${control} control`);
            }
          }
          if (
            name === 'assign' &&
            node.arguments[0] !== undefined &&
            isForbiddenEntityMutationTarget(node.arguments[0])
          ) {
            report(
              node,
              'forbidden Object.assign entity position/velocity mutation'
            );
          }
          for (const argument of node.arguments) {
            const command = stringValue(argument);
            if (
              command !== undefined &&
              /(?:^|\s)\/?(?:tp|teleport)(?:\s|$)/iu.test(command)
            ) {
              report(argument, 'forbidden teleport command string');
            }
          }
        }

        if (
          ts.isNewExpression(node) &&
          ts.isIdentifier(node.expression) &&
          node.expression.text === 'Function'
        ) {
          report(node, 'forbidden new Function');
        }

        if (
          ts.isBinaryExpression(node) &&
          isAssignmentOperator(node.operatorToken.kind) &&
          isForbiddenEntityMutationTarget(node.left)
        ) {
          report(node, 'forbidden entity position/velocity assignment');
        }
        if (
          (ts.isPrefixUnaryExpression(node) ||
            ts.isPostfixUnaryExpression(node)) &&
          (node.operator === ts.SyntaxKind.PlusPlusToken ||
            node.operator === ts.SyntaxKind.MinusMinusToken) &&
          isForbiddenEntityMutationTarget(node.operand)
        ) {
          report(node, 'forbidden entity position/velocity increment');
        }

        if (ts.isPropertyAssignment(node)) {
          const name = propertyName(node.name);
          if (name === 'jump' || name === 'sprint') {
            report(node, `forbidden ${name} control property`);
          }
        }
        node.forEachChild(visit);
      };
      visit(source);
    }
  } finally {
    snapshot?.dispose();
    api.close();
  }
  cachedProductionSafetyViolations = Object.freeze(violations.sort());
  return [...cachedProductionSafetyViolations];
}

function typescriptFiles(root: string): string[] {
  const found: string[] = [];
  const walk = (directory: string): void => {
    const entries: Dirent[] = readdirSync(directory, { withFileTypes: true });
    for (const entry of entries) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (entry.isFile() && path.endsWith('.ts') && !path.endsWith('.d.ts')) {
        found.push(path);
      }
    }
  };
  walk(root);
  return found.sort();
}

function memberName(expression: ts.Expression): string | undefined {
  if (ts.isIdentifier(expression)) return expression.text;
  if (ts.isPropertyAccessExpression(expression)) return expression.name.text;
  if (ts.isElementAccessExpression(expression)) {
    return stringValue(expression.argumentExpression);
  }
  return undefined;
}

function propertyName(name: ts.PropertyName): string | undefined {
  if (ts.isIdentifier(name) || ts.isStringLiteralLikeNode(name)) return name.text;
  return undefined;
}

function stringValue(node: ts.Node | undefined): string | undefined {
  return node !== undefined && ts.isStringLiteralLikeNode(node) ? node.text : undefined;
}

function isForbiddenEntityMutationTarget(node: ts.Node): boolean {
  const path = memberPath(node);
  if (path === undefined) return false;
  const entity = path.lastIndexOf('entity');
  if (entity < 0) return false;
  const remainder = path.slice(entity + 1);
  if (remainder.length === 1) {
    return remainder[0] === 'position' || remainder[0] === 'velocity';
  }
  return (
    remainder.length === 2 &&
    (remainder[0] === 'position' || remainder[0] === 'velocity') &&
    (remainder[1] === 'x' || remainder[1] === 'y' || remainder[1] === 'z')
  );
}

function memberPath(node: ts.Node): string[] | undefined {
  if (
    ts.isParenthesizedExpression(node) ||
    ts.isAsExpression(node) ||
    ts.isTypeAssertion(node) ||
    ts.isNonNullExpression(node)
  ) {
    return memberPath(node.expression);
  }
  if (ts.isThisExpression(node)) return ['this'];
  if (ts.isIdentifier(node)) return [node.text];
  if (ts.isPrivateIdentifier(node)) return [`#${node.text}`];
  if (ts.isPropertyAccessExpression(node)) {
    const parent = memberPath(node.expression);
    return parent === undefined ? undefined : [...parent, node.name.text];
  }
  if (ts.isElementAccessExpression(node)) {
    const parent = memberPath(node.expression);
    const property = stringValue(node.argumentExpression);
    return parent === undefined || property === undefined
      ? undefined
      : [...parent, property];
  }
  return undefined;
}

function isAssignmentOperator(kind: ts.SyntaxKind): boolean {
  return ts.isAssignmentOperator(kind);
}
