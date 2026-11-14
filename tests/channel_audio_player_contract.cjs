/* Controlled media races. This does not claim Brave visual or physical hearing. */
const assert=require('node:assert/strict');
const {PlaybackQueue}=require('../src/gamma/dashboard/static/presence_queue.js');
const {ChannelAudioPlayer}=require('../src/gamma/dashboard/static/channel_audio_player.js');
const {RehearsalController}=require('../src/gamma/dashboard/static/stream_rehearsal.js');
const tick=()=>new Promise(r=>setImmediate(r));
function pending(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
function harness(options={}){
  const h={calls:[],media:[],notices:[],revoked:[],outputs:[],timers:[],valid:true,epoch:1};
  h.item=()=>({id:'output-'+'b'.repeat(32),generation:1,output_epoch:1,channel:'private_stream',
    consumer_id:'listener',audio_available:true,expires_at:2000,utterance_id:'channel-audio-'+'a'.repeat(32),
    chunk_index:0,chunk_count:1,final_chunk:true});
  const request=async(url,body,method,signal)=>{
    const path=new URL(url,'http://test').pathname;h.calls.push({path,body,method,signal});
    if(options[path])return options[path](body,signal,h);
    if(path.endsWith('/status'))return {generation:1,output_epoch:h.epoch,awake:true,
      consumer:{id:'listener',connected:true},outputs:h.outputs.map(x=>({...x}))};
    if(path.endsWith('/poll')){const row=h.outputs.find(x=>['generated','offered'].includes(x.status));
      if(row)row.status='offered';return {generation:1,output_epoch:h.epoch,output:row?{...row}:null};}
    if(path.endsWith('/ack')){const row=h.outputs.find(x=>x.id===body.output_id);if(row)row.status=body.status;return {status:body.status};}
    if(path.endsWith('/render'))return {status:'busy'};
    return {ok:true};
  };
  const makeAudio=url=>{
    const media={url,paused:false,onplaying:null,onended:null,onerror:null,
      async play(){if(this.onplaying)this.onplaying();},pause(){this.paused=true;},removeAttribute(){},load(){}};
    h.media.push(media);return media;
  };
  h.player=new ChannelAudioPlayer({Queue:PlaybackQueue,request,makeAudio,channel:options.channel,
    fetchAudio:options.fetchAudio||(()=>Promise.resolve({type:'audio/wav',size:204})),
    makeURL:()=>`blob:${h.media.length}`,revokeURL:url=>h.revoked.push(url),
    schedule:fn=>{h.timers.push(fn);return h.timers.length;},unschedule(){},
    now:()=>1000,notice:text=>h.notices.push(text)});
  h.context={consumerId:'listener',sessionId:'stream-'+'c'.repeat(32),generation:1,revision:1,valid:()=>h.valid};
  return h;
}
async function start(h){await h.player.enable(h.context);await tick();await tick();}
async function finish(h){await h.player.disable();await tick();}
(async()=>{
  let passed=0;
  {
    const h=harness();await h.player.render('stream-output-'+'a'.repeat(32));
    assert.equal(h.calls.length,0);h.valid=false;await start(h);assert.equal(h.calls.length,0);
    assert.equal(h.player.enabled,false);passed++;
  }
  {
    const held=pending();const h=harness({'/api/coordination/channel-audio/connect':()=>held.promise});
    const connecting=h.player.enable(h.context);await tick();const stopping=h.player.disable();
    held.resolve({ok:true});await connecting;await stopping;
    assert(!h.calls.some(c=>c.path.endsWith('/enable')&&c.body.enabled===true));
    assert(h.calls.some(c=>c.path.endsWith('/disconnect')));assert.equal(h.player.enabled,false);passed++;
  }
  {
    const held=pending();const h=harness({fetchAudio:()=>held.promise});h.outputs=[{...h.item(),status:'generated'}];
    await start(h);assert.equal(h.media.length,0);const stopping=h.player.disable();
    held.resolve({type:'audio/wav',size:204});await stopping;await tick();
    assert.equal(h.media.length,0);assert(!h.calls.some(c=>c.path.endsWith('/ack')));passed++;
  }
  {
    const held=pending();const h=harness({'/api/coordination/interaction/ack':body=>body.status==='started'?held.promise:{status:body.status}});
    h.outputs=[{...h.item(),status:'generated'}];await start(h);assert.equal(h.media.length,1);
    const ended=h.media[0].onended;ended();await tick();
    assert.deepEqual(h.calls.filter(c=>c.path.endsWith('/ack')).map(c=>c.body.status),['started']);
    held.resolve({status:'started'});await tick();await tick();
    assert.deepEqual(h.calls.filter(c=>c.path.endsWith('/ack')).map(c=>c.body.status),['started','played']);
    assert(!h.calls.some(c=>c.path.includes('/stream/ack')));await finish(h);passed++;
  }
  {
    let reads=0;const h=harness({'/api/coordination/interaction/status':(body,signal,h)=>({
      generation:1,output_epoch:1,awake:true,consumer:{id:'listener',connected:true},
      outputs:h.outputs.map(x=>({...x,status:++reads>1?'cancelled':'generated',audio_available:reads<=1}))})});
    h.outputs=[{...h.item(),status:'generated'}];await start(h);
    assert.equal(h.media.length,0);assert(!h.calls.some(c=>c.path.endsWith('/ack')));await finish(h);passed++;
  }
  {
    const h=harness(),displayed=[],streamCalls=[];
    const session={id:'stream-'+'c'.repeat(32),generation:1,revision:1,expires_at:2000,
      plan:{format:'coding'},owner_present:true,focused:false};
    const first='stream-output-'+'1'.repeat(32),second='stream-output-'+'2'.repeat(32);
    let nextId=first;
    const controller=new RehearsalController({audioPlayer:h.player,uuid:()=> 'listener',now:()=>1000,
      schedule:()=>0,unschedule(){},present:o=>{displayed.push(o.text);return true;},
      request:async(path,values)=>{streamCalls.push({path,values});
        if(path==='/start')return {...session};if(path==='/connect')return {revision:1};
        if(path==='/next'){const id=nextId;nextId=null;return {output:id?{...session,id,session_id:session.id,
          consumer_id:'listener',text:'A canonical displayed contribution.',audience:'owner_private',publication_authorized:false}:null};}
        return {ok:true};}});
    await controller.listen(true);assert.equal(h.calls.length,0);
    await controller.confirm(true);await controller.start({format:'coding',broad_plan:'Review boundaries.'});await tick();
    assert.equal(displayed.length,1);assert.equal(h.calls.length,0);
    await controller.listen(true);await tick();
    assert(!h.calls.some(c=>c.path.endsWith('/render')),'Enabling speech replayed an old displayed contribution.');
    nextId=second;await controller.loop(controller.epoch,session.id);await tick();
    assert.deepEqual(h.calls.filter(c=>c.path.endsWith('/render')).map(c=>c.body.stream_output_id),[second]);
    await controller.confirm(false);await h.player.operations;await tick();
    assert.equal(h.player.enabled,false);
    assert(!h.calls.some(c=>c.path.endsWith('/interaction/control')),'Local listening changed global voice controls.');
    assert(streamCalls.some(c=>c.path==='/ack'&&c.values.status==='delivered'));passed++;
  }
  {
    const h=harness();h.outputs=[{...h.item(),status:'generated'}];await start(h);
    assert.equal(h.media.length,1);const ended=h.media[0].onended;
    h.epoch=2;h.outputs[0].status='expired';h.outputs[0].audio_available=false;
    await h.player.poll(h.player.epoch);ended();await tick();
    assert(h.media[0].paused);assert(!h.calls.some(c=>c.path.endsWith('/ack')&&c.body.status==='played'));
    await finish(h);passed++;
  }
  for(const fail of [false,true]){
    const h=harness({'/api/coordination/channel-audio/render':()=>{if(fail)throw new Error('uncertain');return {status:'busy'};}});
    await start(h);const id='stream-output-'+'a'.repeat(32);
    await h.player.render(id);await h.player.render(id);
    assert.equal(h.calls.filter(c=>c.path.endsWith('/render')).length,1);
    assert.equal(h.media.length,0);await finish(h);passed++;
  }
  {
    const held=pending();const h=harness({fetchAudio:()=>held.promise});h.outputs=[{...h.item(),status:'generated'}];
    await start(h);h.valid=false;h.player.suspend();held.resolve({type:'audio/wav',size:204});await tick();
    assert.equal(h.media.length,0);assert(!h.calls.some(c=>c.path.endsWith('/ack')));await finish(h);passed++;
  }
  {
    const h=harness({channel:'shared_moment'});h.outputs=[{...h.item(),channel:'shared_moment',status:'generated'}];
    await start(h);assert.equal(h.media.length,1);await h.player.renderMoment('moment_one');await h.player.renderMoment('moment_one');
    const renders=h.calls.filter(c=>c.path.endsWith('/moment/render'));assert.equal(renders.length,1);
    assert.deepEqual(renders[0].body,{audience:'owner_private',consumer_id:'listener',moment_id:'moment_one'});
    await h.player.render('stream-output-'+'a'.repeat(32));assert.equal(h.calls.filter(c=>c.path.endsWith('/render')).length,1);
    const active=h.media[0];await finish(h);assert(active.paused);passed++;
  }
  {
    const h=harness({channel:'shared_moment','/api/coordination/channel-audio/moment/render':()=>{throw new Error('unknown provider outcome');}});
    await start(h);await h.player.renderMoment('uncertain');await h.player.renderMoment('uncertain');
    assert.equal(h.calls.filter(c=>c.path.endsWith('/moment/render')).length,1);assert.equal(h.media.length,0);await finish(h);passed++;
  }
  console.log(JSON.stringify({passed,kind:'checked_channel_audio_media_contract',physical_browser:false,human_hearing:false}));
})().catch(error=>{console.error(error);process.exitCode=1;});
