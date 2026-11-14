const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
function element(value='') {
  return {value, textContent:'', children:[], handlers:{},
    addEventListener(k,f){this.handlers[k]=f;},
    appendChild(child){this.children.push(child);return child;},
    replaceChildren(...children){this.children=children;},
    get options(){const children=this.children;return {length:children.length,*[Symbol.iterator](){yield*children;}};},
    set innerHTML(value){assert.equal(value,'');this.children=[];}};
}
async function main(){
  const elements={monitorPrivateAudience:element('unknown'), coordinationExercise:element('retry_budget'),
    coordinationActivityTarget:element(), coordinationStatus:element()};
  let ready, interval, ids=0, failed=false, next=[], statusGate=null, permitGate=null;
  const requests=[];
  const window={setInterval(fn){interval=fn;}};
  const document={getElementById:id=>elements[id]||null, createElement:()=>element(),
    addEventListener(k,fn){if(k==='DOMContentLoaded')ready=fn;}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/gamma/dashboard/static/coordination.js'),'utf8'),{window,document,console,
    crypto:{randomUUID:()=>`request-${++ids}`},fetch:async(url,options)=>{
      requests.push({url,body:options?JSON.parse(options.body):null});
      if(options){
        if(permitGate && JSON.parse(options.body).action==='permit'){await permitGate;throw Error('uncertain permit response');}
        if(failed)throw Error('uncertain network');return{ok:true,json:async()=>({requested:'pause',acknowledged:true})};
      }
      const observed=next;
      return{ok:true,json:async()=>{if(statusGate)await statusGate;return{awake:true,focus:'focused',activities:observed};}};
    }});
  ready(); await interval(); assert.equal(requests.length,0);
  const a={id:'practice-a',goal:'retry_budget',status:'paused',worker_run:'practice-a',next_step:'check_saved_edit'};
  const b={id:'practice-b',goal:'message_chunks',status:'paused',worker_run:null,next_step:'start_authorized_exercise'};
  next=[a,b]; elements.monitorPrivateAudience.value='owner_private';
  await elements.monitorPrivateAudience.handlers.change.call(elements.monitorPrivateAudience);
  await interval();
  await window.coordinationAction('resume');
  assert.equal(requests.filter(r=>r.body).length,0,'multiple unfinished activities require an explicit selection');
  elements.coordinationActivityTarget.value='practice-b';
  next=[b,a]; await interval();
  assert.equal(elements.coordinationActivityTarget.value,'practice-b','refresh preserves the selected activity');
  failed=true; await window.coordinationAction('pause');
  failed=false; await window.coordinationAction('pause');
  const controls=requests.filter(r=>r.body);
  assert.equal(controls.length,2);
  assert.equal(controls[0].body.run_id,'practice-b');
  assert.equal(controls[1].body.request_id,controls[0].body.request_id);
  assert.equal(controls[1].body.audience,'owner_private');
  assert(!elements.coordinationStatus.textContent.includes('Run: null'));
  assert(elements.coordinationStatus.textContent.includes('practice-b'));
  next=[a,{...b,status:'stopped'}]; await interval();
  assert.notEqual(elements.coordinationActivityTarget.value,'practice-b','terminal targets are no longer controllable');
  let release;
  statusGate=new Promise(resolve=>{release=resolve;});
  const late=interval();
  await Promise.resolve();
  elements.monitorPrivateAudience.value='unknown';
  await elements.monitorPrivateAudience.handlers.change.call(elements.monitorPrivateAudience);
  release(); await late;
  const count=requests.length; await interval(); await window.coordinationAction('resume');
  assert.equal(requests.length,count,'no private requests before audience confirmation');
  assert(!elements.coordinationStatus.textContent.includes('practice-b'),'clear private display when audience changes');
  statusGate=null;
  elements.monitorPrivateAudience.value='owner_private';
  await elements.monitorPrivateAudience.handlers.change.call(elements.monitorPrivateAudience);
  let releasePermit;
  permitGate=new Promise(resolve=>{releasePermit=resolve;});
  const uncertainPermit=window.coordinationAction('permit');
  await Promise.resolve();
  await window.coordinationAction('focus','focused');
  releasePermit(); await uncertainPermit; permitGate=null;
  await window.coordinationAction('permit');
  const permits=requests.filter(r=>r.body?.action==='permit');
  assert.equal(permits.length,2);
  assert.equal(permits[0].body.request_id,permits[1].body.request_id,'overlapping controls must not renew an uncertain permission');
  console.log('Activity controls retain explicit targets, private scope and retry identity.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
