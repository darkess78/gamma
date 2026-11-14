/* Controlled JS execution of the actual HTTP-served review phone asset. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() {this.listeners = {}; this.children = []; this.checked = false; this.hidden = false; this.value = ''; this.textContent = '';}
  addEventListener(name, fn) {(this.listeners[name] ||= []).push(fn);}
  async fire(name) {for (const fn of this.listeners[name] || []) await fn({preventDefault() {}});}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children = nodes;}
}

(async () => {
  const normalDevice = JSON.stringify({device_id: 'normal-phone', secret: 'normal-secret-must-stay-normal', registration_id: 'normal-registration'});
  const storage = new Map([['shana-phone-device-v1', normalDevice], ['shana-phone-pending-v1', 'normal-pending-id']]);
  const reads = [], writes = [], calls = [], elements = new Map(); let id = 0;
  const document = new Element(); document.hidden = false;
  document.getElementById = key => {if (!elements.has(key)) elements.set(key, new Element()); return elements.get(key);};
  document.createElement = document.createDocumentFragment = () => new Element();
  const el = document.getElementById;
  const context = {document, window: new Element(), console, AbortController, Uint8Array,
    crypto: {randomUUID: () => `review-${++id}`, getRandomValues: value => value.fill(7)},
    btoa: value => Buffer.from(value, 'binary').toString('base64'),
    sessionStorage: {getItem: key => {reads.push(key); return storage.get(key) || null;},
      setItem: (key, value) => {writes.push(key); storage.set(key, value);},
      removeItem: key => {writes.push(key); storage.delete(key);}},
    setTimeout: () => 1, clearTimeout() {}, setInterval() {},
    fetch: async (url, options) => {
      const action = url.split('/').at(-1).split('?')[0], body = options.body ? JSON.parse(options.body) : {};
      calls.push({url, action, body});
      assert.ok(url.startsWith('/review/shana/api/coordination/phone/'));
      assert.ok(!JSON.stringify(body).includes('normal-secret-must-stay-normal'));
      if (action === 'input') throw new Error('uncertain fixture delivery');
      let value = {ok: true};
      if (action === 'register') value = {identity_id: 'review-identity', device: {epoch: 0}};
      if (action === 'connect') value = {identity_id: 'review-identity', device: {epoch: 1}};
      if (action === 'state') value = {identity_id: 'review-identity', awake: true, device: {availability: 'available'}, policy: {}, messages: []};
      if (action === 'conversation') value = {turns: []};
      if (action === 'poll') value = {message: null};
      if (action === 'result') value = {status: 'completed'};
      return {ok: true, json: async () => value};
    }};
  vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  assert.equal(calls.length, 0);
  el('phonePrivate').checked = true; await el('phonePrivate').fire('change');
  await el('phoneConnect').fire('click');
  const registration = calls.find(row => row.action === 'register');
  assert.ok(registration && registration.body.device_id !== 'normal-phone');
  assert.ok(storage.has('shana-review-shana-shana-phone-device-v1'));
  el('phoneText').value = 'A review-only message.'; await el('phoneForm').fire('submit');
  const pending = storage.get('shana-review-shana-shana-phone-pending-v1');
  assert.ok(pending && pending !== 'normal-pending-id');
  await el('phoneCheck').fire('click');
  assert.equal(calls.find(row => row.action === 'result').body.input_request_id, pending);
  await el('phoneForget').fire('click');
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(storage.has('shana-review-shana-shana-phone-device-v1'), false);
  assert.equal(storage.get('shana-phone-device-v1'), normalDevice);
  assert.equal(storage.get('shana-phone-pending-v1'), 'normal-pending-id');
  assert.ok(!reads.includes('shana-phone-device-v1') && !reads.includes('shana-phone-pending-v1'));
  assert.ok(!writes.includes('shana-phone-device-v1') && !writes.includes('shana-phone-pending-v1'));
  console.log('Actual served review phone asset: normal device/pending keys never read or written; fresh review identity, scoped requests and uncertain-result lookup passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
