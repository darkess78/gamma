/* Actual private growth review UI. Credentials arrive only over stdin. */
const fs=require('node:fs'),path=require('node:path');
async function main(){
  let raw='';for await(const chunk of process.stdin)raw+=chunk;const input=JSON.parse(raw);
  const links='/home/neety/.cache/ms-playwright/.links';
  const driver=fs.readdirSync(links).map(n=>fs.readFileSync(path.join(links,n),'utf8').trim()).find(p=>fs.existsSync(path.join(p,'package.json')));
  const {chromium}=require(driver),browser=await chromium.launch({headless:true});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1100}}),page=await context.newPage();
    const errors=[],requests=[];page.on('pageerror',e=>errors.push(String(e)));
    page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))requests.push(new URL(r.url()).pathname);});
    await page.goto(input.url+'/dashboard/growth');
    await page.locator('#username').fill(input.username);await page.locator('#password').fill(input.password);
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/login'&&r.request().method()==='POST'),page.locator('button[type=submit]').click()]);
    await page.waitForURL(input.url+'/dashboard/monitor');await page.goto(input.url+'/dashboard/growth');
    if(requests.includes('/api/coordination/growth'))throw Error('Private state fetched without physical audience confirmation');
    await page.locator('#privateConfirm').check();
    await page.waitForFunction(v=>{const text=document.getElementById('growthDetail').textContent;return text.includes(v.trial_id)&&text.includes(v.hypothesis_id);},input);
    const detail=JSON.parse(await page.locator('#growthDetail').innerText());
    if(!detail.hypotheses.some(h=>h.id===input.hypothesis_id&&h.status==='revised'))throw Error('Revised wrong lesson missing from actual UI');
    if(!detail.trials.some(t=>t.id===input.trial_id&&t.status==='withdrawn'))throw Error('Withdrawn versioned trial missing from actual UI');
    await page.locator('#privateConfirm').uncheck();
    if((await page.locator('#growthDetail').innerText()).trim())throw Error('Private growth details remain in DOM');
    if(await page.locator('#growthState').isVisible())throw Error('Private controls remain visible');
    await page.screenshot({path:input.screenshot,fullPage:true});
    if(errors.length)throw Error(errors.join(';'));
    process.stdout.write(JSON.stringify({ok:true,errors,requests,revised_history_visible:true,versioned_trial_visible:true,private_dom_cleared:true}));
  }finally{await browser.close();}
}
main().catch(e=>{process.stderr.write(String(e));process.exitCode=1;});
