import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { loadMinecraftSidecarRuntimeConfig } from '../src/config.js';
import {
  MinecraftAdapterError,
  MineflayerMinecraftAdapter
} from '../src/mineflayer-runtime.js';

const TOKEN = 'adapter-control-token-2Qw7';
const RAW_SENTINEL = 'raw-server-kick-secret-9X';

class FakeBot extends EventEmitter {
  version: unknown = '1.21.11';
  entity = { position: { x: 1.6, y: 63.5, z: -2.6 } };
  game = { dimension: 'overworld' as unknown };
  health: unknown = 18.5;
  food: unknown = 17;
  targetDigBlock: unknown = null;
  usingHeldItem = false;
  clearCount = 0;
  stopDiggingCount = 0;
  deactivateCount = 0;
  quitCount = 0;
  endCount = 0;
  destroyCount = 0;
  emitEndOnQuit = false;
  readonly _client = {
    socket: {
      destroy: () => {
        this.destroyCount += 1;
      }
    }
  };

  clearControlStates(): void {
    this.clearCount += 1;
  }

  stopDigging(): void {
    this.stopDiggingCount += 1;
  }

  deactivateItem(): void {
    this.deactivateCount += 1;
  }

  quit(): void {
    this.quitCount += 1;
    if (this.emitEndOnQuit) this.emit('end', 'fixed-test-end');
  }

  end(): void {
    this.endCount += 1;
  }
}

function config() {
  return loadMinecraftSidecarRuntimeConfig({
    SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN,
    SHANA_MINECRAFT_SERVER_HOST: '127.9.8.7',
    SHANA_MINECRAFT_SERVER_PORT: '25566',
    SHANA_MINECRAFT_VERSION: '1.21.11',
    SHANA_MINECRAFT_ACCOUNT_MODE: 'offline',
    SHANA_MINECRAFT_BOT_USERNAME: 'Shana_Test'
  });
}

test('adapter construction is inactive and connect uses only exact safe offline options', async () => {
  const bot = new FakeBot();
  const options: Array<Readonly<Record<string, unknown>>> = [];
  const forcedCloses: Array<() => void> = [];
  let unrefCount = 0;
  const adapter = new MineflayerMinecraftAdapter({
    createBot: (value) => {
      options.push(value);
      return bot;
    },
    setTimeout: (callback, milliseconds) => {
      assert.equal(milliseconds, 1_000);
      forcedCloses.push(callback);
      return {
        unref: () => {
          unrefCount += 1;
        }
      };
    }
  });
  assert.equal(options.length, 0);
  assert.equal(adapter.state().connectionState, 'disconnected');

  const events: string[] = [];
  adapter.setEventHandler((event) => events.push(event.type));
  const controller = new AbortController();
  const connected = adapter.connect(config(), controller.signal);
  assert.equal(options.length, 1);
  assert.deepEqual(options[0], {
    host: '127.9.8.7',
    port: 25_566,
    username: 'Shana_Test',
    version: '1.21.11',
    auth: 'offline',
    profilesFolder: false,
    chat: 'disabled',
    defaultChatPatterns: false,
    hideErrors: true,
    logErrors: false,
    respawn: true
  });
  assert.equal(Object.hasOwn(options[0] ?? {}, 'password'), false);
  assert.equal(adapter.state().connectionState, 'connecting');
  assert.deepEqual(events, ['connecting']);

  let resolved = false;
  void connected.then(() => {
    resolved = true;
  });
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(resolved, false);
  bot.emit('spawn');
  await connected;
  assert.deepEqual(events, ['connecting', 'spawned']);
  assert.deepEqual(adapter.state(), {
    connectionState: 'connected',
    spawned: true,
    alive: true,
    negotiatedVersion: '1.21.11',
    dimension: 'minecraft:overworld',
    roundedPosition: { x: 2, y: 64, z: -3 },
    health: 18.5,
    hunger: 17,
    lastDisconnectCategory: null
  });

  adapter.stopAllControls();
  assert.equal(bot.clearCount, 1);
  assert.equal(bot.stopDiggingCount, 0);
  assert.equal(bot.deactivateCount, 0);
  bot.targetDigBlock = {};
  bot.usingHeldItem = true;
  adapter.stopAllControls();
  assert.equal(bot.clearCount, 2);
  assert.equal(bot.stopDiggingCount, 1);
  assert.equal(bot.deactivateCount, 1);

  const disconnect = adapter.disconnect();
  assert.equal(forcedCloses.length, 1);
  forcedCloses[0]?.();
  await disconnect;
  await adapter.disconnect();
  assert.equal(bot.quitCount, 1);
  assert.equal(unrefCount, 1);
  assert.equal(bot.destroyCount, 1);
  assert.equal(adapter.state().connectionState, 'disconnected');
  assert.equal(adapter.state().lastDisconnectCategory, 'requested');
  assert.equal(events.at(-1), 'disconnected');
});

