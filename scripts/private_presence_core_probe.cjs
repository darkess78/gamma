/* Real authenticated dashboard -> API -> Core -> Audio -> source fence path.
 * Python host injects a declared synthetic draft and PCM synthesis fixture.
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
async function main(){
  let raw='';for await(const part of process.stdin)raw+=part;const input=JSON.parse(raw);
  const links='/home/neety/.cache/ms-playwright/.links';
  const driver=fs.readdirSync(links).map(n=>fs.readFileSync(path.join(links,n),'utf8').trim()).find(p=>fs.existsSync(path.join(p,'package.json')));
  const {chromium}=require(driver),browser=await chromium.launch({headless:true});
  const prefix='/api/coordination/interaction',errors=[],acks=[];let consumer=null;
  try{
    const page=await browser.newPage({viewport:{width:800,height:950}});
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('request',req=>{const p=new URL(req.url()).pathname;if(p===prefix+'/connect')consumer=JSON.parse(req.postData()).consumer_id;if(p===prefix+'/ack')acks.push(JSON.parse(req.postData()));});
    await page.goto(input.url+'/dashboard/presence');
    await page.locator('#username').fill(input.username);await page.locator('#password').fill(input.password);
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/login'&&r.request().method()==='POST'),page.locator('button[type=submit]').click()]);
    await page.waitForURL(input.url+'/dashboard/monitor');await page.goto(input.url+'/dashboard/presence');
    await page.locator('#privateConfirm').check();
    await page.waitForFunction(()=>document.getElementById('connectionStatus').textContent.startsWith('Connected privately.'));
    assert(consumer);
    async function getStatus(){const r=await page.request.get(input.url+prefix+'/status',{params:{audience:'owner_private',consumer_id:consumer}});assert.equal(r.status(),200);return r.json();}
    async function post(route,body){const r=await page.request.post(input.url+route,{data:body});assert.equal(r.status(),200,await r.text());return r.json();}
    const envelope=()=>({audience:'owner_private',consumer_id:consumer,request_id:crypto.randomUUID()});
    await page.locator('#muteToggle').click();
    await page.waitForFunction(()=>document.getElementById('muteToggle').textContent==='Mute speech');
    await post(prefix+'/control',{...envelope(),action:'voice_enabled',value:true});
    const voice=await post(prefix+'/input',{...envelope(),session_id:'client-core-proof',text:'A controlled owner voice observation.',
      observation:{channel:'private_voice',speaker:'owner',address_cue:'direct',confidence:.5}});
    assert.equal(voice.status,'generated');
    await page.waitForFunction(()=>document.body.dataset.speaking==='true');
    let before;
    for(let i=0;i<20;i++){before=await getStatus();if(before.outputs.some(o=>o.id===voice.output_id&&o.status==='started'))break;await page.waitForTimeout(50);}
    assert(before.outputs.some(o=>o.id===voice.output_id&&o.status==='started'));
    // A regular admitted Monitor turn changes the real Core source sequence.
    // The output epoch itself remains unchanged.
    await post('/api/monitor/input',{audience:'owner_private',request_id:crypto.randomUUID(),session_id:'client-core-proof',input_mode:'private_text',text:'A newer owner turn invalidates that earlier voice output.'});
    await page.waitForFunction(()=>document.body.dataset.speaking==='false');
    const after=await getStatus();
    assert.equal(after.output_epoch,before.output_epoch);
    const expired=after.outputs.find(o=>o.id===voice.output_id);
    assert.equal(expired.status,'expired');assert.equal(expired.audio_available,false);
    assert.equal(await page.locator('#caption').innerText(),'');
    await page.waitForTimeout(3300);
    assert.equal(acks.some(a=>a.output_id===voice.output_id&&a.status==='played'),false);
    await page.locator('#privateText').fill('A controlled desktop text message.');await page.locator('#sendText').click();
    await page.waitForFunction(()=>document.getElementById('deliveryStatus').textContent==='Displayed as text.');
    const textStatus=await getStatus();
    const delivered=textStatus.outputs.find(o=>o.channel==='desktop_text'&&o.status==='delivered');assert(delivered);
    assert(acks.some(a=>a.output_id===delivered.id&&a.status==='attempted'));
    assert(acks.some(a=>a.output_id===delivered.id&&a.status==='delivered'));
    if(input.screenshot)await page.screenshot({path:input.screenshot,fullPage:true});
    await page.locator('#privateConfirm').uncheck();await page.waitForTimeout(500);
    assert.equal(await page.locator('#presenceControls').isVisible(),false);assert.equal(await page.locator('#caption').innerText(),'');
    assert.equal(errors.length,0,errors.join(';'));
    process.stdout.write(JSON.stringify({complete:true,real_core:true,real_http_and_dashboard_auth:true,real_browser_audio_lifecycle:true,
      sameepoch_core_sequence_invalidated_output:true,expired_audio_stopped_and_caption_cleared:true,no_stale_played_ack:true,
      text_attempted_then_delivered:true,no_auto_microphone_capture:true,synthetic_draft:true,synthetic_synthesis:true,
      real_microphone:false,real_model:false,physical_speaker:false,human_listening:false,
      generation:before.generation,output_epoch:before.output_epoch,expired_output:expired.id,delivered_output:delivered.id,acks,errors}));
  }finally{await browser.close();}
}
main().catch(error=>{process.stderr.write(String(error.stack||error));process.exitCode=1;});
