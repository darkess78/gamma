/* Behavioral transport races, without pretending to be physical browser review. */
const assert = require('node:assert/strict');
const { RehearsalController } = require('../src/gamma/dashboard/static/stream_rehearsal.js');

function pending() { let resolve; const promise=new Promise(r=>{resolve=r;});return {promise,resolve}; }
function harness(overrides={}) {
  const calls=[], shown=[], states=[], tasks=[];let serial=0;
  const session={id:'stream-'+'a'.repeat(32),generation:1,revision:1,expires_at:2000,
    plan:{format:'gameplay'},owner_present:true,focused:false};
  const run=async(path,values,method)=>{
    calls.push({path,values,method});
    if(overrides[path]) return overrides[path](values,method);
    if(path==='/start')return {...session};
    if(path==='/connect')return {ok:true,revision:1};
    if(path==='/status')return {active:{...session}};
    if(path==='/next')return {output:null};
    if(path==='/event')return {status:'quiet'};
    if(path==='/control')return {session:{...session,revision:2},catch_up:[]};
    return {ok:true};
  };
  const c=new RehearsalController({request:run,present:o=>{shown.push(o);return overrides.present!==false;},
    update:s=>states.push(s),uuid:()=>`id-${++serial}`,now:()=>1000,
    schedule:fn=>{tasks.push(fn);return tasks.length;},unschedule:()=>{}});
  return {c,calls,shown,states,tasks,session};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function output(h,changes={}) {
  return {id:'stream-output-'+'b'.repeat(32),session_id:h.session.id,consumer_id:h.c.consumer,
    generation:1,revision:1,audience:'owner_private',publication_authorized:false,
    expires_at:1100,text:'<script>not markup</script>',...changes};
}
async function opened(h) {await h.c.confirm(true);await h.c.start({format:'gameplay',broad_plan:'A finite idea'});await tick();}

(async()=>{
  let checks=0;
  {
    const h=harness();await h.c.start({});await h.c.connectExisting();await h.c.send('chat','hi','viewer');
    await h.c.control('owner_away');assert.equal(h.calls.length,0);checks++;
  }
  {
    const late=pending();const h=harness({'/start':()=>late.promise});
    await h.c.confirm(true);const starting=h.c.start({format:'gameplay'});
    await h.c.confirm(false);await h.c.confirm(true);
    await h.c.connectExisting();assert.equal(h.calls.filter(c=>c.path==='/status').length,0);
    late.resolve({...h.session});await starting;await tick();
    assert.equal(h.calls.filter(c=>c.path==='/connect').length,0);
    assert.equal(h.calls.filter(c=>c.path==='/control'&&c.values.action==='stop').length,1);
    assert.equal(h.c.connected,false);assert.equal(h.c.busy,false);checks++;
  }
  {
    const late=pending();const h=harness({'/next':()=>late.promise});await opened(h);
    await h.c.confirm(false);late.resolve({output:output(h)});await tick();
    assert.equal(h.shown.length,0);assert.equal(h.calls.filter(c=>c.path==='/ack').length,0);
    assert.equal(h.calls.filter(c=>c.path==='/disconnect').length,1);checks++;
  }
  {
    const h=harness();h.c.request=async(path,values,method)=>{
      h.calls.push({path,values,method});
      if(path==='/start')return {...h.session};if(path==='/connect')return {revision:1};
      return path==='/next'?{output:output(h)}:{ok:true};
    };
    await opened(h);assert.equal(h.shown.length,1);assert.equal(h.shown[0].text,'<script>not markup</script>');
    assert.equal(h.calls.filter(c=>c.path==='/ack').length,1);
    await h.c.loop(h.c.epoch,h.session.id);assert.equal(h.shown.length,1);
    assert.equal(h.calls.filter(c=>c.path==='/ack').length,1);checks++;
  }
  {
    const h=harness({present:false});h.c.request=async(path,values,method)=>{
      h.calls.push({path,values,method});if(path==='/start')return {...h.session};
      if(path==='/connect')return {revision:1};return path==='/next'?{output:output(h)}:{ok:true};};
    await opened(h);assert.equal(h.calls.filter(c=>c.path==='/ack').length,0);checks++;
  }
  {
    const h=harness({'/event':()=>{throw new Error('uncertain');}});await opened(h);
    await h.c.send('chat','Is this the next step?','ExampleViewer');await tick();
    const requests=h.calls.filter(c=>c.path==='/event');assert.equal(requests.length,1);
    assert.equal(requests[0].values.source,'replay');assert.equal(h.c.busy,false);checks++;
  }
  {
    const h=harness();h.c.request=async(path,values,method)=>{
      h.calls.push({path,values,method});if(path==='/start')return {...h.session};
      if(path==='/connect')return {revision:1};return path==='/next'?{output:output(h,{generation:2})}:{ok:true};};
    await opened(h);assert.equal(h.shown.length,0);assert.equal(h.calls.filter(c=>c.path==='/ack').length,0);
    assert.equal(h.c.connected,false);checks++;
  }
  {
    const late=pending();let poll=0;const h=harness({'/next':()=>++poll===1?late.promise:{output:null}});
    await opened(h);await h.c.control('focus');late.resolve({output:output(h)});await tick();
    assert.equal(h.shown.length,0);assert.equal(h.calls.filter(c=>c.path==='/ack').length,0);
    assert.equal(h.c.session.revision,2);checks++;
  }
  {
    const late=pending();const h=harness({'/event':()=>late.promise});await opened(h);
    const sending=h.c.send('chat','Question while a draft is pending','viewer');
    assert.equal(h.c.busy,true);await h.c.control('stop');
    assert.equal(h.c.connected,false);
    assert.equal(h.calls.filter(c=>c.path==='/control'&&c.values.action==='stop').length,1);
    late.resolve({status:'ready',output_id:'late'});await sending;
    assert.equal(h.shown.length,0);assert.equal(h.calls.filter(c=>c.path==='/ack').length,0);checks++;
  }
  console.log(JSON.stringify({passed:checks,kind:'private_rehearsal_transport_contract',physical_browser:false}));
})().catch(error=>{console.error(error);process.exitCode=1;});
