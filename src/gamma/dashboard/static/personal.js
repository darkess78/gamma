/* Private candidate editor. No state is fetched until audience confirmation. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let epoch = 0;
  let busy = false;
  let speechStyle = '';
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  const notice = text => { $('notice').textContent = text; };
  function clear() {
    epoch += 1;
    $('personalState').hidden = true;
    for (const id of ['memoryList','peopleList','accountList','accountPerson','queueList']) $(id).replaceChildren();
    for (const id of ['identityName','identityPersonality','identitySpeech','memoryTarget','memoryTopic','memoryText','personName','personRelationship','accountPlatform','accountId']) $(id).value = '';
    $('accountConfirmed').checked = false;
    $('identityProvenance').textContent = '';
  }
  async function request(method, payload) {
    const response = await fetch('/api/coordination/personal' + (method === 'GET' ? '?audience=owner_private' : ''), {
      method, credentials: 'same-origin', headers: {'Content-Type':'application/json'},
      body: method === 'GET' ? undefined : JSON.stringify(payload)
    });
    if (response.status === 401 || response.redirected) {
      $('privateConfirm').checked = false; clear(); throw Error('Sign in again to view private state.');
    }
    if (!response.ok) throw Error('The operation was not accepted. Review the values and account confirmation.');
    return response.json();
  }
  const button = (text, action) => {
    const b = node('button', text); b.type = 'button'; b.onclick = action; return b;
  };
  async function refresh() {
    const current = epoch;
    const state = await request('GET');
    if (current !== epoch || !$('privateConfirm').checked) return;
    const fields = state.identity.fields;
    for (const [id, key] of [['identityName','name'],['identityPersonality','core_personality'],['identitySpeech','speech_style']]) {
      $(id).value = key === 'speech_style' && fields[key] && typeof fields[key] === 'object' ? fields[key].tone || '' :
        typeof fields[key] === 'string' ? fields[key] : JSON.stringify(fields[key] || '');
    }
    speechStyle = fields.speech_style;
    $('identityProvenance').textContent = `Revision ${state.identity.revision}. Current authored settings; ${Object.keys(state.identity.field_sources).length} fields have direct edit sources.`;
    $('memoryList').replaceChildren();
    for (const memory of state.records) {
      const row = node('article', memory.text || 'Removed text');
      row.dataset.memoryId = memory.id;
      row.append(node('small', `${memory.kind} · ${memory.status} · ${memory.certainty} · source ${memory.source_kind} · revision ${memory.revision}`));
      if (!['forgotten','deleted'].includes(memory.status)) {
        row.append(button('Correct', () => {
          $('memoryTarget').value = memory.id; $('memoryTopic').value = memory.topic;
          $('memoryKind').value = memory.kind; $('memoryText').value = memory.text; $('memoryText').focus();
        }), button('Forget', () => save('forget', {target_id:memory.id})),
        button('Delete source text', () => save('delete', {target_id:memory.id})));
      }
      $('memoryList').append(row);
    }
    $('peopleList').replaceChildren(); $('accountPerson').replaceChildren();
    for (const person of state.people.filter(p => p.status === 'active')) {
      $('peopleList').append(node('article', `${person.name} · ${person.relationship || 'No established relationship'} · ${person.interaction_count} accepted interactions`));
      if (person.id !== 'self') { const option = node('option',person.name); option.value = person.id; $('accountPerson').append(option); }
    }
    $('accountList').replaceChildren();
    for (const account of state.accounts.filter(a => a.status === 'active')) {
      const row = node('article',`${account.platform}: ${account.platform_user_id} · no added action authority`);
      row.append(button('Unlink',()=>save('unlink',{platform:account.platform,platform_user_id:account.platform_user_id,person_id:account.person_id})));
      $('accountList').append(row);
    }
    $('textDays').value = state.policy.text_days; $('sessionSources').value = state.policy.per_session_sources;
    $('queueList').replaceChildren(node('p',`${state.queued_messages.length} messages waiting for wake or recovery.`));
    for (const turn of state.queued_messages) $('queueList').append(node('small',`${turn.id} · ${turn.status}`));
    $('personalState').hidden = false;
  }
  async function save(action, values) {
    if (busy || !$('privateConfirm').checked) return;
    busy = true; const current = epoch;
    notice('Saving private change…');
    try {
      await request('POST',{audience:'owner_private',request_id:'personal-'+crypto.randomUUID(),action,values});
      if (current !== epoch) return;
      await refresh(); if (current === epoch) notice('Saved in the isolated candidate.');
    } catch (error) { if (current === epoch) notice(error.message); }
    finally { busy = false; }
  }
  $('privateConfirm').onchange = async () => {
    clear();
    if (!$('privateConfirm').checked) { notice('Private state hidden.'); return; }
    try { await refresh(); notice('Private candidate state loaded.'); } catch (error) { notice(error.message); }
  };
  $('identityForm').onsubmit = event => { event.preventDefault(); save('identity',{fields:{name:$('identityName').value,core_personality:$('identityPersonality').value,
    speech_style:speechStyle && typeof speechStyle === 'object' ? {...speechStyle,tone:$('identitySpeech').value} : $('identitySpeech').value}}); };
  $('memoryForm').onsubmit = event => {
    event.preventDefault(); const target = $('memoryTarget').value;
    save('memory',{op:target?'correct':'remember',...(target?{target_id:target}:{}),kind:$('memoryKind').value,
      subject_id:'owner',topic:$('memoryTopic').value,text:$('memoryText').value});
  };
  $('clearMemory').onclick = () => { for (const id of ['memoryTarget','memoryTopic','memoryText']) $(id).value=''; };
  $('personForm').onsubmit = event => { event.preventDefault(); save('person',{name:$('personName').value,relationship:$('personRelationship').value}); };
  $('accountForm').onsubmit = event => { event.preventDefault(); save('link',{person_id:$('accountPerson').value,platform:$('accountPlatform').value,platform_user_id:$('accountId').value,confirmed:$('accountConfirmed').checked}); };
  $('policyForm').onsubmit = event => { event.preventDefault(); save('policy',{text_days:Number($('textDays').value),per_session_sources:Number($('sessionSources').value)}); };
  window.addEventListener('pagehide',clear);
})();
