/* Actual UI modules, controlled DOM/fetch/Audio events. No microphone or server. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{
 constructor(fragment=false){this.listeners={};this.children=[];this.checked=false;this.disabled=false;this.hidden=false;this.open=false;this.value='';this._text='';this.dataset={};this.style={};this.fragment=fragment;const classes=new Set();this.classList={toggle:n=>classes.has(n)?classes.delete(n):classes.add(n),contains:n=>classes.has(n)};}
 addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);}
 async fire(name,extra={}){for(const fn of this.listeners[name]||[])await fn({preventDefault(){},...extra});}
 append(...items){for(const item of items){if(item.fragment)this.children.push(...item.children);else this.children.push(item);}}
 replaceChildren(...items){this.children=[];this._text='';this.append(...items);}
 set textContent(text){this._text=String(text);this.children=[];}
 get textContent(){return this._text+this.children.map(c=>c.textContent||'').join('');}
 toggleAttribute(name){this[name]=!this[name];}
 hasAttribute(name){return Boolean(this[name]);}
}
const clone=value=>JSON.parse(JSON.stringify(value));
const drain=async()=>{for(let n=0;n<8;n++)await new Promise(resolve=>setImmediate(resolve));};
function deferred(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
function harness(kind){
 const elements=new Map(),calls=[],intervals=[],audios=[],storage=new Map();let sequence=0,consumer=null,recorder=null,clock=1000;
 const document=new Element();document.hidden=false;document.body=new Element();
 document.getElementById=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 document.createElement=()=>new Element();document.createDocumentFragment=()=>new Element(true);
 const window=new Element();window.isSecureContext=true;window.localStorage={getItem:()=>null,setItem(){}};
 const state={generation:1,output_epoch:1,awake:true,focus:'relaxed',voice_enabled:false,speech_enabled:true,
  identity_id:'same-identity',device:{availability:'available'},policy:{enabled:false,authority_expires_at:0},messages:[]};
 let outputs=[],history=[{id:'previous',user:'Earlier question.',assistant:'Earlier canonical reply.'}],inputHandler=null,stateGate=null,conversationGate=null,playedGate=null;
 class Audio{
  constructor(url){this.url=url;this.paused=false;audios.push(this);}
  async play(){this.onplaying?.();}
  pause(){this.paused=true;}
  removeAttribute(){}
  load(){}
 }
 class Capture{
  constructor(){this.sampleRate=16000;this.destination={};}
  createMediaStreamSource(){return {connect(){}};}
  createGain(){return {gain:{value:1},connect(){}};}
  createScriptProcessor(){recorder={onaudioprocess:null,connect(){},disconnect(){}};return recorder;}
  async resume(){}
  async close(){}
 }
 window.AudioContext=Capture;
 const context={document,window,console,Audio,AbortController,Uint8Array,DataView,ArrayBuffer,Float32Array,URLSearchParams,
  performance:{now:()=>clock},navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}},
  crypto:{randomUUID:()=> 'request-'+ ++sequence,getRandomValues:a=>a.fill(7)},
  btoa:s=>Buffer.from(s,'binary').toString('base64'),
  URL:{createObjectURL:()=> 'blob:sample-'+ ++sequence,revokeObjectURL(){}},
  localStorage:window.localStorage,
  sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  setTimeout:()=>1,clearTimeout(){},setInterval:(fn,ms)=>intervals.push({fn,ms}),
  fetch:async(url,options={})=>{
   const action=url.split('/').at(-1).split('?')[0],body=options.body?JSON.parse(options.body):{};
   calls.push({url,action,body,signal:options.signal});let value;
   const snapshot=()=>({...state,consumer:{id:consumer,connected:consumer!==null},outputs:clone(outputs)});
   if(url.includes('/audio/')){
    const output=outputs.find(o=>o.id===action);
    return {ok:Boolean(output?.audio_available&&['offered','started'].includes(output.status)),status:409,
      blob:async()=>({type:'audio/wav',size:100})};
   }
   if(action==='register')value={identity_id:'same-identity',device:{epoch:0}};
   else if(action==='connect'){consumer=body.consumer_id||'phone';value=kind==='presence'?snapshot():{identity_id:'same-identity',device:{epoch:1}};}
   else if(action==='disconnect'||body.action==='disconnect'){consumer=null;value={ok:true};}
   else if(action==='control'&&kind==='presence'){state[body.action]=body.value;value=snapshot();}
   else if(action==='state'){value=stateGate?await stateGate.promise:state;}
   else if(action==='status')value=snapshot();
   else if(action==='conversation'){value=conversationGate?await conversationGate.promise:{turns:history};}
   else if(action==='poll'){
    if(kind==='presence'){const output=outputs.find(o=>['generated','offered'].includes(o.status));if(output)output.status='offered';value={...state,output:output?clone(output):null};}
    else value={message:null};
   }
   else if(action==='input'||action==='audio-input')value=inputHandler?await inputHandler(body):{status:'silent'};
   else if(action==='ack'){
    if(body.status==='played'&&playedGate)await playedGate.promise;
    const output=outputs.find(o=>o.id===body.output_id);if(output)output.status=body.status;value={ok:true};
   }else value={ok:true};
   return {ok:true,json:async()=>clone(value)};
  }};
 vm.createContext(context);
 if(kind==='presence')vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/gamma/dashboard/static/presence_queue.js'),'utf8'),context);
 if(kind==='presence')context.window.ShanaPlaybackQueue=context.ShanaPlaybackQueue;
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/gamma/dashboard/static/'+(kind==='presence'?'private_presence.js':'phone.js')),'utf8'),context);
 return {kind,document,window,el:document.getElementById,calls,intervals,audios,storage,state,
  setOutputs:v=>outputs=v,setHistory:v=>history=v,setInput:fn=>inputHandler=fn,
  captureFrame:(level,at)=>{clock=at;recorder.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(4096).fill(level)}});},
  holdState:()=>stateGate=deferred(),clearState:()=>stateGate=null,
  holdConversation:()=>conversationGate=deferred(),clearConversation:()=>conversationGate=null,
  holdPlayed:()=>playedGate=deferred(),clearPlayed:()=>playedGate=null};
}
async function connected(kind){
 const b=harness(kind);assert.equal(b.calls.length,0,'No API before private confirmation');
 if(kind==='presence'){await b.document.fire('DOMContentLoaded');b.el('privateConfirm').checked=true;await b.el('privateConfirm').fire('change');}
 else{b.el('phonePrivate').checked=true;await b.el('phonePrivate').fire('change');await b.el('phoneConnect').fire('click');}
 await drain();return b;
}
const chunk=index=>({id:'output-'+String(index+1).repeat(32),generation:1,output_epoch:1,status:'generated',display_text:'Checked sentence '+index,
 audio_available:true,utterance_id:'utterance-one',chunk_index:index,chunk_count:2,final_chunk:index===1});
async function prepareChunks(){
 const b=await connected('presence');b.setOutputs([chunk(0),chunk(1)]);
 b.intervals[0].fn();await drain();assert.equal(b.audios.length,1);b.intervals[0].fn();await drain();
 assert.equal(b.audios.length,1,'Second chunk should wait for first played receipt');return b;
}
async function presence(){
 const b=await prepareChunks(),gate=b.holdPlayed();const end=b.audios[0].onended();await drain();
 assert.equal(b.audios.length,1,'No next playback before played acknowledgment');gate.resolve();await end;await drain();
 assert.equal(b.audios.length,2,'Queued second chunk must start without another400ms timer');
 assert.deepEqual(b.calls.filter(c=>c.action==='ack'&&c.body.output_id===chunk(0).id).map(c=>c.body.status),['started','played']);
 const stopped=await prepareChunks(),late=stopped.holdPlayed();const ending=stopped.audios[0].onended();await drain();
 stopped.el('privateConfirm').checked=false;await stopped.el('privateConfirm').fire('change');late.resolve();await ending;await drain();
 assert.equal(stopped.audios.length,1,'Late playedACK must not restart private output after privacy loss');
 const removed=await prepareChunks(),removedGate=removed.holdPlayed();const removedEnd=removed.audios[0].onended();await drain();
 removed.setOutputs([chunk(0),{...chunk(1),status:'expired',audio_available:false}]);removedGate.resolve();await removedEnd;await drain();
 assert.equal(removed.audios.length,1,'Server-revoked queued audio must not play through immediate drain');
 const text=await connected('presence');text.setInput(async()=>{
  text.setOutputs([{id:'output-'+'c'.repeat(32),generation:1,output_epoch:1,status:'generated',display_text:'Checked final output.',audio_available:false}]);
  return {status:'generated',result:{assistant_response:{display_text:'UNRELEASED_DRAFT',internal_summary:'UNRELEASED_DRAFT'}}};
 });text.el('privateText').value='A short question.';await text.el('textForm').fire('submit');await drain();
 assert.equal(text.el('caption').textContent,'Checked final output.','Inputreceipt must poll releasedoutput immediately, without timer');
 assert.ok(!text.el('caption').textContent.includes('UNRELEASED_DRAFT'));
 const mic=await connected('presence');mic.el('ownerSource').checked=true;mic.setInput(async()=>{mic.setOutputs([{id:'output-'+'d'.repeat(32),generation:1,output_epoch:1,status:'generated',display_text:'Checked microphone reply.',audio_available:false}]);return {status:'generated'};});
 await mic.el('voiceToggle').fire('click');await drain();
 mic.captureFrame(.1,1000);mic.captureFrame(0,1256);mic.captureFrame(0,1512);mic.captureFrame(0,1768);await drain();
 assert.equal(mic.calls.filter(c=>c.action==='audio-input').length,1);
 assert.equal(mic.el('caption').textContent,'Checked microphone reply.','Completed microphone receipt must poll releasedoutput immediately');
 return {queued_chunk_immediate:true,played_ack_order_preserved:true,late_privacy_fenced:true,revoked_source_fenced:true,checked_output_immediate_poll:true,microphone_receipt_immediate_poll:true};
}
async function phone(){
 const b=await connected('phone'),gate=b.holdState();const old=b.el('phoneHistory').textContent;
 b.setInput(async body=>{const turn={id:body.request_id,user:body.text,assistant:'Checked <img src=x onerror=steal()> reply.'};b.setHistory([{id:'previous',user:'Earlier question.',assistant:'Earlier canonical reply.'},turn]);
  return {request_id:body.request_id,status:'completed',identity_id:'same-identity',generation:1,response:{response_action:'reply',display_text:turn.assistant,internal_summary:'INTERNAL_NOT_FOR_DISPLAY'}};});
 b.el('phoneText').value='New question.';const send=b.el('phoneForm').fire('submit');await drain();
 try{
  assert.ok(b.el('phoneHistory').textContent.includes('Checked <img src=x onerror=steal()> reply.'),'Checked reply must display while state/history fetch is held');
  assert.ok(b.el('phoneHistory').textContent.includes(old),'Immediate checkedreply must preserve earlier history');
  assert.ok(!b.el('phoneHistory').textContent.includes('INTERNAL_NOT_FOR_DISPLAY'));
  assert.equal(b.el('phoneSend').disabled,false,'Completed send must not wait for backgroundstate refresh');
 }finally{b.clearState();gate.resolve(b.state);await send;await drain();}
 assert.equal(b.el('phoneHistory').textContent.split('Checked <img').length-1,1,'Canonical refresh must not duplicate immediate reply');
 b.setHistory([]);b.intervals[0].fn();await drain();assert.equal(b.el('phoneHistory').textContent,'','Fresh forgetting clears immediate checkedcontent');
 const silent=await connected('phone'),silentGate=silent.holdState();silent.setInput(async body=>({request_id:body.request_id,status:'completed',response:{response_action:'stay_silent',delivery_mode:'silent',display_text:'NOT_COMMUNICATED'}}));silent.el('phoneText').value='Thinking aloud.';const quiet=silent.el('phoneForm').fire('submit');await drain();assert.ok(!silent.el('phoneHistory').textContent.includes('NOT_COMMUNICATED'));silent.clearState();silentGate.resolve(silent.state);await quiet;await drain();
 const wrong=await connected('phone'),hold=wrong.holdState();wrong.setInput(async()=>({request_id:'wrong-request',status:'completed',response:{display_text:'ROGUE_RESPONSE'}}));wrong.el('phoneText').value='Anotherquestion.';
 const mismatch=wrong.el('phoneForm').fire('submit');await drain();assert.ok(!wrong.el('phoneHistory').textContent.includes('ROGUE_RESPONSE'));
 wrong.clearState();hold.resolve(wrong.state);await mismatch;await drain();
 const late=await connected('phone'),lateGate=deferred();late.setInput(()=>lateGate.promise);late.el('phoneText').value='Privatequestion.';const pending=late.el('phoneForm').fire('submit');await drain();const sent=late.calls.find(c=>c.action==='input');
 late.el('phonePrivate').checked=false;await late.el('phonePrivate').fire('change');lateGate.resolve({request_id:sent.body.request_id,status:'completed',response:{display_text:'LATE_PRIVATE'}});await pending;await drain();
 assert.equal(late.el('phoneHistory').textContent,'');assert.equal(late.calls.filter(c=>c.action==='input').length,1,'No send replay');
 const race=await connected('phone'),historyGate=race.holdConversation();race.intervals[0].fn();await drain();race.setInput(async body=>{race.setHistory([{id:body.request_id,user:body.text,assistant:'Latest checked answer.'}]);return {request_id:body.request_id,status:'completed',response:{response_action:'reply',delivery_mode:'text_only',display_text:'Latest checked answer.'}};});race.el('phoneText').value='Currentquestion.';await race.el('phoneForm').fire('submit');await drain();
 assert.ok(race.el('phoneHistory').textContent.includes('Latest checked answer.'));race.clearConversation();historyGate.resolve({turns:[{id:'previous',user:'Old',assistant:'Old snapshot.'}]});await drain();
 assert.ok(race.el('phoneHistory').textContent.includes('Latest checked answer.'),'Older in-flight history must not erase a newer checked reply');
 return {checked_reply_before_history_fetch:true,literal_text_only:true,no_duplicate_after_refresh:true,forget_clears_reply:true,request_id_bound:true,privacy_epoch_fenced:true,no_replay:true,old_history_cannot_replace_new_reply:true};
}
(async()=>{const which=process.argv[2];assert.ok(['presence','phone'].includes(which));const result=await(which==='presence'?presence():phone());console.log(JSON.stringify({passed:true,module:which,...result}));})().catch(e=>{console.error(e);process.exitCode=1;});
