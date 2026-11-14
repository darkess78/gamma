// presence.mjs - Home page: presence mode controls, what Shana is doing, recent stream output.
import { element, requestJson, setText } from './core.mjs';

const MODES = {
  sleep: { title: 'Asleep', detail: 'Quiet. No public output and Twitch is not being watched.', tone: 'off' },
  wake: { title: 'Awake', detail: 'Talking with you privately. Stream output is muted.', tone: 'good' },
  go_live: { title: 'Live', detail: 'Watching Twitch chat and speaking on stream.', tone: 'live' },
  break: { title: 'On break', detail: 'Still watching chat, stream output is muted.', tone: 'warn' },
};
const BUTTONS = { sleep: 'presenceSleepButton', wake: 'presenceWakeButton', go_live: 'presenceGoLiveButton', break: 'presenceBreakButton' };

function formatTime(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function renderAudienceOptions(people, state) {
  const select = element('presenceKnownPersonId');
  if (select && document.activeElement !== select) {
    const current = String((state.audience || {}).known_person_id || select.value || '');
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
  if (kind && document.activeElement !== kind && !kind.dataset.touched) {
    kind.value = String((state.audience || {}).kind || 'unknown');
  }
  updatePresenceAudienceControls();
}

function updatePresenceAudienceControls() {
  const kind = element('presenceAudienceKind');
  const row = element('presenceKnownPersonRow');
  if (row) row.hidden = kind?.value !== 'known_person';
}

function selectedAudience() {
  const kind = String(element('presenceAudienceKind')?.value || 'unknown');
  if (kind !== 'known_person') return { kind };
  const knownPersonId = Number(element('presenceKnownPersonId')?.value || 0);
  if (!knownPersonId) throw new Error('Pick a known person in Wake options first.');
  return { kind, known_person_id: knownPersonId };
}

function renderPresenceStatus(payload = {}) {
  const state = payload.state || {};
  const mode = String(state.mode || 'sleep');
  const info = MODES[mode] || { title: mode, detail: '', tone: 'off' };
  const audience = state.audience || {};
  const working = (payload.continuity || {}).working_state || {};

  setText('presenceModeTitle', info.title);
  const dot = element('presenceDot');
  if (dot) dot.className = `state-dot ${info.tone}`;
  const detail = [info.detail];
  if (mode !== 'sleep' && audience.kind && audience.kind !== 'unknown') {
    detail.push(`With ${audience.display_name || audience.kind}.`);
  }
  if (state.requires_confirmation) detail.push('Public output needs your confirmation before it resumes.');
  if (state.desired_mode && state.desired_mode !== mode) detail.push(`Switching to ${(MODES[state.desired_mode] || {}).title || state.desired_mode}.`);
  setText('presenceModeDetail', detail.join(' '));

  Object.entries(BUTTONS).forEach(([name, id]) => {
    const button = element(id);
    if (!button) return;
    button.classList.toggle('active', mode === name);
    button.setAttribute('aria-pressed', mode === name ? 'true' : 'false');
  });

  setText('homeTopic', working.active_topic || 'None');
  setText('homeObjective', working.current_objective || 'None');
  setText('homeNext', working.next_intended_action || 'None');

  const last = payload.last_durable_output || null;
  if (last && last.text) {
    setText('homeLastSaid', last.text);
    setText('homeLastSaidMeta', [formatTime(last.occurred_at), last.spoken ? 'spoken' : 'text only', last.status].filter(Boolean).join(' / '));
  } else {
    setText('homeLastSaid', 'Nothing yet.');
    setText('homeLastSaidMeta', '');
  }

  renderAudienceOptions(Array.isArray(payload.known_people) ? payload.known_people : [], state);
}

async function loadPresence() {
  try {
    const payload = await requestJson(`/api/presence?_=${Date.now()}`, { cache: 'no-store' });
    renderPresenceStatus(payload);
    return payload;
  } catch (error) {
    setText('presenceModeTitle', 'Unknown');
    setText('presenceModeDetail', `Presence could not be loaded: ${String(error.message || error)}`);
    return null;
  }
}

async function loadRecentOutputs() {
  const target = element('homeRecentOutputs');
  if (!target || !window.humanStreamOutputs) return;
  try {
    const payload = await requestJson('/api/stream/outputs/recent?limit=6', { cache: 'no-store' });
    target.innerHTML = window.humanStreamOutputs(payload);
  } catch (error) {
    target.textContent = `Recent output could not be loaded: ${String(error.message || error)}`;
  }
}

async function setPresenceMode(mode) {
  const normalized = String(mode || '').trim();
  const body = { mode: normalized };
  try {
    if (normalized === 'wake') {
      body.audience = selectedAudience();
      body.session_id = String(element('presenceSessionId')?.value || 'presence-local').trim() || 'presence-local';
    } else {
      body.audience = { kind: 'unknown' };
    }
  } catch (error) {
    setText('presenceModeDetail', String(error.message || error));
    return null;
  }
  if (normalized === 'go_live') {
    if (!window.confirm('Go Live lets Shana speak publicly on stream. Continue?')) return null;
    body.confirm_public_output = true;
  }
  setText('presenceModeDetail', `Switching to ${(MODES[normalized] || {}).title || normalized}...`);
  try {
    const payload = await requestJson('/api/presence/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    await loadPresence();
    if (window.loadStatus) window.setTimeout(window.loadStatus, 350);
    return payload;
  } catch (error) {
    setText('presenceModeDetail', `Presence change failed: ${String(error.message || error)}`);
    return null;
  }
}

window.renderPresenceStatus = renderPresenceStatus;
window.loadPresence = loadPresence;
window.setPresenceMode = setPresenceMode;
window.updatePresenceAudienceControls = updatePresenceAudienceControls;

if (String(window.GAMMA_DASHBOARD_PAGE || '') === 'dashboard') {
  element('presenceAudienceKind')?.addEventListener('change', (event) => { event.target.dataset.touched = '1'; });
  const refresh = () => {
    if (document.hidden) return;
    loadPresence();
    loadRecentOutputs();
  };
  refresh();
  window.setInterval(refresh, 10000);
}