test('factory, error, kick, end, and abort failures stay categorized and secret-free', async () => {
  const factoryEvents: string[] = [];
  const throwing = new MineflayerMinecraftAdapter({
    createBot: () => {
      throw new Error(RAW_SENTINEL);
    }
  });
  throwing.setEventHandler((event) => factoryEvents.push(event.type));
  await assert.rejects(
    throwing.connect(config(), new AbortController().signal),
    (error: unknown) =>
      error instanceof MinecraftAdapterError &&
      error.category === 'connection_failed' &&
      !String(error).includes(RAW_SENTINEL)
  );
  assert.deepEqual(factoryEvents, ['connecting', 'error', 'disconnected']);

  for (const event of ['error', 'kicked', 'end'] as const) {
    const bot = new FakeBot();
    bot.emitEndOnQuit = true;
    const events: string[] = [];
    const adapter = new MineflayerMinecraftAdapter({
      createBot: () => bot,
      setTimeout: () => ({ unref: () => undefined })
    });
    adapter.setEventHandler((value) => events.push(value.type));
    const pending = adapter.connect(config(), new AbortController().signal);
    if (event === 'error') bot.emit(event, new Error(RAW_SENTINEL));
    else if (event === 'kicked') bot.emit(event, RAW_SENTINEL, true);
    else bot.emit(event, RAW_SENTINEL);
    await assert.rejects(
      pending,
      (error: unknown) =>
        error instanceof MinecraftAdapterError &&
        !String(error).includes(RAW_SENTINEL)
    );
    assert.equal(JSON.stringify(events).includes(RAW_SENTINEL), false);
    assert.equal(adapter.state().connectionState, 'disconnected');
  }

  const bot = new FakeBot();
  bot.emitEndOnQuit = true;
  const adapter = new MineflayerMinecraftAdapter({
    createBot: () => bot,
    setTimeout: () => ({ unref: () => undefined })
  });
  const controller = new AbortController();
  const pending = adapter.connect(config(), controller.signal);
  controller.abort();
  await assert.rejects(
    pending,
    (error: unknown) =>
      error instanceof MinecraftAdapterError && error.category === 'aborted'
  );
  assert.equal(bot.clearCount >= 1, true);
  assert.equal(bot.quitCount, 1);
});

test('death, raw respawn, later spawn, health, dimensions, and stale events are bounded', async () => {
  const first = new FakeBot();
  const second = new FakeBot();
  first.emitEndOnQuit = true;
  second.emitEndOnQuit = true;
  const bots = [first, second];
  const callbacks: Array<() => void> = [];
  const adapter = new MineflayerMinecraftAdapter({
    createBot: () => bots.shift(),
    setTimeout: (callback) => {
      callbacks.push(callback);
      return { unref: () => undefined };
    }
  });
  const events: string[] = [];
  adapter.setEventHandler((event) => events.push(event.type));
  const firstConnect = adapter.connect(config(), new AbortController().signal);
  first.emit('spawn');
  await firstConnect;
  first.emit('death');
  assert.equal(adapter.state().alive, false);
  assert.equal(events.at(-1), 'death');
  first.game.dimension = 'minecraft:the_nether';
  first.emit('respawn');
  assert.equal(events.at(-1), 'death');
  first.health = 20;
  first.food = 20;
  first.emit('spawn');
  assert.equal(events.at(-1), 'respawn');
  assert.equal(adapter.state().alive, true);
  assert.equal(adapter.state().dimension, 'minecraft:the_nether');
  first.game.dimension = 'custom:moon';
  first.emit('health');
  assert.equal(adapter.state().dimension, null);
  assert.equal(events.at(-1), 'health');

  await adapter.disconnect();
  const countAfterDisconnect = events.length;
  first.emit('death');
  first.on('error', () => undefined);
  first.emit('error', new Error(RAW_SENTINEL));
  assert.equal(events.length, countAfterDisconnect);

  const secondConnect = adapter.connect(config(), new AbortController().signal);
  second.emit('spawn');
  await secondConnect;
  assert.equal(adapter.state().dimension, 'minecraft:overworld');
  assert.equal(callbacks.length, 0);
  await adapter.disconnect();
});
