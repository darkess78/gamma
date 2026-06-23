import { element, requestJson, setText } from './core.mjs';

function modeLabel(mode) {
  const value = String(mode || 'sleep').replace(/_/g, ' ');
  return value.replace(/\b\w/g, (character) => character.toUpperCase());
}

function boolText(value) {
  return value ? 'on' : 'off';
}

function renderPresenceStatus(payload = {}) {
  const state = payload.state || {};
  const runtime = payload.runtime || {};
  const shana = runtime.shana || {};
  const shanaProcess = shana.process || {};
  const shanaHealth = shana.api_health || {};
  const twitch = runtime.twitch || {};
  const worker = twitch.worker || {};
  const eventsub = twitch.eventsub || {};
  const ready = twitch.stream_ready || {};
  const performer = runtime.performer || {};
  const stats = performer.stats || {};
  const outputs = state.outputs || {};
  const autonomy = state.autonomy || {};
  const safety = state.safety || {};
  const inputs = state.inputs || {};
  const muted = Array.isArray(stats.muted_targets) ? stats.muted_targets : [];
  const mode = String(state.mode || 'sleep');

  setText('presenceModeStatus', [
    `Mode: ${modeLabel(mode)}`,
    state.requires_confirmation ? 'Needs confirmation before public output resumes.' : '',
    `Desired: ${modeLabel(state.desired_mode || mode)}`,
    `Updated: ${state.updated_at || 'n/a'}`
  ].filter(Boolean).join('\n'));

  setText('presenceRuntimeStatus', [
    `Shana process: ${shanaProcess.running ? 'running' : 'stopped'}`,
    `Shana API: ${shanaHealth.ok ? 'healthy' : (shanaHealth.detail || 'unavailable')}`,
    `Twitch IRC: ${(worker.process || {}).running ? 'running' : 'stopped'}`,
    `EventSub: ${(eventsub.process || {}).running ? 'running' : 'stopped'}`,
    `Readiness: ${ready.mode || 'unknown'}`
  ].join('\n'));

  setText('presenceOutputStatus', [
    `Dashboard monitor: ${boolText(outputs.dashboard_monitor)}`,
    `Stream public: ${boolText(outputs.stream_public)}`,
    `Voice: ${boolText(outputs.voice)}`,
    `Subtitles: ${boolText(outputs.subtitles)}`,
    `Local mic: ${boolText(inputs.local_mic)}`,
    `Ambient: ${boolText(autonomy.ambient_chat_enabled)}`,
    `Proactive: ${boolText(autonomy.proactive_idle_enabled)}`,
    `Safety review: ${boolText(safety.llm_safety_review_enabled)}`,
    `Dry run: ${boolText(safety.dry_run)}`,
    `Muted targets: ${muted.length ? muted.join(', ') : 'none'}`
  ].join('\n'));

  ['sleep', 'wake', 'go_live', 'break'].forEach((name) => {
    const button = element(`presence${modeLabel(name).replace(/\s+/g, '')}Button`);
    if (!button) return;
    button.classList.toggle('active', mode === name);
    button.setAttribute('aria-pressed', mode === name ? 'true' : 'false');
  });

  setText('overviewPresenceStatus', modeLabel(mode));
  setText('overviewPresenceMini', modeLabel(mode));
}

async function loadPresence() {
  try {
    const payload = await requestJson(`/api/presence?_=${Date.now()}`, { cache: 'no-store' });
    renderPresenceStatus(payload);
    return payload;
  } catch (error) {
    setText('presenceModeStatus', `Presence load failed.\n${String(error)}`);
    throw error;
  }
}

async function setPresenceMode(mode) {
  const normalized = String(mode || '').trim();
  const body = { mode: normalized };
  if (normalized === 'go_live') {
    if (!window.confirm('Go Live enables public stream output for this Shana backend session.')) return;
    body.confirm_public_output = true;
  }
  setText('presenceModeStatus', `Setting Presence to ${modeLabel(normalized)}...`);
  try {
    const payload = await requestJson('/api/presence/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    await loadPresence();
    if (window.loadStatus) window.setTimeout(window.loadStatus, 350);
    return payload;
  } catch (error) {
    setText('presenceModeStatus', `Presence update failed.\n${String(error)}`);
    throw error;
  }
}

window.renderPresenceStatus = renderPresenceStatus;
window.loadPresence = loadPresence;
window.setPresenceMode = setPresenceMode;

if (String(window.GAMMA_DASHBOARD_PAGE || '') === 'presence') {
  window.setTimeout(() => loadPresence().catch(() => {}), 0);
}
