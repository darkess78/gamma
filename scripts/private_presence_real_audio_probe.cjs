/* Real isolated model/STT/TTS -> authenticated browser Audio path.
 * Input WAV is supplied on stdin. This driver never captures a microphone,
 * replaces a provider, changes a model answer, or makes a human hearing claim.
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
async function main(){
  let raw='';for await(const part of process.stdin)raw+=part;const input=JSON.parse(raw);
  const url=new URL(input.url);
  assert(['127.0.0.1','[::1]'].includes(url.hostname)&&url.protocol==='http:'&&url.port&&!['8000','8001','8080','11434'].includes(url.port),'isolated loopback host required');
  assert(!url.username&&!url.password&&!url.search&&!url.hash&&['','/'].includes(url.pathname),'origin only');
  assert(typeof input.audio_base64==='string'&&input.audio_base64.length<=1100000,'bounded input WAV required');
  const inputBytes=Buffer.from(input.audio_base64,'base64');assert(inputBytes.length>=44&&inputBytes.length<=800000);
  const links='/home/neety/.cache/ms-playwright/.links';
  const driver=fs.readdirSync(links).map(n=>fs.readFileSync(path.join(links,n),'utf8').trim()).find(p=>fs.existsSync(path.join(p,'package.json')));
  const {chromium}=require(driver),browser=await chromium.launch({headless:true});
  const prefix='/api/coordination/interaction',errors=[],ackRequests=[],ackResponses=[],audioResponses=[],tasks=[];
  let consumer=null,inputStartedAt=null,inputCompletedAt=null;
  const deadline=Date.now()+180000;
  const remaining=()=>Math.max(1,deadline-Date.now());
  try{
    const page=await browser.newPage({viewport:{width:800,height:950}});
    page.setDefaultTimeout(15000);
    await page.addInitScript(()=>{
      window.__presenceAudioEvents=[];window.__presenceMicrophoneCalls=0;
      const original=HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play=function(...args){
        this.addEventListener('playing',()=>window.__presenceAudioEvents.push({kind:'playing',at:Date.now()}),{once:true});
        this.addEventListener('ended',()=>window.__presenceAudioEvents.push({kind:'ended',at:Date.now()}),{once:true});
        return Reflect.apply(original,this,args);
      };
      const capture=navigator.mediaDevices?.getUserMedia;
      if(capture)navigator.mediaDevices.getUserMedia=function(...args){window.__presenceMicrophoneCalls+=1;return Reflect.apply(capture,this,args);};
    });
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('request',request=>{
      const p=new URL(request.url()).pathname;
      if(p===prefix+'/connect')consumer=JSON.parse(request.postData()).consumer_id;
      if(p===prefix+'/ack')ackRequests.push({...JSON.parse(request.postData()),at:Date.now()});
    });
    page.on('response',response=>{
      const p=new URL(response.url()).pathname;
      if(p===prefix+'/ack'){
        const body=JSON.parse(response.request().postData());
        ackResponses.push({output_id:body.output_id,status:body.status,http_status:response.status(),at:Date.now()});
      }
      if(p.startsWith(prefix+'/audio/')){
        const at=Date.now();
        tasks.push((async()=>{
          const body=await response.body();
          audioResponses.push({output_id:p.split('/').at(-1),http_status:response.status(),at,
            bytes:body.length,sha256:crypto.createHash('sha256').update(body).digest('hex'),content_type:response.headers()['content-type']});
        })());
      }
    });
    await page.goto(input.url+'/dashboard/presence');
    await page.locator('#username').fill(input.username);await page.locator('#password').fill(input.password);
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/login'&&r.request().method()==='POST'),page.locator('button[type=submit]').click()]);
    await page.waitForURL(input.url+'/dashboard/monitor');await page.goto(input.url+'/dashboard/presence');
    await page.locator('#privateConfirm').check();
    await page.waitForFunction(()=>document.getElementById('connectionStatus').textContent.startsWith('Connected privately.'));
    assert(consumer);
    await page.locator('#ownerSource').check();
    async function post(route,body){const response=await page.request.post(input.url+route,{data:body,timeout:remaining()});assert.equal(response.status(),200,await response.text());return response.json();}
    async function status(){const response=await page.request.get(input.url+prefix+'/status',{params:{audience:'owner_private',consumer_id:consumer},timeout:Math.min(5000,remaining())});assert.equal(response.status(),200,await response.text());return response.json();}
    const envelope=()=>({audience:'owner_private',consumer_id:consumer,request_id:crypto.randomUUID()});
    await page.locator('#muteToggle').click();await page.waitForFunction(()=>document.getElementById('muteToggle').textContent==='Mute speech');
    await post(prefix+'/control',{...envelope(),action:'voice_enabled',value:true});
    const requestId=crypto.randomUUID();
    inputStartedAt=Date.now();
    const receipt=await post(prefix+'/audio-input',{...envelope(),request_id:requestId,session_id:'real-audio-browser-proof',
      audio_base64:input.audio_base64,observation:{channel:'private_voice',speaker:'owner',address_cue:'none',confidence:.5}});
    inputCompletedAt=Date.now();
    assert.equal(receipt.status,'generated',JSON.stringify({status:receipt.status,reason:receipt.reason}));
    const outputIds=receipt.output_ids||[receipt.output_id];
    assert(outputIds.length>=1&&outputIds.length<=4&&outputIds.every(Boolean));
    let final;
    while(Date.now()<deadline){
      final=await status();
      if(outputIds.every(outputId=>final.outputs.some(o=>o.id===outputId&&o.status==='played')))break;
      const failed=final.outputs.filter(o=>outputIds.includes(o.id)&&['failed','cancelled','expired'].includes(o.status));
      assert.equal(failed.length,0,JSON.stringify(failed.map(o=>({id:o.id,status:o.status,reason:o.reason}))));
      await page.waitForTimeout(Math.min(100,remaining()));
    }
    assert(final&&outputIds.every(outputId=>final.outputs.some(o=>o.id===outputId&&o.status==='played')),'not all returned output chunks played within180s');
    await Promise.all(tasks);
    const events=await page.evaluate(()=>window.__presenceAudioEvents);
    const captures=await page.evaluate(()=>window.__presenceMicrophoneCalls);
    assert.equal(captures,0,'driver unexpectedly captured a microphone');
    const outputs=outputIds.map(id=>final.outputs.find(o=>o.id===id));
    outputs.forEach((output,index)=>{
      assert.equal(output.utterance_id,requestId);assert.equal(output.chunk_index,index);
      assert.equal(output.chunk_count,outputIds.length);assert.equal(output.final_chunk,index===outputIds.length-1);
      const responses=audioResponses.filter(r=>r.output_id===output.id);
      assert.equal(responses.length,1,'duplicate audio fetch');assert.equal(responses[0].http_status,200);
      assert(responses[0].bytes>44&&responses[0].content_type.startsWith('audio/'));
    });
    const actual=ackRequests.filter(a=>outputIds.includes(a.output_id));
    const expected=outputIds.flatMap(id=>[[id,'started'],[id,'played']]);
    assert.deepEqual(actual.map(a=>[a.output_id,a.status]),expected,'audio ACK order changed');
    for(const [outputId,state] of expected){const responses=ackResponses.filter(r=>r.output_id===outputId&&r.status===state);assert.equal(responses.length,1);assert.equal(responses[0].http_status,200);}
    const playing=events.filter(e=>e.kind==='playing'),ended=events.filter(e=>e.kind==='ended');
    assert.equal(playing.length,outputIds.length);assert.equal(ended.length,outputIds.length);
    assert.equal(errors.length,0,errors.join(';'));
    if(input.screenshot)await page.screenshot({path:input.screenshot,fullPage:true});
    await page.locator('#privateConfirm').uncheck();await page.waitForTimeout(300);
    assert.equal(await page.locator('#caption').innerText(),'');
    const relative=value=>value-inputStartedAt;
    const report={complete:true,scope:'actual authenticated isolated Core/model/STT/TTS/browser Audio with supplied controlled WAV',
      request_id:requestId,input_sha256:crypto.createHash('sha256').update(inputBytes).digest('hex'),
      chunk_count:outputIds.length,outputs:outputs.map(o=>({id:o.id,utterance_id:o.utterance_id,chunk_index:o.chunk_index,
        chunk_count:o.chunk_count,final_chunk:o.final_chunk,status:o.status,speech_text:o.speech_text,display_text:o.display_text})),
      input_completed_ms:relative(inputCompletedAt),first_fetch_ms:relative(Math.min(...audioResponses.map(r=>r.at))),
      first_audio_playing_ms:relative(playing[0].at),last_audio_ended_ms:relative(ended.at(-1).at),
      first_started_before_audio_input_completed:outputIds.length>1?playing[0].at<inputCompletedAt:null,
      audio_responses:audioResponses.map(r=>({...r,at_ms:relative(r.at),at:undefined})),
      audio_events:events.map(e=>({kind:e.kind,at_ms:relative(e.at)})),
      ack_http:ackResponses.filter(r=>outputIds.includes(r.output_id)).map(r=>({...r,at_ms:relative(r.at),at:undefined})),
      ordered_once:true,no_microphone_capture:true,real_browser_audio_events:true,provider_overrides_in_driver:false,
      physical_speaker:false,human_listening:false,errors};
    process.stdout.write(JSON.stringify(report));
  }finally{await browser.close();}
}
main().catch(error=>{process.stderr.write(String(error.stack||error));process.exitCode=1;});
