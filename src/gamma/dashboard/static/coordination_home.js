(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let current = null, polling = false, acting = false;
  const ui = () => window.ShanaUI;
  async function refresh() {
    if (polling || acting || document.hidden) return;
    polling = true;
    try {
      const [state, health] = await Promise.all([ui().request('/api/coordination/status?audience=owner_private'), ui().request('/api/coordination/maintenance?audience=owner_private')]);
      current = state;
      $('homeState').textContent = state.awake ? 'Awake · private test' : 'Asleep · work retained';
      $('homeSummary').textContent = state.awake ? state.focus === 'focused' ? 'You are focusing. Shana can keep working quietly.' : 'Ready for conversation. Your work and shared context stay together.' : 'New conversation waits. Existing authorized workers can keep their instructions.';
      $('homeSleep').textContent = state.awake ? 'Sleep' : 'Wake'; $('homeFocus').textContent = state.focus === 'focused' ? 'I can chat now' : "I'm focusing";
      $('homeSleep').disabled = false; $('homeFocus').disabled = !state.awake;
      const rows = (state.activities || []).filter(a => !['cancelled','abandoned'].includes(a.status)).slice(0, 3);
      $('homeWork').replaceChildren();
      for (const row of rows) {const li = document.createElement('li'); const title = document.createElement('strong'); title.textContent = row.goal || row.requested_goal || 'Ongoing work'; const p = document.createElement('div'); p.className = 'shana-muted'; p.textContent = row.status.replaceAll('_',' '); li.append(title,p); $('homeWork').append(li);}
      if (!rows.length) {const li = document.createElement('li'); li.className = 'shana-empty'; li.textContent = 'No work is running right now.'; $('homeWork').append(li);}
      $('homeStorage').textContent = `${(health.free_bytes / 1073741824).toFixed(1)} GiB available on Gamma.${health.storage_warning ? ' Space is low. Large recovery operations may need attention.' : ''}`;
      $('homeBackup').disabled = !health.small_write_admitted;
      $('homeBackups').replaceChildren();
      for (const backup of health.backups) {const li = document.createElement('li'); li.textContent = `${new Date(backup.created_at * 1000).toLocaleString()} · ${backup.status.replaceAll('_',' ')}`; if (backup.status === 'verified_snapshot') {const b = document.createElement('button'); b.className = 'secondary'; b.textContent = 'Check restore'; b.addEventListener('click', () => recovery('rehearse', backup.id)); li.append(document.createElement('br'), b);} $('homeBackups').append(li);}
    } catch (error) {ui().notice($('homeNotice'), error.message, true);} finally {polling = false;}
  }
  async function control(action, extra = {}) {
    if (acting || !current) return;
    acting = true; $('homeSleep').disabled = $('homeFocus').disabled = true;
    try {await ui().request('/api/coordination/control', {action, ...extra}); ui().notice($('homeNotice'), 'Updated.');} catch (error) {ui().notice($('homeNotice'), error.message, true);} finally {acting = false; refresh();}
  }
  async function recovery(action, backup_id) {
    if (acting) return; acting = true;
    $('homeCheck').disabled = $('homeBackup').disabled = true; ui().notice($('recoveryNotice'), 'Checking the actual stored state…');
    try {const result = await ui().request('/api/coordination/maintenance', {action, ...(backup_id ? {backup_id} : {})}); const ok = action === 'backup' ? result.status === 'verified_snapshot' : result.ok === true; ui().notice($('recoveryNotice'), ok ? action === 'backup' ? 'Recovery copy verified.' : 'Check passed. Current state stayed in place.' : result.status === 'preparing' ? 'The recovery copy is still being prepared. Check its status before trying again.' : 'The recovery check needs review.', !ok);} catch (error) {ui().notice($('recoveryNotice'), error.message, true);} finally {acting = false; $('homeCheck').disabled = false; refresh();}
  }
  document.addEventListener('DOMContentLoaded', () => {$('homeSleep').addEventListener('click', () => control(current.awake ? 'sleep' : 'wake')); $('homeFocus').addEventListener('click', () => control('focus', {focus: current.focus === 'focused' ? 'relaxed' : 'focused'})); $('homeCheck').addEventListener('click', () => recovery('check')); $('homeBackup').addEventListener('click', () => recovery('backup')); refresh(); setInterval(refresh, 10000);});
})();
