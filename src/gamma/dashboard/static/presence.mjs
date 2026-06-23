import { element, requestJson, setText } from './core.mjs';

function modeLabel(mode) {
  const value = String(mode || 'sleep').replace(/_/g, ' ');
  return value.replace(/\b\w/g, (character) => character.toUpperCase());
}

function boolText(value) {
  return value ? 'on' : 'off';
}

function renderAudienceOptions(payload, state) {
  const people = Array.isArray(payload.known_people) ? payload.known_people : [];
  const select = element('presenceKnownPersonId');
  if (select) {
    const current = String((state.audience || {}).known_person_id || '');
    select.replaceChildren();
    people.forEach((person) => {
      const option = document.createElement('option');
      option.value = String(person.id || '');
      option.textContent = `${person.name || 'Known person'} (${person.trust || 'guest'})`;
      option.selected = option.value === current;
      select.appendChild(option);
    });
  }
  const kind = element('presenceAudienceKind');
  if (kind) kind.value = String((state.audience || {}).kind || 'unknown');
  updatePresenceAudienceControls();
}

function updatePresenceAudienceControls() {
  const known = element('presenceAudienceKind')?.value === 'known_person';
  const row = element('presenceKnownPersonRow');
  if (row) row.hidden = !known;
}

function selectedAudience() {
  const kind = String(element('presenceAudienceKind')?.value || 'unknown');
  if (kind !== 'known_person') return { kind };
  const knownPersonId = Number(element('presenceKnownPersonId')?.value || 0);
  if (!knownPersonId) throw new Error('Select a known person before Wake.');
  return { kind, known_person_id: knownPersonId };
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
  const audience = state.audience || {};
  const wake = state.wake || {};
  const scheduler = autonomy.scheduler || {};
  const continuity = payload.continuity || {};
  const working = continuity.working_state || {};
  const subscribers = Array.isArray(stats.subscribers) ? stats.subscribers : [];
  const monitorSubscribers = subscribers.filter((subscriber) => subscriber.target_policy === 'dashboard_monitor');
  const audioReady = monitorSubscribers.filter((subscriber) => subscriber.audio_ready).length;

  setText('presenceModeStatus', [
    `Mode: ${modeLabel(mode)}`,
    state.requires_confirmation ? 'Needs confirmation before public output resumes.' : '',
    `Desired: ${modeLabel(state.desired_mode || mode)}`,
    `Audience: ${audience.display_name || 'Unknown'} (${audience.kind || 'unknown'})`,
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
    `Muted targets: ${muted.length ? muted.join(', ') : 'none'}`,
    `Monitor subscribers: ${monitorSubscribers.length} (${audioReady} audio ready)`
  ].join('\n'));

  setText('presenceWakeStatus', [
    `Opening enabled: ${boolText(wake.enabled !== false)}`,
    `Last result: ${wake.last_status || 'none'}`,
    `Reason: ${wake.suppression_reason || 'none'}`,
    `Last opening: ${wake.last_opening || 'none'}`,
    `Last Wake: ${wake.last_event_at || 'never'}`,
    `Active topic: ${working.active_topic || 'none'}`,
    `Objective: ${working.current_objective || 'none'}`,
    `Next action: ${working.next_intended_action || 'none'}`
  ].join('\n'));

  setText('presenceAutonomyStatus', [
    `Scheduler: ${scheduler.status || 'idle'}`,
    `Reason: ${scheduler.reason || 'not checked'}`,
    `Last check: ${scheduler.last_checked_at || 'never'}`,
    `Next eligible check: ${scheduler.next_check_at || 'not scheduled'}`,
    `Last emitted: ${scheduler.last_emitted_at || 'never'}`,
    `Attempts for topic: ${scheduler.attempts_for_topic || 0}`,
    `Last autonomous action: ${(state.activity || {}).last_autonomous_action?.occurred_at || 'none'}`
  ].join('\n'));

  renderAudienceOptions(payload, state);

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
  if (normalized === 'wake') {
    body.audience = selectedAudience();
    body.session_id = String(element('presenceSessionId')?.value || 'presence-local').trim() || 'presence-local';
  } else {
    body.audience = { kind: 'unknown' };
  }
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
window.updatePresenceAudienceControls = updatePresenceAudienceControls;

if (String(window.GAMMA_DASHBOARD_PAGE || '') === 'presence') {
  window.setTimeout(() => loadPresence().catch(() => {}), 0);
}
