/* Real browser/Audio client test against a disposable transport fixture.
 * This does not claim Core, model, microphone, speaker or human listening proof.
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
function wav(seconds){
  const count=Math.floor(seconds*16000),data=Buffer.alloc(44+count*2);
  data.write('RIFF');data.writeUInt32LE(data.length-8,4);data.write('WAVEfmt ',8);data.writeUInt32LE(16,16);
  data.writeUInt16LE(1,20);data.writeUInt16LE(1,22);data.writeUInt32LE(16000,24);data.writeUInt32LE(32000,28);
  data.writeUInt16LE(2,32);data.writeUInt16LE(16,34);data.write('data',36);data.writeUInt32LE(count*2,40);
  for(let i=0;i<count;i++)data.writeInt16LE(Math.round(Math.sin(i*440*Math.PI/8000)*1200),44+i*2);
  return data;
}
async function main(){
  const links='/home/neety/.cache/ms-playwright/.links';
  const driver=fs.readdirSync(links).map(n=>fs.readFileSync(path.join(links,n),'utf8').trim()).find(p=>fs.existsSync(path.join(p,'package.json')));
  const {chromium}=require(driver),requests=[],acks=[],errors=[];
  let state={generation:1,output_epoch:1,awake:true,focus:'relaxed',voice_enabled:false,speech_enabled:false};
  let output=null,holdControl=false,releaseControl=null,consumerId=null,holdDisconnect=false,releaseDisconnect=null;
  const snapshot=()=>({...state,consumer:{id:consumerId,connected:consumerId!==null},outputs:output?[output]:[]});
  const server=http.createServer(async(req,res)=>{
    const url=new URL(req.url,'http://localhost');
    if(url.pathname==='/dashboard/presence'){res.setHeader('Content-Type','text/html');res.end(fs.readFileSync('src/gamma/coordination/presence.html'));return;}
    if(url.pathname.startsWith('/static/')){res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync('src/gamma/dashboard/static/'+path.basename(url.pathname)));return;}
    if(!url.pathname.startsWith('/api/')){res.writeHead(404);res.end();return;}
    requests.push(url.pathname);
    let raw='';for await(const chunk of req)raw+=chunk;const body=raw?JSON.parse(raw):{};
    const action=url.pathname.split('/').at(-1);
    res.setHeader('Cache-Control','no-store');
    const json=value=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));};
    if(action==='status'){json(snapshot());return;}
    if(action==='connect'){consumerId=body.consumer_id;json(snapshot());return;}
    if(action==='disconnect'){
      if(holdDisconnect)await new Promise(resolve=>{releaseDisconnect=resolve;});
      consumerId=null;state={...state,output_epoch:state.output_epoch+1};json(snapshot());return;
    }
    if(action==='control'){
      if(holdControl)await new Promise(resolve=>{releaseControl=resolve;});
      state={...state,[body.action]:body.value,output_epoch:state.output_epoch+1};json(snapshot());return;
    }
    if(action==='poll'){json({...state,output:output&&output.output_epoch===state.output_epoch?output:null});return;}
    if(url.pathname.includes('/audio/')){res.setHeader('Content-Type','audio/wav');res.end(wav(output?.seconds||.2));return;}
    if(action==='ack'){
      assert.equal('request_id' in body,false,'ack schema must not get unsupported request_id');
      acks.push(body);if(output?.id===body.output_id)output.status=body.status;
      if(['played','delivered','cancelled','failed'].includes(body.status)&&output?.id===body.output_id)output=null;
      json({ok:true});return;
    }
    if(action==='input'){json({ok:true,status:'partial'});return;}
    json({ok:true});
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:800,height:950}});
    await page.addInitScript(()=>{
      window.__micRequests=[];
      navigator.mediaDevices.getUserMedia=()=>new Promise((resolve,reject)=>window.__micRequests.push({resolve,reject}));
    });
    page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(origin+'/dashboard/presence');
    await page.waitForTimeout(600);assert.equal(requests.length,0);
    await page.locator('#privateConfirm').check();
    await page.waitForFunction(()=>document.getElementById('connectionStatus').textContent.startsWith('Connected privately.'));
    await page.locator('#muteToggle').click();
    await page.waitForFunction(()=>document.getElementById('muteToggle').textContent==='Mute speech');
    const first='output-'+'1'.repeat(32);
    output={id:first,generation:1,output_epoch:state.output_epoch,status:'offered',display_text:'A disposable client playback check.',speech_text:'A disposable client playback check.',audio_available:true,seconds:3};
    await page.waitForFunction(()=>document.body.dataset.speaking==='true');
    await page.waitForTimeout(100);
    assert(acks.some(a=>a.output_id===first&&a.status==='started'),'actual Audio playing event missing');
    state={...state,output_epoch:state.output_epoch+1};
    await page.waitForFunction(()=>document.body.dataset.speaking==='false');
    await page.waitForTimeout(3200);
    assert.equal(acks.some(a=>a.output_id===first&&a.status==='played'),false,'stale playback completed after epoch changed');
    const second='output-'+'2'.repeat(32);
    output={id:second,generation:1,output_epoch:state.output_epoch,status:'offered',display_text:'A short output.',speech_text:'A short output.',audio_available:true,seconds:.2};
    await page.waitForFunction(()=>document.getElementById('deliveryStatus').textContent==='Playback ended on this device.');
    await page.waitForTimeout(100);
    assert(acks.some(a=>a.output_id===second&&a.status==='started'));
    assert(acks.some(a=>a.output_id===second&&a.status==='played'));
    const third='output-'+'3'.repeat(32),sameEpoch=state.output_epoch;
    output={id:third,generation:1,output_epoch:sameEpoch,status:'offered',display_text:'This source will be removed.',speech_text:'This source will be removed.',audio_available:true,seconds:3};
    await page.waitForFunction(()=>document.body.dataset.speaking==='true');
    await page.waitForTimeout(100);
    assert(acks.some(a=>a.output_id===third&&a.status==='started'));
    output.status='expired';output.audio_available=false;output.display_text='';output.speech_text='';
    await page.waitForFunction(()=>document.body.dataset.speaking==='false');
    assert.equal(state.output_epoch,sameEpoch,'fixture changed epoch instead of testing source invalidation');
    assert.equal(await page.locator('#caption').innerText(),'');
    await page.waitForTimeout(3200);
    assert.equal(acks.some(a=>a.output_id===third&&a.status==='played'),false,'sameepoch expired output kept playing');
    await page.locator('#privateText').fill('A disposable partial-receipt wording check.');
    await page.locator('#sendText').click();
    await page.waitForFunction(()=>document.getElementById('inputStatus').textContent.startsWith('Only part of the spoken reply was generated.'));
    const before=await page.locator('#availability').innerText();
    await page.locator('#avatarToggle').click();assert.equal(await page.locator('#avatar').isVisible(),false);
    assert.equal(await page.locator('#availability').innerText(),before);
    await page.locator('#placement').selectOption('left');assert.equal(await page.locator('body').getAttribute('data-placement'),'left');
    holdControl=true;await page.locator('#muteToggle').click();
    await page.waitForTimeout(100);assert(releaseControl);
    await page.locator('#privateConfirm').uncheck();releaseControl();holdControl=false;
    await page.waitForTimeout(700);
    assert.equal(await page.locator('#presenceControls').isVisible(),false);
    await page.waitForTimeout(150);assert.equal(consumerId,null);
    holdDisconnect=true;
    await page.locator('#privateConfirm').check();
    await page.waitForFunction(()=>document.getElementById('connectionStatus').textContent.startsWith('Connected privately.'));
    await page.locator('#privateConfirm').uncheck();
    await page.waitForTimeout(100);assert(releaseDisconnect);
    const connectsBefore=requests.filter(p=>p.endsWith('/interaction/connect')).length;
    await page.locator('#privateConfirm').check();await page.waitForTimeout(200);
    assert.equal(requests.filter(p=>p.endsWith('/interaction/connect')).length,connectsBefore,'new connect raced old disconnect');
    holdDisconnect=false;releaseDisconnect();
    await page.waitForFunction(()=>document.getElementById('connectionStatus').textContent.startsWith('Connected privately.'));
    await page.locator('#privateConfirm').uncheck();
    for(const field of ['caption','availability','deliveryStatus','inputStatus'])assert.equal(await page.locator('#'+field).innerText(),'');
    // A late browser permission answer from an earlier private session must not
    // stop a newer capture request or reopen the old capture after privacy loss.
    await page.locator('#privateConfirm').check();
    await page.waitForFunction(()=>document.getElementById('connectionStatus').textContent.startsWith('Connected privately.'));
    await page.locator('#voiceToggle').click();
    await page.waitForFunction(()=>window.__micRequests.length===1);
    await page.locator('#privateConfirm').uncheck();
    await page.locator('#privateConfirm').check();
    await page.waitForFunction(()=>document.getElementById('connectionStatus').textContent.startsWith('Connected privately.'));
    await page.locator('#voiceToggle').click();
    await page.waitForFunction(()=>window.__micRequests.length===2);
    await page.evaluate(()=>window.__micRequests[0].reject(new Error('old permission rejected')));
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#voiceToggle').isDisabled(),true,'old permission failure changed new capture request');
    await page.locator('#privateConfirm').uncheck();
    await page.evaluate(()=>{
      const context=new AudioContext(),destination=context.createMediaStreamDestination();
      window.__lateStream=destination.stream;window.__lateContext=context;
      window.__micRequests[1].resolve(destination.stream);
    });
    await page.waitForFunction(()=>window.__lateStream.getTracks().every(t=>t.readyState==='ended'));
    await page.evaluate(()=>window.__lateContext.close());
    assert.equal(await page.locator('#presenceControls').isVisible(),false);
    assert.equal(requests.includes('/api/coordination/interaction/audio-input'),false);
    assert.equal(errors.length,0,errors.join(';'));
    const receipt={passed:true,scope:'production client HTML/JS and actual headless browser Audio lifecycle against disposable HTTP fixture',
      no_private_fetch_before_confirmation:true,no_auto_capture:true,started_and_ended_audio_events:true,stale_epoch_stopped_audio:true,
      late_control_could_not_recreate_private_dom:true,avatar_hide_did_not_sleep:true,local_placement:true,
      stale_microphone_permission_fenced:true,
      sameepoch_expired_output_stopped_audio:true,disconnect_before_reconnect_serialized:true,
      partial_receipt_not_reported_as_full_delivery:true,
      real_core:false,real_microphone:false,physical_speaker:false,human_listening:false,acks,requests,errors};
    fs.mkdirSync('data/coordination/p5',{recursive:true});fs.writeFileSync('data/coordination/p5/presence-client-browser.json',JSON.stringify(receipt,null,2));
    console.log(JSON.stringify(receipt));
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
