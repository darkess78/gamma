const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.GAMMA_PLAYWRIGHT_PACKAGE || '/home/neety/.local/lib/python3.13/site-packages/playwright/driver/package');
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});
  try {
    const context=await browser.newContext();
    await context.request.post(process.env.FIXTURE_BASE+'/login',{form:{username:'fixture',password:process.env.FIXTURE_PASSWORD}});
    const api=async(path,data)=>{
      const options={headers:{Authorization:`Bearer ${process.env.FIXTURE_TOKEN}`}};
      const response=data===undefined ? await context.request.get(process.env.FIXTURE_API+path,options)
        : await context.request.post(process.env.FIXTURE_API+path,{...options,data});
      assert.equal(response.status(),200,`API ${path}: ${response.status()}`);
      return response.json();
    };
    const page=await context.newPage(), errors=[], frames=[], sent=[];
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('websocket',socket=>{
      socket.on('framereceived',event=>{try {frames.push(JSON.parse(String(event.payload)));} catch(_) {}});
      socket.on('framesent',event=>{try {sent.push(JSON.parse(String(event.payload)));} catch(_) {}});
    });
    await page.addInitScript(encoded=>{
      navigator.mediaDevices.getUserMedia=async()=>{
        const context=new AudioContext(), destination=context.createMediaStreamDestination();
        const bytes=Uint8Array.from(atob(encoded),char=>char.charCodeAt(0));
        const source=context.createBufferSource();
        source.buffer=await context.decodeAudioData(bytes.buffer);
        source.connect(destination); await context.resume(); source.start();
        return destination.stream;
      };
    },fs.readFileSync(process.env.FIXTURE_WAV).toString('base64'));
    await page.goto(process.env.FIXTURE_BASE+'/dashboard');
    await page.evaluate(()=>{
      document.querySelector('#voiceSessionId').value='real-audio-acceptance';
      document.querySelector('#liveBargeInEnabled').checked=false;
      document.querySelector('#voiceSynthesizeSpeech').checked=true;
    });
    const brain=await api('/v1/brain/status');
    const identity={pid:brain.worker.pid,generation:brain.worker.generation};
    const brainUnchanged=async()=>{
      const state=await api('/v1/brain/status');
      assert.equal(state.worker.pid,identity.pid); assert.equal(state.worker.generation,identity.generation);
    };
    async function start(){await page.evaluate(()=>window.toggleLiveVoice());}
    async function stop(){
      await page.evaluate(()=>window.stopLiveVoice());
      await page.waitForFunction(()=>document.querySelector('#liveVoiceButton').textContent==='Start Live');
    }
    async function resultAfter(offset){
      for(let i=0;i<1200;i++){
        const result=frames.slice(offset).find(frame=>frame.type==='turn_result');
        if(result) return result;
        const failure=frames.slice(offset).find(frame=>frame.type==='error');
        if(failure) throw new Error('Live turn failed: '+JSON.stringify(failure));
        await new Promise(resolve=>setTimeout(resolve,100));
      }
      throw new Error('No live result');
    }
    async function playing(){
      await page.waitForFunction(()=>{const a=document.querySelector('#voicePlayback'); return !a.paused&&a.currentTime>.05;}, {}, {timeout:120000});
    }
    const spoken=[];
    let offset=frames.length;
    await start();
    let [result]=await Promise.all([resultAfter(offset),playing()]); spoken.push(result.turn_id);
    assert.equal(sent.filter(frame=>frame.type==='end_turn').length,1,'VAD must finalize only once');
    assert.equal(frames.slice(offset).filter(frame=>frame.type==='turn_submitting').length,1);
    const first=await api('/v1/voice/live/'+result.turn_id);
    assert.ok(first.decision_trace_id&&first.delivery_grant&&first.playback_expected);
    assert.match(first.transcript,/quiet garden/i);
    await page.waitForFunction(()=>{const a=document.querySelector('#voicePlayback');return a.paused&&!a.getAttribute('src');},{},{timeout:45000});
    await stop();
    const sttBefore=await api('/v1/voice/stt/status');
    const stopped=await api('/v1/voice/tts/stop',{});
    assert.equal(stopped.enabled,false);
    offset=frames.length;
    await start(); result=await resultAfter(offset);
    const textOnly=await api('/v1/voice/live/'+result.turn_id);
    assert.ok(textOnly.transcript&&textOnly.reply_text);
    assert.equal(textOnly.playback_expected,false);
    assert.equal(frames.slice(offset).filter(frame=>frame.type==='speech_chunk_ready').length,0);
    await stop();
    assert.equal((await api('/v1/voice/stt/status')).pid,sttBefore.pid);
    const restarted=await api('/v1/voice/stt/restart',{});
    assert.notEqual(restarted.pid,sttBefore.pid);
    await brainUnchanged();
    const text=await api('/v1/conversation/respond',{user_text:'A fictional independent text check.',session_id:'independent-text',synthesize_speech:false,fast_mode:true});
    assert.ok(text.display_text); await brainUnchanged();
    const crashed=await api('/fixture/stt/crash',{});
    assert.equal(crashed.reaped,true);
    await brainUnchanged();
    await api('/v1/voice/tts/start',{});
    offset=frames.length;
    await start();
    [result]=await Promise.all([resultAfter(offset),playing()]); spoken.push(result.turn_id);
    await api('/v1/voice/live/'+result.turn_id+'/cancel',{reason:'acceptance_interruption'});
    await page.waitForFunction(()=>{const a=document.querySelector('#voicePlayback');return a.paused&&!a.getAttribute('src');},{},{timeout:5000});
    assert.ok(frames.slice(offset).some(frame=>frame.type==='playback_revoked'));
    await stop();
    await brainUnchanged();
    assert.notEqual((await api('/v1/voice/stt/status')).pid,crashed.pid);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({spoken_turns:spoken,synthetic_capture:true,one_final_per_capture:true,
      tts_stop_keeps_transcription_and_text:true,stt_restart_keeps_brain:true,
      stt_crash_recovered_without_brain_restart:true,natural_completion:true,interrupted_playback_stopped:true,page_errors:errors}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
