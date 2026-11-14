/* Execute the actual dashboard module against controlled browser and network events. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor() { this.listeners = {}; this.children = []; this.checked = false; this.disabled = false;
    this.hidden = false; this.open = false; this.value = ''; this.textContent = ''; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  async fire(name, extra = {}) { for (const callback of this.listeners[name] || []) await callback({preventDefault() {}, ...extra}); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}

function browser(options = {}) {
  let sequence = 0;
  const elements = new Map(), calls = [], storage = new Map(), intervals = [], notifications = [], registrations = [];
  const document = new Element(); document.hidden = false;
  document.getElementById = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  document.createElement = document.createDocumentFragment = () => new Element();
  const window = new Element();
  const state = {identity_id: 'same-identity', awake: true, device: {availability: 'available'},
    policy: {enabled: false, authority_expires_at: 0}, messages: []};
  let inputHandler = async () => { throw new Error('connection lost after request'); };
  let pollMessage = null;
  const context = {
    document, window, console, AbortController, Uint8Array,
    crypto: {randomUUID: () => `request-${++sequence}`, getRandomValues: a => a.fill(7)},
    btoa: value => Buffer.from(value, 'binary').toString('base64'),
    sessionStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    setTimeout: () => 1, clearTimeout() {}, setInterval: fn => intervals.push(fn),
    Notification: class { static permission = options.permission || 'granted'; static async requestPermission() { return options.permission || 'granted'; }
      constructor(title, data) { if (options.constructorThrows) throw new TypeError('Mobile constructor is unsupported'); notifications.push({title, ...data}); } close() {} },
    fetch: async (url, options) => {
      const action = url.split('/').at(-1).split('?')[0], body = options.body ? JSON.parse(options.body) : {};
      calls.push({action, body, signal: options.signal, credentials: options.credentials});
      let value;
      if (action === 'input') value = await inputHandler(body);
      else if (action === 'register') value = {identity_id: 'same-identity', device: {epoch: 0}};
      else if (action === 'connect') value = {identity_id: 'same-identity', device: {epoch: 1}};
      else if (action === 'state') value = state;
      else if (action === 'conversation') value = {turns: [{user: '<img src=x onerror=steal()>', assistant: 'Safe text.'}]};
      else if (action === 'poll') { value = {message: pollMessage}; pollMessage = null; }
      else if (action === 'result') value = {status: 'completed'};
      else if (action === 'devices') value = {devices: [{id: 'lost-device', label: '<img src=x>', last_seen_at: 1, connected: false, revoked: false}]};
      else value = {ok: true};
      return {ok: true, json: async () => value};
    }
  };
  if (options.worker) context.navigator = {serviceWorker: {register: async (script, settings) => {
    registrations.push({script, scope: settings.scope, updateViaCache: settings.updateViaCache});
    if (options.registrationFails) throw new Error('Worker registration unavailable');
    return {active: {state: 'activated'}, getNotifications: async () => [],
      showNotification: async (title, data) => {
        if (options.showFails) throw new Error('OS alert failed');
        notifications.push({title, ...data, bridge: 'service-worker'});
      }};
  }}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/gamma/dashboard/static/phone.js'), 'utf8'), context);
  return {el: document.getElementById, calls, storage, intervals, notifications, registrations, document, window, state,
    setInputHandler: fn => { inputHandler = fn; }, setPoll: value => { pollMessage = value; }};
}

const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  const b = browser();
  assert.equal(b.calls.length, 0, 'no private API before explicit confirmation and connect');
  await b.el('phoneNotifications').fire('click');
  assert.equal(b.notifications.length, 0);
  b.el('phonePrivate').checked = true; await b.el('phonePrivate').fire('change');
  assert.equal(b.calls.length, 0, 'confirmation alone does not connect');
  await b.el('phoneConnect').fire('click');
  assert.equal(b.calls.filter(c => c.action === 'register').length, 1);
  assert.equal(b.calls.filter(c => c.action === 'connect').length, 1);
  assert.ok(b.calls.every(c => c.credentials === 'same-origin'));
  assert.equal(b.el('phoneContent').hidden, false);
  const conversation = b.el('phoneHistory').children[0];
  assert.equal(conversation.children[0].children[1].textContent, '<img src=x onerror=steal()>', 'untrusted content stays literal text');
  assert.ok(!('innerHTML' in conversation));
  b.el('phoneText').value = 'Send this once.';
  await b.el('phoneForm').fire('submit');
  assert.equal(b.calls.filter(c => c.action === 'input').length, 1);
  assert.match(b.el('phoneInputStatus').textContent, /unconfirmed/);
  assert.ok(b.storage.get('shana-phone-pending-v1'));
  assert.ok(![...b.storage.values()].some(value => value.includes('Send this once.')), 'no conversation text cached in browser storage');
  await b.el('phoneForm').fire('submit');
  for (const interval of b.intervals) interval();
  await flush(); await flush();
  assert.equal(b.calls.filter(c => c.action === 'input').length, 1, 'no automatic replay after an uncertain send');
  await b.el('phoneCheck').fire('click');
  assert.equal(b.calls.filter(c => c.action === 'result').length, 1);
  assert.equal(b.storage.has('shana-phone-pending-v1'), false);
  await b.el('phoneNotifications').fire('click');
  b.state.messages = [{id: 'reminder-1', text: 'Private workshop details', status: 'received'}];
  b.setPoll({id: 'reminder-1', text: 'Private workshop details'});
  for (const interval of b.intervals) interval();
  await flush(); await flush();
  assert.equal(b.notifications.length, 1);
  assert.equal(b.notifications[0].title, 'Shana');
  assert.equal(b.notifications[0].body, 'You have a private update.');
  assert.match(b.notifications[0].tag, /^shana-private-/);
  assert.ok(!JSON.stringify(b.notifications).includes('Private workshop details'));
  assert.ok(b.calls.some(c => c.action === 'receipt' && c.body.status === 'received'));
  b.el('phonePrivate').checked = false; await b.el('phonePrivate').fire('change'); await flush();
  assert.equal(b.el('phoneContent').hidden, true);
  assert.equal(b.el('phoneHistory').children.length, 0);
  assert.equal(b.el('phoneUpdates').children.length, 0);

  const late = browser();
  late.el('phonePrivate').checked = true; await late.el('phonePrivate').fire('change'); await late.el('phoneConnect').fire('click');
  let release;
  late.setInputHandler(() => new Promise(resolve => { release = resolve; }));
  late.el('phoneText').value = 'Late answer must stay hidden.';
  const send = late.el('phoneForm').fire('submit');
  await flush();
  late.el('phonePrivate').checked = false; await late.el('phonePrivate').fire('change');
  release({status: 'completed', response: {display_text: 'Late private reply.'}});
  await send; await flush();
  assert.equal(late.el('phoneHistory').children.length, 0, 'late private output stays hidden after privacy loss');
  assert.equal(late.el('phoneContent').hidden, true);
  assert.ok(late.calls.find(c => c.action === 'input').signal.aborted);
  assert.equal(late.calls.filter(c => c.action === 'input').length, 1);
  assert.ok(late.calls.some(c => c.action === 'control' && c.body.action === 'disconnect'));
  const manage = browser();
  await manage.el('phoneLoadDevices').fire('click');
  assert.equal(manage.calls.length, 0, 'device management also waits for private confirmation');
  manage.el('phonePrivate').checked = true; await manage.el('phonePrivate').fire('change');
  await manage.el('phoneLoadDevices').fire('click');
  assert.equal(manage.calls.filter(c => c.action === 'register').length, 0, 'lost-key management works before device registration');
  const listed = manage.el('phoneDeviceList').children[0].children[0];
  assert.equal(listed.children[0].textContent, '<img src=x>');
  await listed.children[2].fire('click');
  const revoked = manage.calls.find(c => c.action === 'revoke-device');
  assert.equal(revoked.body.device_id, 'lost-device');
  assert.ok(!('device_secret' in revoked.body) && !('epoch' in revoked.body));
  assert.equal(manage.calls.filter(c => c.action === 'connect').length, 0);
  const mobile = browser({worker: true, constructorThrows: true});
  await mobile.el('phoneNotifications').fire('click');
  assert.equal(mobile.registrations.length, 0, 'no worker or permission flow before private connection');
  mobile.el('phonePrivate').checked = true; await mobile.el('phonePrivate').fire('change'); await mobile.el('phoneConnect').fire('click');
  await mobile.el('phoneNotifications').fire('click');
  assert.deepEqual(mobile.registrations, [{script: '/dashboard/phone-worker.js', scope: '/dashboard/phone', updateViaCache: 'none'}]);
  mobile.state.messages = [{id: 'mobile-reminder', text: 'Never put this private text in an OS alert.', status: 'received'}];
  mobile.setPoll({id: 'mobile-reminder', text: 'Never put this private text in an OS alert.'});
  for (const interval of mobile.intervals) interval();
  await flush(); await flush();
  assert.equal(mobile.notifications.length, 1);
  assert.equal(mobile.notifications[0].bridge, 'service-worker');
  assert.equal(mobile.notifications[0].body, 'You have a private update.');
  assert.ok(!('data' in mobile.notifications[0]));
  assert.equal(mobile.el('phoneContent').hidden, false);
  for (const failure of [{constructorThrows: true}, {worker: true, registrationFails: true}, {worker: true, showFails: true}]) {
    const broken = browser(failure);
    broken.el('phonePrivate').checked = true; await broken.el('phonePrivate').fire('change'); await broken.el('phoneConnect').fire('click');
    await broken.el('phoneNotifications').fire('click');
    broken.state.messages = [{id: 'failed-alert', text: 'The private message remains in page.', status: 'received'}];
    broken.setPoll({id: 'failed-alert', text: 'The private message remains in page.'});
    for (const interval of broken.intervals) interval();
    await flush(); await flush();
    assert.equal(broken.el('phoneContent').hidden, false, 'alert failure must not disconnect conversation');
    assert.equal(broken.el('phoneState').textContent, 'Connected');
    assert.match(broken.el('phoneNotificationStatus').textContent, /unavailable/);
    broken.setInputHandler(async () => ({status: 'completed'}));
    broken.el('phoneText').value = 'Chat still works after an alert failure.';
    await broken.el('phoneForm').fire('submit');
    assert.equal(broken.calls.filter(c => c.action === 'input').length, 1);
  }
  const denied = browser({worker: true, permission: 'denied'});
  denied.el('phonePrivate').checked = true; await denied.el('phonePrivate').fire('change'); await denied.el('phoneConnect').fire('click');
  await denied.el('phoneNotifications').fire('click');
  assert.equal(denied.registrations.length, 0, 'denied permission never installs a worker');
  assert.equal(denied.el('phoneContent').hidden, false);
  console.log(JSON.stringify({passed: true, actual_module: 'phone.js', controlled_browser: true,
    no_private_api_before_confirmation: true, no_uncertain_replay: true, generic_notifications: true,
    late_output_withheld: true, physical_android: false}));
})().catch(error => { console.error(error); process.exitCode = 1; });
