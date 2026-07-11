import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_CONTROL_WEBSOCKET_URL,
  DEFAULT_HEARTBEAT_SECONDS,
  loadMinecraftSidecarConfig
} from '../src/config.js';

const TOKEN = 'config-test-token-8Ks2';

test('configuration uses conservative defaults and deterministic generated instance IDs', () => {
  const config = loadMinecraftSidecarConfig(
    { SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN },
    { createInstanceId: () => 'generated-id' }
  );
  assert.deepEqual(config, {
    controlWebSocketUrl: DEFAULT_CONTROL_WEBSOCKET_URL,
    controlToken: TOKEN,
    sidecarInstanceId: 'sidecar-generated-id',
    heartbeatSeconds: DEFAULT_HEARTBEAT_SECONDS
  });
  assert.equal(Object.isFrozen(config), true);
});

test('configuration accepts explicit bounded environment-only values', () => {
  const config = loadMinecraftSidecarConfig({
    SHANA_MINECRAFT_CONTROL_WEBSOCKET_URL:
      'ws://127.20.30.40:9123/v1/minecraft/control',
    SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN,
    SHANA_MINECRAFT_SIDECAR_INSTANCE_ID: 'sidecar.dev:one',
    SHANA_MINECRAFT_HEARTBEAT_SECONDS: '60'
  });
  assert.equal(config.controlWebSocketUrl, 'ws://127.20.30.40:9123/v1/minecraft/control');
  assert.equal(config.sidecarInstanceId, 'sidecar.dev:one');
  assert.equal(config.heartbeatSeconds, 60);
});

test('missing and malformed tokens fail without exposing their values', () => {
  for (const token of [undefined, '', ' ', ` ${TOKEN}`, `${TOKEN} `, `${TOKEN}\nvalue`]) {
    let rendered = '';
    assert.throws(
      () => loadMinecraftSidecarConfig({ SHANA_MINECRAFT_CONTROL_TOKEN: token }),
      (error: unknown) => {
        rendered = String(error);
        return true;
      }
    );
    assert.equal(rendered.includes(TOKEN), false);
  }
});

test('configuration delegates the exact loopback URL policy', () => {
  for (const url of [
    'ws://localhost:8000/v1/minecraft/control',
    'ws://192.168.1.2:8000/v1/minecraft/control',
    'wss://127.0.0.1:8000/v1/minecraft/control',
    'ws://127.0.0.1:8000/v1/minecraft/control?token=blocked'
  ]) {
    assert.throws(
      () =>
        loadMinecraftSidecarConfig({
          SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN,
          SHANA_MINECRAFT_CONTROL_WEBSOCKET_URL: url
        }),
      /control URL is invalid/u
    );
  }
});

test('heartbeat accepts only canonical decimal integers from 1 through 60', () => {
  for (const value of ['1', '5', '59', '60']) {
    assert.equal(
      loadMinecraftSidecarConfig({
        SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN,
        SHANA_MINECRAFT_HEARTBEAT_SECONDS: value
      }).heartbeatSeconds,
      Number(value)
    );
  }
  for (const value of ['0', '61', '-1', '+1', '01', '1.0', '1e1', ' 5', '5 ']) {
    assert.throws(
      () =>
        loadMinecraftSidecarConfig({
          SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN,
          SHANA_MINECRAFT_HEARTBEAT_SECONDS: value
        }),
      /heartbeat interval/u
    );
  }
});

test('instance IDs are canonical, bounded, secret-free, and generator failures are safe', () => {
  for (const value of ['', ' leading', 'space value', '-leading', 'x'.repeat(129)]) {
    assert.throws(
      () =>
        loadMinecraftSidecarConfig({
          SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN,
          SHANA_MINECRAFT_SIDECAR_INSTANCE_ID: value
        }),
      /instance ID is invalid/u
    );
  }
  assert.throws(
    () =>
      loadMinecraftSidecarConfig(
        { SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN },
        {
          createInstanceId: () => {
            throw new Error(`raw-${TOKEN}`);
          }
        }
      ),
    (error: unknown) => !String(error).includes(TOKEN)
  );
  assert.throws(
    () =>
      loadMinecraftSidecarConfig({
        SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN,
        SHANA_MINECRAFT_SIDECAR_INSTANCE_ID: `sidecar-${TOKEN}`
      }),
    /instance ID is invalid/u
  );
});
