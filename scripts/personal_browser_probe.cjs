/* Fictional private-state journey in the real candidate DOM. Credentials stdin only. */
const fs=require('node:fs'); const path=require('node:path');
async function main(){
  const input=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>resolve(s));}));
  const links='/home/neety/.cache/ms-playwright/.links';
  const driver=fs.readdirSync(links).map(n=>fs.readFileSync(path.join(links,n),'utf8').trim()).find(p=>fs.existsSync(path.join(p,'package.json')));
  const {chromium}=require(driver); const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:1000}});const errors=[];let gets=0;
  page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>{if(r.method()==='GET'&&new URL(r.url()).pathname==='/api/coordination/personal')gets++;});
  const checks={};
  try{
    await page.goto(input.url+'/dashboard/personal');
    await page.locator('#username').fill(input.username);await page.locator('#password').fill(input.password);
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/login'&&r.request().method()==='POST'),page.locator('button[type=submit]').click()]);
    await page.goto(input.url+'/dashboard/personal');
    if(gets!==0||await page.locator('#personalState').isVisible())throw Error('State fetched or shown without private confirmation');checks.explicit_private_confirmation=true;
    await page.locator('#privateConfirm').check();await page.locator('#personalState').waitFor({state:'visible'});
    async function submit(form){
      const [r]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/coordination/personal'&&r.request().method()==='POST'),page.locator('#'+form+' button').first().click()]);
      if(!r.ok())throw Error('Owner form failed '+r.status());
      await page.waitForFunction(()=>document.getElementById('notice').textContent==='Saved in the isolated candidate.');
      return r.json();
    }
    await page.locator('#identitySpeech').fill('Dry, short, concrete sentences.');await submit('identityForm');checks.authored_edit_actual_form=true;
    await page.locator('#memoryTopic').fill('fictional browser note');await page.locator('#memoryText').fill('The fictional kite is copper.');
    const saved=await submit('memoryForm');const mid=saved.id;
    await page.locator(`[data-memory-id="${mid}"]`).getByText('Correct',{exact:true}).click();
    await page.locator('#memoryText').fill('The fictional kite is silver.');await submit('memoryForm');
    await page.locator(`[data-memory-id="${mid}"]`).getByText('silver',{exact:false}).waitFor();checks.memory_create_correct=true;
    const [forgot]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/coordination/personal'),page.locator(`[data-memory-id="${mid}"]`).getByText('Forget',{exact:true}).click()]);
    if(!forgot.ok())throw Error('Forget failed');await page.waitForFunction(()=>document.getElementById('notice').textContent==='Saved in the isolated candidate.');checks.forget_actual_form=true;
    await page.locator('#personName').fill('Fictional Mira');await page.locator('#personRelationship').fill("Familiar to Shana. Shana disagrees with Mira's risky shortcuts.");
    const person=await submit('personForm');await page.locator('#accountPerson').selectOption(person.person_id);
    await page.locator('#accountPlatform').fill('fictional-test');await page.locator('#accountId').fill('candidate-only-673');
    await page.locator('#accountConfirmed').check();await submit('accountForm');checks.exact_confirmed_link=true;
    await page.locator('#privateConfirm').uncheck();if(await page.locator('#personalState').isVisible()||await page.locator('#memoryList').innerText())throw Error('Private state remained visible');checks.hide_clears_private_dom=true;
    await page.screenshot({path:input.screenshot,fullPage:true});
    if(errors.length)throw Error('Browser JavaScript error');checks.no_javascript_errors=true;
    process.stdout.write(JSON.stringify({ok:true,checks}));
  }finally{await browser.close();}
}
main().catch(e=>{process.stderr.write(String(e));process.exitCode=1;});
