const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

async function main() {
  const elements = {
    monitorInputMode: {value: 'private_text'}, monitorPrivateAudience: {value: 'owner_private'},
    monitorInputSession: {value: 'private'}, monitorInputText: {value: 'keep my input'},
    monitorInputStatus: {textContent: ''}, subtitleDisplay: {textContent: ''}
  };
  const audienceHandlers = {};
  elements.monitorPrivateAudience.addEventListener = (name, fn) => { audienceHandlers[name] = fn; };
  const requests = [];
  let generation = 0;
  let state = 'network_error';
  let replyGate = null;
  const window = {GAMMA_PRIVATE_COORDINATION: true,
    setTimeout() { throw Error('legacy status polling started'); },
    setInterval() { throw Error('legacy interval started'); }};
  const sandbox = {window, URL, URLSearchParams, location: new URL('http://127.0.0.1:12345'),
    document: {body: {classList: {remove() {}, add() {}}}, getElementById: id => elements[id] || null,
      querySelectorAll: () => []},
    localStorage: {getItem: () => null, setItem() {}},
    crypto: {randomUUID: () => `request-${++generation}`},
    console, fetch: async (url, options) => {
      assert.equal(url, '/api/monitor/input');
      requests.push(JSON.parse(options.body));
      if (state === 'network_error') throw Error('uncertain transport');
      if (replyGate) await replyGate;
      return {ok: true, json: async () => ({ok: true, result: {
        generation: 1, decision: {decision: state === 'complete' ? 'reply' : 'defer'},
        assistant_response: {delivery_mode: state === 'complete' ? 'text_only' : 'deferred',
          reason_code: state, display_text: state === 'complete' ? '<private text>' : ''}
      }})};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/gamma/dashboard/static/monitor.js'), 'utf8'), sandbox);
  assert.equal(requests.length, 0);
  await window.submitMonitorInput();
  assert.equal(elements.monitorInputText.value, 'keep my input');
  state = 'pending';
  await window.submitMonitorInput();
  assert.equal(requests[0].request_id, requests[1].request_id);
  assert.equal(elements.monitorInputText.value, 'keep my input');
  state = 'complete';
  await window.submitMonitorInput();
  assert.equal(requests[1].request_id, requests[2].request_id);
  assert.equal(requests[2].synthesize_speech, false);
  assert.equal(requests[2].output_target_policy, 'dashboard_monitor');
  assert.equal(elements.monitorInputText.value, '');
  assert.equal(elements.subtitleDisplay.textContent, '<private text>');
  elements.monitorInputText.value = 'held private input';
  let release;
  replyGate = new Promise(resolve => { release = resolve; });
  const held = window.submitMonitorInput();
  await Promise.resolve();
  const heldId = requests.at(-1).request_id;
  const count = requests.length;
  elements.monitorInputText.value = 'new private draft';
  await window.submitMonitorInput();
  assert.equal(requests.length, count, 'serialize private input instead of replacing an uncertain request');
  elements.monitorPrivateAudience.value = 'unknown'; audienceHandlers.change();
  assert.equal(elements.subtitleDisplay.textContent, '');
  assert.equal(elements.monitorInputText.hidden, true);
  release(); await held; replyGate = null;
  assert.equal(elements.subtitleDisplay.textContent, '', 'late private reply is withheld after audience loss');
  assert.equal(elements.monitorInputText.value, 'new private draft');
  elements.monitorPrivateAudience.value = 'owner_private'; audienceHandlers.change();
  assert.equal(elements.monitorInputText.hidden, false);
  elements.monitorInputText.value = 'held private input';
  await window.submitMonitorInput();
  assert.equal(requests.at(-1).request_id, heldId, 'private retry retains the accepted request across audience changes');
  elements.monitorInputText.value = 'do not send this';
  elements.monitorPrivateAudience.value = 'unknown'; audienceHandlers.change();
  const prior = requests.length; await window.submitMonitorInput();
  assert.equal(requests.length, prior, 'unconfirmed private input is never sent');
  console.log('Private Monitor preserves uncertain/deferred input and request identity, renders text only, and starts no legacy connections.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
