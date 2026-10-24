const assert = require('node:assert/strict');
const {chromium} = require(process.env.GAMMA_PLAYWRIGHT_PACKAGE || '/home/neety/.local/lib/python3.13/site-packages/playwright/driver/package');
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});
  try {
    const context=await browser.newContext();
    await context.request.post(process.env.FIXTURE_BASE+'/login',{form:{username:'fixture',password:process.env.FIXTURE_PASSWORD}});
    const page=await context.newPage(); const errors=[]; const frames=[];
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('websocket',socket=>socket.on('framereceived',event=>{
      try {frames.push(JSON.parse(String(event.payload)));} catch(_) {}
    }));
    await page.addInitScript(()=>{
      navigator.mediaDevices.getUserMedia=async()=>{
        const context=new AudioContext(); const output=context.createMediaStreamDestination();
        const oscillator=context.createOscillator(); oscillator.frequency.value=300;
        oscillator.connect(output); await context.resume(); oscillator.start(); oscillator.stop(context.currentTime+1.5);
        return output.stream;
      };
    });
    await page.goto(process.env.FIXTURE_BASE+'/dashboard/workers');
    await page.locator('#brain-pause').click();
    await page.waitForFunction(()=>document.querySelector('#brain-status').textContent.startsWith('paused.'));
    const rejected=await context.request.post(process.env.FIXTURE_API+'/v1/conversation/respond',{
      headers:{Authorization:`Bearer ${process.env.FIXTURE_TOKEN}`},data:{user_text:'Fictional paused request',synthesize_speech:false}});
    assert.equal(rejected.status(),503);
    assert.equal((await context.request.post(process.env.FIXTURE_API+'/v1/brain/resume')).status(),401);
    await page.locator('#brain-resume').click();
    await page.waitForFunction(()=>!document.querySelector('#brain-pause').disabled);
    await page.goto(process.env.FIXTURE_BASE+'/dashboard');
    const overflow=await page.evaluate(()=>new Promise((resolve,reject)=>{
      const socket=new WebSocket('ws://'+location.host+'/api/voice/live');
      const timer=setTimeout(()=>{socket.close();reject(new Error('Missing live audio limit error'));},5000);
      socket.onopen=()=>{
        socket.send(JSON.stringify({type:'start_turn'}));
        for(let i=0;i<3;i++) socket.send(new Uint8Array(131072));
      };
      socket.onmessage=event=>{
        const message=JSON.parse(event.data);
        if(message.code==='audio_limit'){clearTimeout(timer);socket.close();resolve(true);}
      };
    }));
    assert.equal(overflow,true);
    await page.evaluate(()=>{
      document.querySelector('#voiceSessionId').value='browser-fixture';
      document.querySelector('#liveBargeInEnabled').checked=false;
      window.toggleLiveVoice();
    });
    await page.waitForFunction(()=>{
      const audio=document.querySelector('#voicePlayback'); return audio && !audio.paused && audio.currentTime>0.1;
    },{},{timeout:30000});
    const playing=await page.locator('#voicePlayback').evaluate(audio=>({src:audio.currentSrc,time:audio.currentTime,duration:audio.duration}));
    assert.ok(playing.src.startsWith('blob:')); assert.ok(playing.duration>=11);
    const offer=frames.find(frame=>frame.type==='speech_chunk_ready' && frame.source==='core_live');
    assert.ok(offer?.playback?.grant_id);
    const response=await context.request.post(process.env.FIXTURE_API+`/v1/delivery/${offer.playback.grant_id}/cancel`,{
      headers:{Authorization:`Bearer ${process.env.FIXTURE_TOKEN}`}});
    assert.equal(response.status(),200);
    await page.waitForFunction(()=>{const audio=document.querySelector('#voicePlayback');return audio.paused && !audio.getAttribute('src');},{},{timeout:5000});
    assert.ok(frames.some(frame=>frame.type==='playback_revoked'));
    await page.evaluate(()=>window.stopLiveVoice());
    await page.waitForFunction(()=>document.querySelector('#liveVoiceButton').textContent==='Start Live');
    await page.evaluate(()=>window.toggleLiveVoice());
    await page.waitForFunction(()=>{
      const audio=document.querySelector('#voicePlayback'); return !audio.paused && audio.currentTime>0.1;
    },{},{timeout:30000});
    await page.waitForFunction(()=>{
      const audio=document.querySelector('#voicePlayback'); return audio.paused && !audio.getAttribute('src');
    },{},{timeout:18000});
    await page.evaluate(()=>window.stopLiveVoice());
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({scope:'Chromium synthetic microphone and reply tone',live_audio_limit:true,brain_pause_resume:true,paused_ingress_status:503,playing,revoked:true,natural_completion:true,page_errors:errors},null,2));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
