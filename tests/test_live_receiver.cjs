const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const PlaybackRequests = require('../src/gamma/dashboard/static/playback-requests.js');
const LiveOffers = require('../src/gamma/dashboard/static/live-offers.js');
const sockets=[]; const fetched=[]; const released=[]; let plays=0;
const audio={pause(){},removeAttribute(){this.src='';},load(){},play(){plays++; this.onplaying?.(); return Promise.resolve();}};
class Socket {
  static OPEN=1;
  constructor(url){this.url=url;this.readyState=1;this.sent=[];sockets.push(this);}
  send(raw){this.sent.push(JSON.parse(raw));}
  close(){this.readyState=3; this.onclose?.();}
  receive(payload){this.onmessage({data:JSON.stringify(payload)});}
}
const elements={voicePlayback:audio};
const context={console,PlaybackRequests,LiveOffers,WebSocket:Socket,AbortController,
  setTimeout,clearTimeout,setInterval,clearInterval,URL:{revokeObjectURL:url=>released.push(url)},
  location:{protocol:'https:',host:'fixture.invalid',hostname:'fixture.invalid'},
  navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[]})}},
  localStorage:{getItem:()=>null},document:{getElementById:id=>elements[id]||null}};
context.window=context; context.isSecureContext=true;
vm.createContext(context);
vm.runInContext(fs.readFileSync(require.resolve('../src/gamma/dashboard/static/live.js'),'utf8'),context);
PlaybackRequests.loadDashboardAudio=async(receipt,signal)=>{fetched.push({receipt,signal});return 'blob:'+receipt.event_id;};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  context.toggleLiveVoice();
  const receiver=sockets[0]; receiver.receive({type:'ready',playback_lease:{lease_id:'lease'}});
  await flush(); const voice=sockets[1];
  const offer=(turn,id)=>({type:'speech_chunk_ready',source:'core_live',turn_id:turn,payload:{chunk_index:1,text:'fixture'},
    playback:{schema_version:1,lease_id:'lease',event_id:id,grant_id:'a'.repeat(32),audio_digest:'b'.repeat(64)}});
  receiver.receive(offer('foreign','foreign')); await flush(); assert.equal(fetched.length,0);
  receiver.receive(offer('mine','one')); voice.receive({type:'turn_submitting',turn_id:'mine'});
  await flush(); assert.equal(fetched.length,1); assert.equal(plays,0);
  assert.equal(receiver.sent.at(-1).type,'playback_prepare');
  receiver.receive({type:'playback_permit',lease_id:'lease',event_id:'one',expires_at:Date.now()/1000+4});
  await flush(); assert.equal(plays,1); assert.equal(audio.src,'blob:one');
  assert.equal(receiver.sent.at(-1).status,'started');
  receiver.receive({type:'playback_revoked',lease_id:'lease',event_id:'one'});
  assert.ok(released.includes('blob:one')); assert.equal(audio.src,'');
  assert.equal(receiver.sent.at(-1).status,'interrupted');
  voice.receive({type:'turn_submitting',turn_id:'next'}); receiver.receive(offer('next','two'));
  await flush(); assert.equal(fetched.length,2);
  receiver.close(); await flush();
  assert.equal(plays,1); assert.ok(fetched[1].signal.aborted); assert.ok(released.includes('blob:two'));
  assert.equal(voice.readyState,3);
  console.log('live receiver ownership, permit, receipts, revocation and disconnect checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;context.stopLiveVoice();});
