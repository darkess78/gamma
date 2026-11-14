'use strict';
// Controlled lifecycle checks. This is not visual Brave or OSRS acceptance.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
const tick = async () => {for (let n = 0; n < 5; n++) await new Promise(resolve => setImmediate(resolve));};
function deferred() {let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve};}
function fixture() {
  const nodes = new Map(), windows = new Map(), ready = [], timers = new Map(), calls = [], notices = [], feed = [], captures = [];
  let timerId = 0, clock = 1000000, nextId = 0, handler, selectedSource = null, serverEpoch = 1, watchRow;
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, {id, checked: false, disabled: false, value: 'osrs_screen', hidden: false, textContent: '',
      videoWidth: 640, videoHeight: 360, children: [], handlers: new Map(), classList: {toggle() {}},
      addEventListener(name, fn) {this.handlers.set(name, fn);}, append(...x) {this.children.push(...x);},
      replaceChildren() {this.children = [];}});
    return nodes.get(id);
  }
  const track = {label: 'Authored private fixture', stopped: false, handlers: new Map(), getSettings: () => ({displaySurface: 'window'}),
    stop() {this.stopped = true;}, addEventListener(name, fn) {this.handlers.set(name, fn);}};
  const stream = {getVideoTracks: () => [track], getTracks: () => [track]};
  const document = {hidden: false, getElementById: element, querySelectorAll: () => [],
    addEventListener(name, fn) {if (name === 'DOMContentLoaded') ready.push(fn);},
    createElement(tag) {assert.equal(tag, 'canvas'); return {getContext: () => ({drawImage() {captures.push(clock);}}),
      toDataURL: () => 'data:image/jpeg;base64,YWN0dWFsLWNhbnZhcy1ieXRlcw=='};}};
  const window = {addEventListener(name, fn) {windows.set(name, fn);}, ShanaUI: {id: () => String(++nextId),
    notice(node, text) {node.textContent = text; notices.push(text);}, feed(node, text) {feed.push(text); node.children.unshift({textContent:text});},
    async request(path, values, options) {
      calls.push({path, values, options});
      if (handler) {const result = handler(path, values, options); if (result !== undefined) return result;}
      if (path.includes('/minecraft/')) throw new Error('No world connected');
      if (path.includes('/status')) return {image_analysis_available: true, watch_available: true, state: {enabled: true, source_epoch: serverEpoch, source_id: selectedSource},
        context: {shared_goal: '', mode: 'relaxed', observation: {summary: 'An authored test panel'}}};
      if (values?.action === 'select') {selectedSource = values.values.source_id; return {source_epoch: ++serverEpoch};}
      if (values?.action === 'stop') return {source_epoch: ++serverEpoch};
      if (path.endsWith('/moment/received')) {assert(element('sharingReactions').children.length, 'Receipt before DOM text.'); return {status:'consumer_reported_received'};}
      if (path.endsWith('/watch/start')) {watchRow = {id:'watch-'+'a'.repeat(32), lease:'b'.repeat(32), status:'active', minutes:values.minutes,
        remaining_moments:10, consumed_moments:0, expires_at:clock/1000+values.minutes*60, next_sample_at:clock/1000}; return watchRow;}
      if (path.endsWith('/watch/heartbeat')) return {...watchRow, availability_expires_at:clock/1000+45};
      if (path.endsWith('/watch/stop')) return {...watchRow, status:'stopped', reason:'owner_stopped_watch'};
      throw new Error('Unexpected request ' + path);
    }}};
  const speechCalls = [];
  window.ShanaChannelAudioPlayer = class {
    constructor(options) {this.options=options;this.enabled=false;this.connected=false;}
    async enable(context) {this.context=context;this.enabled=this.connected=true;speechCalls.push({kind:'enable',context});this.options.update({enabled:true});}
    async disable() {this.enabled=this.connected=false;speechCalls.push({kind:'disable',private:element('sharingPrivate').checked,checked:element('sharingListen').checked});this.options.update({enabled:false});}
    async renderMoment(id) {assert(element('sharingReactions').children.length);assert(calls.some(c=>c.path.endsWith('/moment/received')&&c.values.consumer_id));speechCalls.push({kind:'render',id});}
  };
  class FakeDate extends Date {static now() {return clock;}}
  const sandbox = {document, window, navigator: {mediaDevices: {getDisplayMedia: async () => stream}},
    AbortController, AbortSignal, DOMException, Date: FakeDate, console,
    setTimeout(fn, delay) {const id = ++timerId; timers.set(id, {fn, delay}); return id;},
    clearTimeout(id) {timers.delete(id);}};
  vm.runInNewContext(source, sandbox);
  ready.forEach(fn => fn());
  return {nodes, windows, calls, notices, feed, captures, timers, track, element, speechCalls,
    setHandler(fn) {handler = fn;},
    async start() {element('sharingPrivate').checked = true; element('sharingPrivate').handlers.get('change')(); await tick();
      await element('sharingStart').handlers.get('click')(); await tick();},
    async listen() {element('sharingListen').checked=true;await element('sharingListen').handlers.get('change')();await tick();},
    frame() {return element('sharingLiveFrame').handlers.get('click')();},
    moment() {return element('sharingFrame').handlers.get('click')();},
    watch() {element('sharingWatchMinutes').value = '5'; return element('sharingWatchStart').handlers.get('click')();},
    async timer(delay) {const entry = [...timers].find(([, value]) => value.delay === delay); assert(entry, 'missing timer ' + delay);
      timers.delete(entry[0]); clock += delay; entry[1].fn(); await tick();},
    imageCalls() {return calls.filter(call => call.path.endsWith('/image'));},
    reviewCalls() {return calls.filter(call => call.path.endsWith('/review'));},
    async privacyOff() {element('sharingPrivate').checked = false; element('sharingPrivate').handlers.get('change')(); await tick();},
  };
}
async function renewal() {
  const f = fixture(), analysis = deferred(), review = deferred(); let images = 0;
  f.setHandler(path => {if (path.endsWith('/image')) return ++images === 1 ? analysis.promise : {status: images === 2 ? 'refresh_received' : 'refreshed'};
    if (path.endsWith('/review')) return review.promise;});
  await f.start(); const running = f.frame(); await tick();
  await f.timer(2000); assert.equal(f.imageCalls().length, 2); assert.equal(f.reviewCalls().length, 0);
  const packets = f.imageCalls().map(call => call.values);
  assert.equal(packets[1].metadata.sequence, packets[0].metadata.sequence + 1);
  assert.equal(packets[1].metadata.captured_at - packets[0].metadata.captured_at, 2);
  assert.equal(f.captures.length, 2, 'Each renewal must capture the actual selected video again.');
  assert(packets.every(packet => packet.image_base64 && packet.media_type === 'image/jpeg'));
  analysis.resolve({status: 'interpreted'}); await tick(); assert.equal(f.reviewCalls().length, 1);
  await f.timer(2000); assert.equal(f.imageCalls().length, 3); assert.equal(f.reviewCalls().length, 1);
  review.resolve({status: 'reacted', text: 'I can see the test panel.'}); await running; await tick();
  assert.deepEqual(f.feed, ['I can see the test panel.']); assert.equal(f.timers.size, 0);
  assert(f.imageCalls().every(call => call.options.signal.aborted));
  assert.equal(f.track.stopped, false, 'A completed moment keeps the selected window available locally.');
}
async function oneRefresh() {
  const f = fixture(), analysis = deferred(), refresh = deferred(); let images = 0;
  f.setHandler(path => {if (path.endsWith('/image')) return ++images === 1 ? analysis.promise : refresh.promise;});
  await f.start(); const running = f.frame(); await tick();
  await f.timer(2000); assert.equal(f.imageCalls().length, 2);
  assert.equal([...f.timers.values()].filter(t => t.delay === 2000).length, 0, 'Refreshes must not overlap.');
  await f.privacyOff(); analysis.resolve({status: 'interpreted'}); refresh.resolve({status: 'refreshed'});
  await running; await tick(); assert.equal(f.reviewCalls().length, 0); assert.equal(f.timers.size, 0);
}
async function noUninterpretedReview() {
  const f = fixture(); f.setHandler(path => {if (path.endsWith('/image')) return {status: 'busy'};});
  await f.start(); const running = f.frame(); await tick(); await f.timer(2000);
  assert.equal(f.reviewCalls().length, 0); await f.privacyOff(); await running;
  assert.equal(f.timers.size, 0);
}
async function privacyPendingReview() {
  const f = fixture(), review = deferred();
  f.setHandler(path => {if (path.endsWith('/image')) return {status: 'interpreted'};
    if (path.endsWith('/review')) return review.promise;});
  await f.start(); const running = f.frame(); await tick(); assert.equal(f.reviewCalls().length, 1);
  await f.privacyOff(); review.resolve({status: 'reacted', text: 'Late private content'}); await running;
  assert.deepEqual(f.feed, []); assert.equal(f.timers.size, 0); assert(f.track.stopped);
  assert(f.reviewCalls()[0].options.signal.aborted);
  assert(f.calls.some(call => call.values?.action === 'stop'));
  assert.equal(f.element('sharingObservation').textContent, 'Sharing stopped.');
}
async function pagehide() {
  const f = fixture(), analysis = deferred(); f.setHandler(path => path.endsWith('/image') ? analysis.promise : undefined);
  await f.start(); const running = f.frame(); await tick(); f.windows.get('pagehide')(); await tick();
  assert.equal(f.timers.size, 0); assert(f.track.stopped);
  assert(f.calls.some(call => call.values?.action === 'stop' && call.options.keepalive));
  analysis.resolve({status: 'interpreted'}); await running; assert.equal(f.reviewCalls().length, 0);
}
async function timeout() {
  const f = fixture(), analysis = deferred(); f.setHandler(path => path.endsWith('/image') ? analysis.promise : undefined);
  await f.start(); const running = f.frame(); await tick(); await f.timer(45000);
  assert(f.track.stopped); assert.equal(f.timers.size, 0); assert(f.imageCalls()[0].options.signal.aborted);
  analysis.resolve({status: 'interpreted'}); await running; assert.equal(f.reviewCalls().length, 0);
}
async function goalCancels() {
  const f = fixture(), analysis = deferred(); f.setHandler((path, values) => {
    if (path.endsWith('/image')) return analysis.promise;
    if (values && ['goal', 'mode'].includes(values.action)) return {status: 'saved'};
  });
  await f.start(); const running = f.frame(); await tick();
  await f.element('sharingGoalForm').handlers.get('submit')({preventDefault() {}}); await tick();
  assert.equal(f.timers.size, 0); analysis.resolve({status: 'interpreted'}); await running;
  assert.equal(f.reviewCalls().length, 0);
}
async function historicalMoment() {
  const f = fixture(), result = deferred();
  f.setHandler(path => path.endsWith('/moment') ? result.promise : undefined);
  await f.start(); const running = f.moment(); await tick();
  assert.equal(f.captures.length, 1); assert.equal(f.imageCalls().length, 0); assert.equal(f.reviewCalls().length, 0);
  assert.equal([...f.timers.values()].filter(t => t.delay === 2000).length, 0);
  const packet = f.calls.find(c => c.path.endsWith('/moment')).values;
  result.resolve({id:'historical',status:'generated',text:'About the captured view.',text_sha256:'a'.repeat(64),
    captured_at:packet.metadata.captured_at,age_seconds:21,summary:'An authored past application view.'});
  await running; await tick();
  assert.deepEqual(f.feed, ['About the captured view.']); assert.equal(f.captures.length, 1);
  assert(f.element('sharingObservation').textContent.includes('21 seconds before this result'));
  assert(f.element('sharingObservation').textContent.includes('Current state is unknown'));
  assert.equal(f.calls.filter(c => c.path.endsWith('/moment/received')).length, 1);
  assert.equal(f.timers.size,0); assert(!f.track.stopped);
}
async function historicalPrivacy() {
  const f=fixture(), delayed=deferred(); f.setHandler(path=>path.endsWith('/moment')?delayed.promise:undefined);
  await f.start(); const running=f.moment(); await tick(); await f.privacyOff();
  delayed.resolve({id:'private',status:'generated',text:'Must not enter DOM',text_sha256:'a'.repeat(64),captured_at:1000,age_seconds:20});
  await running; assert.deepEqual(f.feed,[]); assert.equal(f.timers.size,0);
  assert.equal(f.calls.filter(c=>c.path.endsWith('/moment/received')).length,0);
}
async function watchLifecycle() {
  const f=fixture(), delayed=deferred();
  f.setHandler(path=>path.endsWith('/watch/moment')?delayed.promise:undefined);
  await f.start(); await f.watch(); assert.equal(f.captures.length,0);
  const selected=f.calls.filter(c=>c.values?.action==='select');
  assert.equal(selected.length,2); assert.equal(selected[1].values.values.seconds,300);
  assert.equal(selected[1].values.values.maximum_reviews,10);
  await f.timer(1000); assert.equal(f.captures.length,1);
  assert.equal([...f.timers.values()].filter(t=>t.delay===1000).length,0,'Only one sample can be in flight.');
  await f.timer(10000);
  const heartbeat=f.calls.find(c=>c.path.endsWith('/watch/heartbeat'));
  assert(heartbeat && !heartbeat.values.image_base64 && !heartbeat.values.metadata);
  await f.element('sharingWatchStop').handlers.get('click')();
  assert(!f.track.stopped,'Stop watching keeps manual selection available.'); assert.equal(f.timers.size,0);
  delayed.resolve({moment:{status:'generated',text:'Late watch content'},watch:{status:'active'}});await tick();
  assert.deepEqual(f.feed,[]); assert.equal(f.timers.size,0);
}
async function watchBusy() {
  const f=fixture(); f.setHandler(path=>path.endsWith('/watch/moment')?{moment:{status:'busy'},
    watch:{id:'watch-'+'a'.repeat(32),lease:'b'.repeat(32),status:'active',minutes:5,remaining_moments:10,consumed_moments:0,next_sample_at:1031}}:undefined);
  await f.start(); await f.watch(); await f.timer(1000);
  assert.equal(f.captures.length,1);assert.equal(f.reviewCalls().length,0);
  assert([...f.timers.values()].some(t=>t.delay===30000),'Busy work backs off to server cadence.');
  await f.privacyOff();assert.equal(f.timers.size,0);assert(f.track.stopped);
}
async function watchDisconnect() {
  const f=fixture(); f.setHandler(path=>{if(path.endsWith('/watch/heartbeat'))throw new Error('controlled disconnected');});
  await f.start();await f.watch();await f.timer(10000);
  assert.equal(f.captures.length,0);assert.equal(f.timers.size,0);
  assert(f.calls.some(c=>c.path.endsWith('/watch/stop')));
  assert(f.element('sharingWatchStart').disabled===false,'A new owner Start remains possible; no automatic resume.');
}
async function watchPendingStartPrivacy() {
  const f=fixture(), late=deferred(); f.setHandler(path=>path.endsWith('/watch/start')?late.promise:undefined);
  await f.start();const starting=f.watch();await tick();await f.privacyOff();
  late.resolve({id:'watch-'+'a'.repeat(32),lease:'b'.repeat(32),status:'active',minutes:5});await starting;await tick();
  assert(f.track.stopped);assert.equal(f.timers.size,0);assert.equal(f.captures.length,0);
  assert(f.calls.some(c=>c.path.endsWith('/watch/stop')));
}
async function watchWindowEnded() {
  const f=fixture();await f.start();await f.watch();f.track.handlers.get('ended')();await tick();
  assert(f.track.stopped);assert.equal(f.timers.size,0);
  assert(f.calls.some(c=>c.values?.action==='stop'));
}
async function watchStopDuringStart() {
  const f=fixture(), late=deferred();f.setHandler(path=>path.endsWith('/watch/start')?late.promise:undefined);
  await f.start();const starting=f.watch();await tick();await f.element('sharingWatchStop').handlers.get('click')();
  late.resolve({id:'watch-'+'a'.repeat(32),lease:'b'.repeat(32),status:'active',minutes:5});await starting;await tick();
  assert(f.track.stopped);assert.equal(f.captures.length,0);assert.equal(f.timers.size,0);
  assert(f.calls.some(c=>c.path.endsWith('/watch/stop')));
}
async function receiptRetryDoesNotResendText() {
  const f=fixture();let receipts=0;
  f.setHandler(path=>{if(path.endsWith('/moment'))return{id:'once',status:'generated',text:'Only once.',text_sha256:'a'.repeat(64),captured_at:1000,age_seconds:20};
    if(path.endsWith('/moment/received')){if(++receipts===1)throw new Error('lost acknowledgment');return{status:'consumer_reported_received'};}});
  await f.start();await f.moment();assert.deepEqual(f.feed,['Only once.']);assert.equal(f.captures.length,1);
  const ack=f.calls.filter(c=>c.path.endsWith('/moment/received'));assert.equal(ack.length,2);
  assert.equal(ack[0].values.request_id,ack[1].values.request_id);assert.equal(f.reviewCalls().length,0);
}
async function watchPagehide() {
  const f=fixture();await f.start();await f.watch();f.windows.get('pagehide')();await tick();
  assert.equal(f.timers.size,0);assert(f.track.stopped);assert.equal(f.captures.length,0);
  assert(f.calls.some(c=>c.path.endsWith('/watch/stop')&&c.options.keepalive));
  assert(f.calls.some(c=>c.values?.action==='stop'&&c.options.keepalive));
}
async function finalWatchReceipt() {
  const f=fixture(),ack=deferred();let received=0;
  const exhausted={id:'watch-'+'a'.repeat(32),lease:'b'.repeat(32),status:'exhausted',reason:'watch_sample_budget_exhausted',
    minutes:5,remaining_moments:0,consumed_moments:10};
  f.setHandler(path=>{
    if(path.endsWith('/watch/moment'))return{moment:{id:'final',status:'generated',text:'The final moment.',text_sha256:'a'.repeat(64),captured_at:1001,age_seconds:20},watch:exhausted};
    if(path.endsWith('/watch/heartbeat'))return exhausted;
    if(path.endsWith('/moment/received')){if(++received===1)throw new Error('lost first ack');return ack.promise;}
  });
  await f.start();await f.watch();await f.timer(1000);assert.deepEqual(f.feed,['The final moment.']);
  await f.timer(10000);assert(!f.calls.some(c=>c.path.endsWith('/watch/stop')),'Final receipt was revoked while arriving.');
  ack.resolve({status:'consumer_reported_received'});await tick();
  assert.equal(f.captures.length,1);assert.equal(received,2);assert.equal(f.timers.size,0);
  assert(!f.calls.some(c=>c.path.endsWith('/watch/stop')));assert.deepEqual(f.feed,['The final moment.']);
}
async function momentListening() {
  const f=fixture();let received=false;
  f.setHandler((path,values)=>{
    if(path.endsWith('/moment'))return{id:'spoken',status:'generated',text:'A shared reflection.',text_sha256:'a'.repeat(64),captured_at:1000,age_seconds:20};
    if(path.endsWith('/moment/received')){received=true;assert(values.consumer_id.startsWith('moments-'));return{status:'consumer_reported_received',audio_consumer_bound:true};}
  });
  await f.start();assert(!f.element('sharingListen').checked);assert.equal(f.speechCalls.length,0);
  await f.listen();await f.moment();assert(received);assert.equal(f.speechCalls.filter(c=>c.kind==='render').length,1);
  await f.privacyOff();assert(f.speechCalls.some(c=>c.kind==='disable'&&!c.checked));assert(!f.element('sharingListen').checked);
}
async function uncertainMomentReceiptNoSpeech() {
  const f=fixture();
  f.setHandler(path=>{
    if(path.endsWith('/moment'))return{id:'uncertain',status:'generated',text:'Only displayed.',text_sha256:'a'.repeat(64)};
    if(path.endsWith('/moment/received'))throw new Error('lost receipt');
  });
  await f.start();await f.listen();await f.moment();
  assert.equal(f.speechCalls.filter(c=>c.kind==='render').length,0);
  assert.equal(f.calls.filter(c=>c.path.endsWith('/moment/received')).length,2);
  assert.equal(f.captures.length,1);await f.privacyOff();
}
async function explicitSingleMomentGrant() {
  const f=fixture();await f.start();await f.listen();
  let firstEpoch=f.calls.find(c=>c.values?.action==='select');
  f.setHandler(path=>{
    if(path.includes('/status'))return{image_analysis_available:true,watch_available:true,state:{enabled:true,source_id:firstEpoch.values.values.source_id,source_epoch:2,remaining_reviews:0,remaining_frames:0},context:{}};
    if(path.endsWith('/moment'))return{id:'fresh',status:'silent',text:''};
  });
  await f.moment();const grants=f.calls.filter(c=>c.values?.action==='select');assert.equal(grants.length,2);
  assert.equal(grants[1].values.values.maximum_reviews,1);assert.equal(grants[1].values.values.maximum_frames,1);
  assert.equal(grants[1].values.values.seconds,180);assert.equal(grants[1].values.values.expected_epoch,2);
  assert(!f.element('sharingListen').checked);assert.equal(f.captures.length,1);await f.privacyOff();
}
const cases = {renewal, oneRefresh, noUninterpretedReview, privacyPendingReview, pagehide, timeout, goalCancels,
  historicalMoment,historicalPrivacy,watchLifecycle,watchBusy,watchDisconnect,watchPendingStartPrivacy,watchWindowEnded,
  watchStopDuringStart,receiptRetryDoesNotResendText,watchPagehide,finalWatchReceipt,momentListening,uncertainMomentReceiptNoSpeech,explicitSingleMomentGrant};
(async () => {assert(cases[process.argv[3]]); await cases[process.argv[3]](); console.log(process.argv[3] + ': passed');})()
  .catch(error => {console.error(error); process.exitCode = 1;});
