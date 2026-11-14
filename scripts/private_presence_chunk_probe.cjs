/* Actual Core/browser chunk ordering with a declared held synthesis callback. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
async function main(){
  let raw='';for await(const part of process.stdin)raw+=part;const input=JSON.parse(raw);
  const links='/home/neety/.cache/ms-playwright/.links';
  const driver=fs.readdirSync(links).map(n=>fs.readFileSync(path.join(links,n),'utf8').trim()).find(p=>fs.existsSync(path.join(p,'package.json')));
  const {chromium}=require(driver),browser=await chromium.launch({headless:true});
  const prefix='/api/coordination/interaction',errors=[],acks=[],audioFetches=[];let consumer=null;
  try{
    const page=await browser.newPage({viewport:{width:800,height:950}});
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('request',req=>{const p=new URL(req.url()).pathname;if(p===prefix+'/connect')consumer=JSON.parse(req.postData()).consumer_id;if(p===prefix+'/ack')acks.push(JSON.parse(req.postData()));if(p.startsWith(prefix+'/audio/'))audioFetches.push(p.split('/').at(-1));});
    await page.goto(input.url+'/dashboard/presence');await page.locator('#username').fill(input.username);await page.locator('#password').fill(input.password);
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/login'&&r.request().method()==='POST'),page.locator('button[type=submit]').click()]);
    await page.waitForURL(input.url+'/dashboard/monitor');await page.goto(input.url+'/dashboard/presence');
    await page.locator('#privateConfirm').check();await page.waitForFunction(()=>document.getElementById('connectionStatus').textContent.startsWith('Connected privately.'));
    async function get(route){const r=await page.request.get(input.url+route);assert.equal(r.status(),200,await r.text());return r.json();}
    async function post(route,body){const r=await page.request.post(input.url+route,{data:body});assert.equal(r.status(),200,await r.text());return r.json();}
    const status=()=>get(prefix+'/status?audience=owner_private&consumer_id='+consumer);
    const envelope=()=>({audience:'owner_private',consumer_id:consumer,request_id:crypto.randomUUID()});
    const voice=(rid)=>post(prefix+'/input',{...envelope(),request_id:rid,session_id:'chunk-browser-proof',text:'A controlled owner voice observation.',
      observation:{channel:'private_voice',speaker:'owner',address_cue:'direct',confidence:.5}});
    await page.locator('#muteToggle').click();await page.waitForFunction(()=>document.getElementById('muteToggle').textContent==='Mute speech');
    await post(prefix+'/control',{...envelope(),action:'voice_enabled',value:true});
    const cancelledRid=crypto.randomUUID();let requestReturned=false;
    const pending=voice(cancelledRid).then(value=>{requestReturned=true;return value;});
    await page.waitForFunction(()=>document.body.dataset.speaking==='true');
    let held;
    for(let i=0;i<30;i++){held=await get('/api/presence-proof/state');if(held.second_held)break;await page.waitForTimeout(50);}
    assert.equal(held.second_held,true);assert.equal(requestReturned,false,'first audio waited for full synthesis request');
    let firstState;
    for(let i=0;i<20;i++){firstState=await status();if(firstState.outputs.some(o=>o.utterance_id===cancelledRid&&o.status==='started'))break;await page.waitForTimeout(50);}
    const first=firstState.outputs.find(o=>o.utterance_id===cancelledRid);assert(first);
    assert.equal(first.chunk_index,0);assert.equal(first.chunk_count,2);assert.equal(first.status,'started');
    await post('/api/monitor/input',{audience:'owner_private',request_id:crypto.randomUUID(),session_id:'chunk-browser-proof',input_mode:'private_text',text:'A newer owner turn cancels those earlier chunks.'});
    await page.waitForFunction(()=>document.body.dataset.speaking==='false');
    assert.equal(await page.locator('#caption').innerText(),'');
    await post('/api/presence-proof/release',{});
    const cancelled=await pending;
    assert.equal(cancelled.status,'cancelled');
    const invalid=await status();assert.equal(invalid.output_epoch,firstState.output_epoch);
    const old=invalid.outputs.filter(o=>o.utterance_id===cancelledRid);assert.equal(old.length,1);assert.equal(old[0].status,'expired');
    await page.waitForTimeout(3300);assert.equal(acks.some(a=>a.output_id===first.id&&a.status==='played'),false);
    const acceptedRid=crypto.randomUUID();const accepted=await voice(acceptedRid);
    assert.equal(accepted.status,'generated');assert.equal(accepted.output_ids.length,2);
    const ids=accepted.output_ids;
    let finished;
    for(let i=0;i<80;i++){finished=await status();if(ids.every(id=>finished.outputs.some(o=>o.id===id&&o.status==='played')))break;await page.waitForTimeout(100);}
    assert(ids.every(id=>finished.outputs.some(o=>o.id===id&&o.status==='played')),'both chunks did not play');
    const relevant=acks.filter(a=>ids.includes(a.output_id)).map(a=>[ids.indexOf(a.output_id),a.status]);
    assert.deepEqual(relevant,[[0,'started'],[0,'played'],[1,'started'],[1,'played']]);
    for(const outputId of ids)assert.equal(audioFetches.filter(id=>id===outputId).length,1,'chunk fetched more than once');
    assert.equal(await page.locator('#caption').innerText(),input.second_sentence);
    assert.equal(await page.locator('#voiceToggle').innerText(),'Start listening');
    if(input.screenshot)await page.screenshot({path:input.screenshot,fullPage:true});
    await page.locator('#privateConfirm').uncheck();await page.waitForTimeout(300);assert.equal(await page.locator('#caption').innerText(),'');
    assert.equal(errors.length,0,errors.join(';'));
    process.stdout.write(JSON.stringify({complete:true,real_core:true,real_http_auth_and_browser:true,synthetic_draft:true,synthetic_synthesis:true,
      first_chunk_played_before_full_request_returned:true,second_synthesis_held:true,newer_core_turn_cancelled_future_chunk:true,
      same_epoch_cancellation:true,no_stale_played_ack:true,two_chunks_played_once_in_order:true,second_chunk_caption_replaced_first:true,
      ack_order:relevant,first_output:first.id,completed_outputs:ids,audio_fetches:audioFetches,errors,
      real_model:false,real_microphone:false,physical_speaker:false,human_listening:false}));
  }finally{await browser.close();}
}
main().catch(error=>{process.stderr.write(String(error.stack||error));process.exitCode=1;});
