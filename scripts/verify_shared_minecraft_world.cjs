/* Independent automated owner actor for one explicitly approved disposable world.
 * No server is created here, and this script never sends Minecraft chat or controls.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const readline = require('node:readline');
const { createRequire } = require('node:module');
const assert = require('node:assert/strict');

const boundedPosition = p => p && ['x', 'y', 'z'].every(k => Number.isFinite(p[k]))
  && Math.abs(p.x) <= 12 && p.y >= 63 && p.y <= 68 && Math.abs(p.z) <= 12;
const copyPosition = p => ({ x: p.x, y: p.y, z: p.z });
const hash = raw => crypto.createHash('sha256').update(raw).digest('hex');

function validate(config) {
  assert.equal(config.approved, true, 'An explicit approved runner is required.');
  assert.equal(config.host, '127.0.0.1');
  assert.ok(Number.isInteger(config.port) && config.port > 1024 && config.port <= 65535);
  assert.equal(config.version, '1.21.11');
  assert.equal(config.owner, 'P6Owner');
  assert.equal(config.companion, 'P6Shana');
  assert.ok(path.isAbsolute(config.dependencyPackage));
  assert.ok(Array.isArray(config.scenarios) && config.scenarios.length === 6);
  for (const scene of config.scenarios) {
    assert.ok(/^[a-z0-9_]+$/.test(scene.id));
    assert.ok(boundedPosition(scene.layout.start) && boundedPosition(scene.layout.owner));
    assert.ok(scene.layout.wall === null || boundedPosition(scene.layout.wall));
  }
  return config;
}

function selfTest() {
  assert.equal(boundedPosition({x: 0.5, y:64, z: 10.5}), true);
  assert.equal(boundedPosition({x: Infinity, y:64, z:0}), false);
  assert.equal(boundedPosition({x: 100, y:64, z:0}), false);
  assert.throws(() => validate({approved:false}));
  console.log(JSON.stringify({self_test:true, no_network:true, automated_owner:true}));
}

async function main() {
  const input = readline.createInterface({input:process.stdin, crlfDelay:Infinity});
  let configured = false, bot, config, Vec3;
  const send = value => process.stdout.write(JSON.stringify(value) + '\n');
  let queue = Promise.resolve();
  const cleanup = () => { if (bot) { bot.clearControlStates(); bot.quit(); } };
  process.on('SIGTERM', () => { cleanup(); setTimeout(() => process.exit(0), 1000).unref(); });
  process.on('SIGINT', () => { cleanup(); setTimeout(() => process.exit(0), 1000).unref(); });
  input.on('close', cleanup);
  input.on('line', line => {
    queue = queue.then(async () => {
      assert.ok(line.length <= 24000);
      const request = JSON.parse(line);
      if (!configured) {
        config = validate(request);
        configured = true;
        const installed = createRequire(config.dependencyPackage);
        const mineflayer = installed('mineflayer');
        Vec3 = installed('vec3').Vec3;
        assert.equal(installed('mineflayer/package.json').version, '4.37.1');
        bot = mineflayer.createBot({host:config.host, port:config.port, version:config.version,
          username:config.owner, auth:'offline', profilesFolder:false, chat:'disabled',
          defaultChatPatterns:false, hideErrors:true, logErrors:false});
        bot.on('error', () => send({event:'actor_error'}));
        bot.on('end', () => send({event:'actor_ended'}));
        await new Promise((resolve,reject) => {
          const timeout=setTimeout(() => reject(new Error('spawn_timeout')),30000);
          bot.once('spawn', () => {clearTimeout(timeout);resolve();});
          bot.once('error', () => {clearTimeout(timeout);reject(new Error('spawn_failed'));});
        });
        send({event:'actor_spawned', automated_owner:true, version:bot.version});
        return;
      }
      assert.ok(/^[A-Za-z0-9_-]{1,80}$/.test(request.request_id));
      if (request.action === 'close') {
        cleanup(); send({request_id:request.request_id,ok:true});return;
      }
      assert.equal(request.action, 'probe');
      const scene=config.scenarios.find(value => value.id===request.scenario_id);
      assert.ok(scene);
      const companion=bot.players[config.companion]?.entity;
      const positions={owner:copyPosition(bot.entity.position),
        companion:companion ? copyPosition(companion.position) : null};
      const cells=[];
      const z=Math.floor(scene.layout.start.z);
      for(let x=Math.floor(scene.layout.start.x);x<=Math.ceil(scene.layout.owner.x);x++) {
        for(const y of [63,64,65]) {
          const block=bot.blockAt(new Vec3(x,y,z));
          cells.push({x,y,z,name:block?.name ?? null});
        }
      }
      const view={scenario_id:scene.id, positions, cells, at:Date.now()/1000,
        automated_owner:true, evidence_kind:'independent_owner_client_server_state'};
      send({request_id:request.request_id,ok:true,view,sha256:hash(JSON.stringify(view))});
    }).catch(() => {send({event:'probe_failed'});cleanup();process.exitCode=1;input.close();});
  });
}

if (process.argv.includes('--self-test')) selfTest();
else if (process.argv.includes('--approved-owner-actor')) main();
else { console.error('Use --self-test or the approved Python runner.');process.exitCode=2; }
