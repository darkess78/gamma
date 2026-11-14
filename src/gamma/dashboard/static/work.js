/* Private practical task staging and exact candidate review. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag,text) => {const n=document.createElement(tag);n.textContent=text;return n;};
  let epoch=0,selected=null,busy=false,refreshPending=false;
  function setBusy(value) {
    busy=value;
    const unavailable=busy||!$('privateConfirm').checked||$('workState').hidden;
    for(const control of document.querySelectorAll('#workState button'))control.disabled=unavailable;
    $('downloadWork').disabled=unavailable||!selected;
    $('workState').setAttribute('aria-busy',String(busy));
  }
  function clear() {
    epoch+=1;selected=null;refreshPending=false;
    $('workState').hidden=true;$('workList').replaceChildren();$('workDetail').textContent='';
    $('workSpec').value='';$('authorizeStart').checked=false;setBusy(busy);
  }
  function privateNow(value) {return value===epoch&&$('privateConfirm').checked;}
  async function request(method,payload,taskId,artifact=false) {
    if(!$('privateConfirm').checked)throw Error('Confirm a private audience to view work.');
    let url='/api/coordination/work'+(artifact?'/artifact':'');
    if(method==='GET')url+='?audience=owner_private'+(taskId?'&task_id='+encodeURIComponent(taskId):'');
    const response=await fetch(url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},
      body:method==='GET'?undefined:JSON.stringify(payload)});
    if(response.status===401||response.redirected){$('privateConfirm').checked=false;clear();throw Error('Sign in again to view private work.');}
    if(!response.ok)throw Error('The task operation was not accepted. Check its scope, start permission and remaining deadline.');
    return response.json();
  }
  const button=(text,action)=>{const b=node('button',text);b.type='button';b.disabled=busy;b.onclick=action;return b;};
  async function details(id) {
    const current=epoch,value=await request('GET',null,id);
    if(!privateNow(current))return;
    selected=id;$('workDetail').textContent=JSON.stringify(value,null,2);setBusy(busy);
  }
  async function refresh() {
    const current=epoch,value=await request('GET');
    if(!privateNow(current))return;
    $('workList').replaceChildren();
    for(const activity of value.activities){
      const row=node('article',activity.goal);row.dataset.taskId=activity.id;
      row.append(node('small',`${activity.id} | ${activity.mode} | ${activity.status} | ${activity.reason}`));
      row.append(button('Inspect candidate',()=>operate(()=>details(activity.id))));
      if(activity.status==='ready')row.append(button('Start this task',()=>operate(()=>start(activity.id))));
      $('workList').append(row);
    }
  }
  async function operate(action) {
    if(busy||!$('privateConfirm').checked)return;
    setBusy(true);const current=epoch;
    $('notice').textContent='Loading private work. Please wait for this request to finish.';
    try{await action();if(privateNow(current))$('notice').textContent='Private isolated work loaded.';}
    catch(error){if(privateNow(current))$('notice').textContent=error.message;}
    finally{
      setBusy(false);
      if(refreshPending&&$('privateConfirm').checked){refreshPending=false;operate(refresh);}
    }
  }
  async function start(id) {
    await request('POST',{audience:'owner_private',request_id:crypto.randomUUID(),action:'start',task_id:id});
    await refresh();
  }
  $('prepareWork').onclick=()=>operate(async()=>{
    const current=epoch,spec=JSON.parse($('workSpec').value);
    const result=await request('POST',{audience:'owner_private',request_id:crypto.randomUUID(),action:'prepare',spec,
      maximum_attempts:Number($('maximumAttempts').value),budget_seconds:Number($('budgetSeconds').value),
      authorize_start:$('authorizeStart').checked});
    if(privateNow(current)){await refresh();await details(result.activity.id);}
  });
  $('refreshWork').onclick=()=>operate(refresh);
  $('downloadWork').onclick=()=>operate(async()=>{
    const current=epoch,id=selected;
    if(!id)return;
    const value=await request('GET',null,id,true);
    if(!privateNow(current)||selected!==id)return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    const link=node('a','');link.href=url;link.download=id+'-candidate.json';link.click();URL.revokeObjectURL(url);
  });
  $('privateConfirm').onchange=()=>{
    if(!$('privateConfirm').checked){clear();$('notice').textContent='Private work hidden.';return;}
    epoch+=1;$('workState').hidden=false;
    if(busy){refreshPending=true;$('notice').textContent='Waiting for the previous private request to finish.';return;}
    operate(refresh);
  };
  window.addEventListener('pagehide',clear);
  window.addEventListener('storage',event=>{if(event.key==='gamma-logout'){clear();$('privateConfirm').checked=false;}});
  setBusy(false);
})();
