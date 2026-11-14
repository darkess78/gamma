/* Actual private Monitor UI driver. Credentials stay on stdin and are never logged. */
const {createInterface} = require('node:readline');
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const links = '/home/neety/.cache/ms-playwright/.links';
  const candidates = fs.readdirSync(links).map(n => fs.readFileSync(path.join(links,n),'utf8').trim());
  const driver = candidates.find(p => fs.existsSync(path.join(p,'package.json')));
  if (!driver) throw Error('No installed Playwright driver');
  const {chromium} = require(driver);
  const browser = await chromium.launch({headless:true});
  let context, page;
  const requests = [], errors = [], sockets = [], controls = [];
  for await (const line of createInterface({input:process.stdin})) {
    try {
      const value = JSON.parse(line);
      if (value.op === 'start') {
        if (context) await context.close();
        context = await browser.newContext({viewport:{width:1440,height:1100}});
        page = await context.newPage();
        page.on('request', r => {const p=new URL(r.url()).pathname;if(p.startsWith('/api/'))requests.push(p);if(p==='/api/coordination/control'&&r.method()==='POST')controls.push(r.postDataJSON());});
        page.on('pageerror', e => errors.push(String(e)));
        page.on('websocket', w => sockets.push(new URL(w.url()).pathname));
        await page.goto(value.url+'/dashboard/monitor');
        await page.locator('#username').fill(value.username);
        await page.locator('#password').fill(value.password);
        await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/login' && r.request().method()==='POST'),page.locator('button[type=submit]').click()]);
        await page.waitForURL(value.url+'/dashboard/monitor');
        await page.locator('#monitorPrivateAudience').selectOption('owner_private');
        if(await page.locator('#monitorInputMode').inputValue()!=='private_text')throw Error('Private mode not selected');
        process.stdout.write(JSON.stringify({ok:true})+'\n');
      } else if (value.op === 'chat') {
        await page.locator('#monitorInputText').fill(value.text);
        const [response] = await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/monitor/input', {timeout:180000}),page.locator('button[onclick="submitMonitorInput()"]').click()]);
        const body = await response.json();
        if(!response.ok())throw Error('Private input HTTP '+response.status()+': '+JSON.stringify(body));
        const request = response.request().postDataJSON();
        await page.waitForFunction(()=>!document.getElementById('monitorInputStatus').textContent.startsWith('Sending'));
        process.stdout.write(JSON.stringify({ok:true,body,request,visible:await page.locator('#subtitleDisplay').innerText()})+'\n');
      } else if(value.op === 'control') {
        if(!['sleep','wake','focus','pause','resume','stop'].includes(value.action))throw Error('Unsupported probe control');
        if(value.target){
          await page.waitForFunction(id=>Array.from(document.getElementById('coordinationActivityTarget').options).some(o=>o.value===id),value.target);
          await page.locator('#coordinationActivityTarget').selectOption(value.target);
        }
        const call = value.action === 'focus' ? `coordinationAction('focus', '${value.focus}')` : `coordinationAction('${value.action}')`;
        if(value.expect_no_request){
          const count=controls.length;
          await page.locator(`button[onclick="${call}"]`).click();
          await page.waitForFunction(()=>(document.getElementById('coordinationControlStatus')||document.getElementById('coordinationStatus')).textContent.includes('Select an unfinished activity'));
          if(controls.length!==count)throw Error('Ambiguous control was sent');
          process.stdout.write(JSON.stringify({ok:true,visible:await page.locator('#coordinationStatus').innerText()})+'\n');
          continue;
        }
        const [response] = await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/coordination/control'),page.locator(`button[onclick="${call}"]`).click()]);
        const body = await response.json();
        if(!response.ok())throw Error('Control HTTP '+response.status());
        if(value.expected)await page.waitForFunction(expected=>document.getElementById('coordinationStatus').textContent.includes(expected),value.expected);
        process.stdout.write(JSON.stringify({ok:true,body,request:response.request().postDataJSON(),visible:await page.locator('#coordinationStatus').innerText()})+'\n');
      } else if(value.op === 'audience_visibility') {
        await page.locator('#monitorInputText').fill('Unsent private fixture');
        await page.locator('#monitorPrivateAudience').selectOption('unknown');
        if(await page.locator('#monitorInputText').isVisible())throw Error('Private draft remains visible');
        if((await page.locator('#subtitleDisplay').innerText()).trim())throw Error('Private reply remains visible');
        if(!(await page.locator('#coordinationStatus').innerText()).includes('not confirmed'))throw Error('Private activity details remain visible');
        await page.locator('#monitorPrivateAudience').selectOption('owner_private');
        if(!(await page.locator('#monitorInputText').isVisible())||await page.locator('#monitorInputText').inputValue()!=='Unsent private fixture')throw Error('Private draft was lost');
        await page.waitForFunction(id=>Array.from(document.getElementById('coordinationActivityTarget').options).some(o=>o.value===id),value.restore_target);
        await page.locator('#coordinationActivityTarget').selectOption(value.restore_target);
        process.stdout.write(JSON.stringify({ok:true,private_output_hidden_and_draft_retained:true})+'\n');
      } else if(value.op === 'activity_observation') {
        await page.waitForFunction(value=>{
          const select=document.getElementById('coordinationActivityTarget');
          return select.value===value.id && Array.from(select.options).some(o=>o.value===value.id&&o.textContent.includes(`| ${value.status} |`));
        },value);
        process.stdout.write(JSON.stringify({ok:true,selected:await page.locator('#coordinationActivityTarget').inputValue()})+'\n');
      } else if(value.op === 'finish') {
        await page.screenshot({path:value.screenshot,fullPage:true});
        await browser.close();
        process.stdout.write(JSON.stringify({ok:true,requests,errors,sockets,controls})+'\n');
        return;
      } else throw Error('Unknown probe operation');
    } catch(e) {process.stdout.write(JSON.stringify({ok:false,error:String(e)})+'\n');}
  }
  await browser.close();
}
main().catch(e=>{process.stderr.write(String(e));process.exitCode=1;});
