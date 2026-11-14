/* Actual client capture/VAD -> real HTTP/Core with declared synthetic microphone/providers. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
async function main(){
  let raw='';for await(const part of process.stdin)raw+=part;const input=JSON.parse(raw);
  const links='/home/neety/.cache/ms-playwright/.links';
  const driver=fs.readdirSync(links).map(n=>fs.readFileSync(path.join(links,n),'utf8').trim()).find(p=>fs.existsSync(path.join(p,'package.json')));
  const {chromium}=require(driver),browser=await chromium.launch({headless:true});
  const prefix='/api/coordination/interaction',errors=[],requests=[],responses=[],aborted=[],acknowledgments=[],tasks=[];let consumer=null;
  try{
    const page=await browser.newPage({viewport:{width:800,height:950}});
    await page.addInitScript(()=>{
      window.__syntheticMicrophoneCalls=0;
      navigator.mediaDevices.getUserMedia=async()=>{
        window.__syntheticMicrophoneCalls+=1;
        const context=new AudioContext(),oscillator=context.createOscillator(),gain=context.createGain(),destination=context.createMediaStreamDestination();
        oscillator.frequency.value=440;gain.gain.value=0;oscillator.connect(gain);gain.connect(destination);oscillator.start();await context.resume();
        window.__captureFixture={context,oscillator,gain,destination};
        window.__pulse=async()=>{gain.gain.setValueAtTime(.08,context.currentTime);await new Promise(resolve=>setTimeout(resolve,450));gain.gain.setValueAtTime(0,context.currentTime);};
        return destination.stream;
      };
    });
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('request',request=>{
      const p=new URL(request.url()).pathname;
      if(p===prefix+'/connect')consumer=JSON.parse(request.postData()).consumer_id;
      if(p===prefix+'/audio-input'){
        const body=JSON.parse(request.postData());
        requests.push({request_id:body.request_id,at:Date.now(),sha256:crypto.createHash('sha256').update(Buffer.from(body.audio_base64,'base64')).digest('hex')});
      }
      if(p===prefix+'/ack')acknowledgments.push(JSON.parse(request.postData()));
    });
    page.on('response',response=>{if(new URL(response.url()).pathname===prefix+'/audio-input')tasks.push((async()=>responses.push({request_id:JSON.parse(response.request().postData()).request_id,
      at:Date.now(),http_status:response.status(),receipt:await response.json()}))());});
    page.on('requestfailed',request=>{if(new URL(request.url()).pathname===prefix+'/audio-input')aborted.push({request_id:JSON.parse(request.postData()).request_id,error:request.failure()?.errorText});});
    await page.goto(input.url+'/dashboard/presence');await page.locator('#username').fill(input.username);await page.locator('#password').fill(input.password);
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/login'&&r.request().method()==='POST'),page.locator('button[type=submit]').click()]);
    await page.waitForURL(input.url+'/dashboard/monitor');await page.goto(input.url+'/dashboard/presence');
    await page.locator('#privateConfirm').check();await page.waitForFunction(()=>document.getElementById('connectionStatus').textContent.startsWith('Connected privately.'));
    assert.equal(await page.evaluate(()=>window.__syntheticMicrophoneCalls),0);
    await page.locator('#ownerSource').check();await page.locator('#muteToggle').click();
    await page.waitForFunction(()=>document.getElementById('muteToggle').textContent==='Mute speech');
    await page.locator('#voiceToggle').click();await page.waitForFunction(()=>document.getElementById('voiceToggle').textContent==='Stop listening');
    async function get(route){const response=await page.request.get(input.url+route);assert.equal(response.status(),200);return response.json();}
    async function proof(){return get('/api/capture-proof/state');}
    async function waitFor(read,accept,ms=10000){const end=Date.now()+ms;let value;while(Date.now()<end){value=await read();if(accept(value))return value;await page.waitForTimeout(50);}throw Error('bounded capture proof timed out: '+JSON.stringify(value));}
    await page.evaluate(()=>window.__pulse());
    await waitFor(async()=>requests.length,n=>n===1);
    await page.waitForFunction(()=>document.body.dataset.speaking==='true');
    const held=await waitFor(proof,value=>value.second_held);
    assert.equal(responses.length,0,'first HTTP request returned before held synthesis');
    assert.equal(held.transcriptions.length,1);
    await page.evaluate(()=>window.__pulse());
    await waitFor(async()=>requests.length,n=>n===2);
    const admitted=await waitFor(proof,value=>value.transcriptions.length===2&&value.completed_turns.length===2);
    assert.equal(responses.length,0,'new microphone turn waited until old HTTP request completed');
    assert.deepEqual(admitted.transcriptions.map(row=>row.sha256),requests.map(row=>row.sha256),'client WAV did not reach real transcriber callback');
    await page.waitForFunction(()=>document.body.dataset.speaking==='false');
    await page.evaluate(()=>window.__pulse());
    await page.waitForFunction(()=>document.getElementById('micStatus').textContent==='Two voice turns are already pending. This turn was not sent.');
    assert.equal(requests.length,2,'third captured clip spawned an unbounded request');
    const response=await page.request.post(input.url+'/api/capture-proof/release',{data:{}});assert.equal(response.status(),200);
    await waitFor(async()=>responses.length,n=>n===2);await Promise.all(tasks);
    const first=responses.find(row=>row.request_id===requests[0].request_id),second=responses.find(row=>row.request_id===requests[1].request_id);
    assert.equal(first.http_status,200);assert.equal(second.http_status,200);
    assert.equal(first.receipt.status,'cancelled');assert.equal(second.receipt.status,'generated');
    const ids=second.receipt.output_ids;
    assert.equal(ids.length,2);
    const final=await waitFor(()=>get(prefix+'/status?audience=owner_private&consumer_id='+consumer),
      value=>ids.every(id=>value.outputs.some(row=>row.id===id&&row.status==='played')));
    const old=final.outputs.filter(row=>row.utterance_id===requests[0].request_id);
    assert.equal(old.length,1);assert(['cancelled','expired'].includes(old[0].status));
    assert.equal(acknowledgments.some(row=>row.output_id===old[0].id&&row.status==='played'),false);
    assert.equal((await page.locator('#micStatus').innerText()).startsWith('Turn processed.'),true,'old cancelled receipt overwrote newer input status');
    // A later capture is held in synthesis. Revoking the private audience must
    // abort its client fetch, end the stream and invalidate release on the server.
    await page.evaluate(()=>window.__pulse());
    await waitFor(async()=>requests.length,n=>n===3);
    await waitFor(proof,value=>value.privacy_held);
    await page.locator('#privateConfirm').uncheck();
    await page.waitForFunction(()=>window.__captureFixture.destination.stream.getTracks().every(track=>track.readyState==='ended'));
    await waitFor(async()=>aborted,value=>value.some(row=>row.request_id===requests[2].request_id));
    await waitFor(proof,value=>value.connected_consumers===0);
    const released=await page.request.post(input.url+'/api/capture-proof/release-privacy',{data:{}});assert.equal(released.status(),200);
    const cancelledPrivacy=await waitFor(proof,value=>value.request_receipts.some(row=>row.id===requests[2].request_id&&row.status==='cancelled'));
    assert.equal(cancelledPrivacy.output_utterances.includes(requests[2].request_id),false);
    await page.evaluate(()=>window.__captureFixture.context.close());
    assert.equal(await page.locator('#caption').innerText(),'');assert.equal(errors.length,0,errors.join(';'));
    process.stdout.write(JSON.stringify({complete:true,real_client_capture_vad_wav_http_core:true,synthetic_microphone_stream:true,
      synthetic_transcribe_draft_synthesis:true,physical_microphone:false,real_model:false,human_listening:false,
      no_auto_microphone_capture:true,second_clip_transcribed_and_core_accepted_before_first_http_completed:true,
      third_clip_dropped_without_request:true,newer_voice_cancelled_old_playback_and_future_chunk:true,
      newer_input_status_retained:true,privacy_stopped_stream_and_cleared_dom:true,privacy_aborted_active_audio_request:true,
      privacy_blocked_future_audio_release:true,
      requests,responses:responses.map(row=>({request_id:row.request_id,at:row.at,http_status:row.http_status,status:row.receipt.status})),
      completed_outputs:ids,aborted_requests:aborted,errors}));
  }finally{await browser.close();}
}
main().catch(error=>{process.stderr.write(String(error.stack||error));process.exitCode=1;});
