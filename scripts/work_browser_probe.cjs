/* Exercise the actual private P3 review page. Credentials remain on stdin. */
const fs = require('node:fs');
const path = require('node:path');
let stage='initialization';
async function main() {
  let raw='';for await (const chunk of process.stdin)raw+=chunk;
  const input=JSON.parse(raw);
  const links='/home/neety/.cache/ms-playwright/.links';
  const driver=fs.readdirSync(links).map(name=>fs.readFileSync(path.join(links,name),'utf8').trim())
    .find(folder=>fs.existsSync(path.join(folder,'package.json')));
  if(!driver)throw Error('Installed browser driver unavailable');
  const {chromium}=require(driver);
  const browser=await chromium.launch({headless:true});
  try {
    const context=await browser.newContext({viewport:{width:1440,height:1100}});
    const page=await context.newPage();const errors=[],requests=[];
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('request',request=>{const url=new URL(request.url());if(url.pathname.startsWith('/api/'))requests.push(url.pathname);});
    stage='login';await page.goto(input.url+'/dashboard/work');
    await page.locator('#username').fill(input.username);await page.locator('#password').fill(input.password);
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/login'&&r.request().method()==='POST'),
      page.locator('button[type=submit]').click()]);
    await page.waitForURL(input.url+'/dashboard/monitor');
    stage='open_work';
    await page.goto(input.url+'/dashboard/work');
    if(requests.some(p=>p.startsWith('/api/coordination/work')))throw Error('Private work fetched without audience confirmation');
    stage='confirm_private_audience';
    let releaseInitialList;
    const initialListGate=new Promise(resolve=>{releaseInitialList=resolve;});
    await page.route(url=>url.pathname==='/api/coordination/work'&&!url.searchParams.has('task_id'),async route=>{
      await initialListGate;await route.continue();
    });
    const listResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/coordination/work'&&r.request().method()==='GET');
    await page.locator('#privateConfirm').check();
    if(!await page.locator('#prepareWork').isDisabled()||!await page.locator('#refreshWork').isDisabled()||!await page.locator('#downloadWork').isDisabled())
      throw Error('Task buttons remain clickable while initial private request is pending');
    releaseInitialList();const loaded=await listResponse;
    if(!loaded.ok())throw Error('Private task list was not loaded');
    await page.waitForFunction(()=>document.getElementById('notice').textContent==='Private isolated work loaded.');
    stage='prepare_research_without_start';
    await page.locator('#workSpec').fill(JSON.stringify({mode:'research',goal:'Review the selected browser-staged note without implementing anything.',
      files:{'note.txt':'The owner is staging a reviewable research task. No build or start was requested.'},editable:[],checks:[]}));
    const [prepared]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/coordination/work'&&r.request().method()==='POST'),
      page.locator('#prepareWork').click()]);
    const staged=await prepared.json();if(!prepared.ok()||staged.activity.status!=='ready')throw Error('Browser staging did not preserve clear start boundary');
    await page.waitForFunction(id=>document.getElementById('workDetail').textContent.includes(id),staged.activity.id);
    stage='inspect_candidate';
    const card=page.locator(`#workList article[data-task-id="${input.task_id}"]`);
    await card.waitFor({state:'visible'});
    await card.getByRole('button',{name:'Inspect candidate'}).click();
    await page.waitForFunction(()=>document.getElementById('workDetail').textContent.includes('tasks.py'));
    const visible=await page.locator('#workDetail').innerText();
    if(!visible.includes('verified_candidate'))throw Error('Verified candidate missing from actual DOM');
    const inspected=JSON.parse(visible);
    if(inspected.activity?.id!==input.task_id||!/^[a-f0-9]{64}$/.test(inspected.checkpoint?.source_sha256||''))
      throw Error('Inspected candidate is not the exact selected task');
    stage='download_candidate';
    const download=await Promise.all([page.waitForEvent('download'),page.locator('#downloadWork').click()]);
    const downloaded=download[0];
    const filename=await downloaded.path();const artifact=JSON.parse(fs.readFileSync(filename,'utf8'));
    if(!artifact.files||!artifact.files['tasks.py'])throw Error('Downloaded candidate source missing');
    if(artifact.activity?.id!==input.task_id||artifact.checkpoint?.source_sha256!==inspected.checkpoint.source_sha256
      ||JSON.stringify(artifact.files)!==JSON.stringify(inspected.files))throw Error('Downloaded candidate differs from inspected source');
    if(input.download_path)fs.writeFileSync(input.download_path,fs.readFileSync(filename));
    stage='hide_private_data';await page.locator('#privateConfirm').uncheck();
    if((await page.locator('#workDetail').innerText()).trim())throw Error('Private candidate detail remains visible');
    if((await page.locator('#workList').innerText()).trim())throw Error('Private task list remains visible');
    await page.screenshot({path:input.screenshot,fullPage:true});
    let reply=null;
    if(!input.artifact_only){
      stage='monitor_checked_status';await page.goto(input.url+'/dashboard/monitor');
      await page.locator('#monitorPrivateAudience').selectOption('owner_private');
      await page.locator('#monitorInputText').fill('What is the checked outcome of the changed task tracker work '+input.task_id+'? Use its actual work activity result.');
      const [spoken]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/monitor/input',{timeout:180000}),
        page.locator('button[onclick="submitMonitorInput()"]').click()]);
      reply=(await spoken.json()).result.assistant_response;
      if(!spoken.ok()||!reply.display_text.includes(input.task_id)||reply.speech_text)throw Error('Actual Monitor work receipt failed');
    }
    if(errors.length)throw Error('Browser JavaScript errors: '+errors.join(';'));
    process.stdout.write(JSON.stringify({ok:true,requests,errors,candidate_downloaded:true,
      private_dom_cleared:true,loading_controls_disabled:true,browser_staged_task_id:staged.activity.id,monitor_receipt:reply,
      monitor_chat_verified_here:!input.artifact_only,exact_download_source:true,
      download_source_sha256:artifact.checkpoint.source_sha256}));
  } finally {await browser.close();}
}
main().catch(error=>{process.stderr.write(stage+': '+String(error));process.exitCode=1;});
