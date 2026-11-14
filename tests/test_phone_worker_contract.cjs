/* Actual notification-only worker, controlled clients; no browser or OS permission. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function worker(existing = []) {
  const listeners = {}, calls = [];
  const self = {location: {origin: 'https://private-review.example'},
    addEventListener: (name, callback) => { listeners[name] = callback; },
    skipWaiting: async () => { calls.push('skipWaiting'); },
    clients: {claim: async () => { calls.push('claim'); },
      matchAll: async () => existing,
      openWindow: async url => { calls.push({open: url}); }}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/gamma/dashboard/static/phone_worker.js'), 'utf8'),
    {self, URL, fetch: () => { throw new Error('No network is permitted in this worker contract'); }});
  return {listeners, calls};
}

(async () => {
  const first = worker();
  assert.deepEqual(Object.keys(first.listeners).sort(), ['activate', 'install', 'notificationclick']);
  let operation;
  first.listeners.install({waitUntil: promise => { operation = promise; }}); await operation;
  first.listeners.activate({waitUntil: promise => { operation = promise; }}); await operation;
  first.listeners.notificationclick({notification: {data: {url: 'https://attacker.example/steal'}, close() {}},
    waitUntil: promise => { operation = promise; }});
  await operation;
  assert.deepEqual(first.calls, ['skipWaiting', 'claim', {open: 'https://private-review.example/dashboard/phone'}]);
  let focused = 0;
  const second = worker([{url: 'https://private-review.example/dashboard/work', focus: async () => { throw new Error('Wrong page'); }},
    {url: 'https://private-review.example/dashboard/phone', focus: async () => { focused++; }}]);
  second.listeners.notificationclick({notification: {close() {}}, waitUntil: promise => { operation = promise; }});
  await operation;
  assert.equal(focused, 1);
  assert.equal(second.calls.length, 0);
  console.log(JSON.stringify({passed: true, actual_module: 'phone_worker.js', controlled_worker: true,
    no_push_or_fetch_handler: true, fixed_same_origin_destination: true, physical_android: false}));
})().catch(error => { console.error(error); process.exitCode = 1; });
