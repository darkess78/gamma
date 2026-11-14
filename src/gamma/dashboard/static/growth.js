/* Private owner review of evidence and reversible growth trials. */
'use strict';
(() => {
  const $=id=>document.getElementById(id);
  let epoch=0,busy=false,refreshPending=false;
  function controls(value) {
    busy=value;
    const disabled=busy||!$('privateConfirm').checked||$('growthState').hidden;
    for(const button of document.querySelectorAll('#growthState button'))button.disabled=disabled;
    $('growthState').setAttribute('aria-busy',String(busy));
  }
  function clear() {
    epoch+=1;refreshPending=false;$('growthState').hidden=true;
    $('growthDetail').textContent='';$('growthValues').value='{}';controls(busy);
  }
  const current=value=>value===epoch&&$('privateConfirm').checked;
  async function request(method,payload) {
    if(!$('privateConfirm').checked)throw Error('Confirm a private audience first.');
    const response=await fetch('/api/coordination/growth'+(method==='GET'?'?audience=owner_private':''),{
      method,credentials:'same-origin',headers:{'Content-Type':'application/json'},
      body:method==='GET'?undefined:JSON.stringify(payload)});
    if(response.status===401||response.redirected){$('privateConfirm').checked=false;clear();throw Error('Sign in again to view private growth state.');}
    if(!response.ok)throw Error('The action was not accepted. Check its source, scope, evidence, version and remaining permission.');
    return response.json();
  }
  async function refresh() {
    const version=epoch,value=await request('GET');
    if(current(version))$('growthDetail').textContent=JSON.stringify(value,null,2);
  }
  async function operate(action) {
    if(busy||!$('privateConfirm').checked)return;
    const version=epoch;controls(true);$('notice').textContent='Loading private growth state.';
    try{await action();if(current(version))$('notice').textContent='Private growth state loaded.';}
    catch(error){if(current(version))$('notice').textContent=error.message;}
    finally{controls(false);if(refreshPending&&$('privateConfirm').checked){refreshPending=false;operate(refresh);}}
  }
  $('applyGrowth').onclick=()=>operate(async()=>{
    const version=epoch,values=JSON.parse($('growthValues').value);
    await request('POST',{audience:'owner_private',request_id:crypto.randomUUID(),action:$('growthAction').value,values});
    if(current(version))await refresh();
  });
  $('refreshGrowth').onclick=()=>operate(refresh);
  $('privateConfirm').onchange=()=>{
    if(!$('privateConfirm').checked){clear();$('notice').textContent='Private growth state hidden.';return;}
    epoch+=1;$('growthState').hidden=false;
    if(busy){refreshPending=true;return;}operate(refresh);
  };
  window.addEventListener('pagehide',clear);
  window.addEventListener('storage',event=>{if(event.key==='gamma-logout'){clear();$('privateConfirm').checked=false;}});
  controls(false);
})();
