/* Real isolated browser path. Credentials arrive on stdin and are never printed. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
async function main(){
  let raw='';for await(const part of process.stdin)raw+=part;
  const input=JSON.parse(raw);
  const links='/home/neety/.cache/ms-playwright/.links';
  const driver=fs.readdirSync(links).map(n=>fs.readFileSync(path.join(links,n),'utf8').trim()).find(p=>fs.existsSync(path.join(p,'package.json')));
  const {chromium}=require(driver);
  const browser=await chromium.launch({headless:true});
  try{
    const context=await browser.newContext({viewport:{width:800,height:950}}),page=await context.newPage();
    const errors=[],requests=[],acknowledgments=[];
    page.on('pageerror',e=>errors.push(String(e)));
    page.on('request',r=>{const p=new URL(r.url()).pathname;if(p.startsWith('/api/'))requests.push(p);if(p.endsWith('/interaction/ack'))acknowledgments.push(JSON.parse(r.postData()||'{}'));});
    await page.goto(input.url+'/dashboard/presence');
    if(await page.locator('#username').isVisible()){
      await page.locator('#username').fill(input.username);await page.locator('#password').fill(input.password);
      await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/login'&&r.request().method()==='POST'),page.locator('button[type=submit]').click()]);
      await page.waitForURL(input.url+'/dashboard/monitor');await page.goto(input.url+'/dashboard/presence');
    }
    assert.equal(requests.filter(p=>p.includes('/interaction/')).length,0,'private API was called before confirmation');
    await page.locator('#privateConfirm').check();
    await page.waitForFunction(()=>document.querySelector('#connectionStatus').textContent.startsWith('Connected privately.'));
    assert.equal(await page.locator('#ownerSource').isChecked(),false);
    assert.equal(await page.locator('#voiceToggle').innerText(),'Start listening');
    assert.equal(requests.includes('/api/coordination/interaction/audio-input'),false,'capture started automatically');
    const before=await page.locator('#availability').innerText();
    await page.locator('#avatarToggle').click();
    assert.equal(await page.locator('#avatar').isVisible(),false);
    assert.equal(await page.locator('#availability').innerText(),before);
    await page.locator('#placement').selectOption('left');
    assert.equal(await page.locator('body').getAttribute('data-placement'),'left');
    await page.locator('#compactToggle').click();
    assert.equal(await page.locator('#textForm').isVisible(),false);
    await page.locator('#compactToggle').click();
    if(input.probe_text){
      await page.locator('#privateText').fill(input.probe_text);
      await page.locator('#sendText').click();
      await page.waitForFunction(()=>document.querySelector('#inputStatus').textContent==='Turn processed.',{},{timeout:180000});
      await page.waitForFunction(()=>document.querySelector('#caption').textContent.length>0,{},{timeout:30000});
    }
    await page.locator('#avatarToggle').click();
    if(input.screenshot)await page.screenshot({path:input.screenshot,fullPage:true});
    await page.locator('#privateConfirm').uncheck();
    assert.equal(await page.locator('#caption').innerText(),'');
    assert.equal(await page.locator('#privateText').inputValue(),'');
    assert.equal(await page.locator('#presenceControls').isVisible(),false);
    await page.waitForTimeout(700);
    assert.equal(await page.locator('#caption').innerText(),'','late result recreated private text');
    assert.equal(errors.length,0,errors.join(';'));
    process.stdout.write(JSON.stringify({ok:true,errors,requests,acknowledgments,
      no_auto_capture:true,hidden_avatar_kept_state:true,local_placement:true,
      private_dom_cleared:true,subjective_avatar_acceptance:false,audible_naturalness_assessed:false}));
  }finally{await browser.close();}
}
main().catch(error=>{process.stderr.write(String(error));process.exitCode=1;});
