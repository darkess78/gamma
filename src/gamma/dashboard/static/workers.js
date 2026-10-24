const get = (id) => document.getElementById(id);
let cursor = null;
async function refreshBrain() {
  try {
    const status = await request('/api/brain/status');
    get('brain-status').textContent = `${status.worker?.state ?? status.state}. Process: ${status.worker?.pid ?? "none"}. Ready: ${status.worker?.ready ? "yes" : "no"}. Failure: ${status.worker?.failure ?? "none"}. Active: ${status.active ? 'yes' : 'no'}. Queued: ${status.pending}. Interactive: ${status.pending_foreground ?? status.pending}. Background: ${status.pending_background ?? 0}.`;
    get('brain-start').disabled = !['stopped', 'failed'].includes(status.worker?.state);
    get('brain-stop').disabled = status.worker?.state === 'stopped';
    const paused = ['paused', 'pausing'].includes(status.state);
    get('brain-pause').disabled = paused || !status.worker?.ready || ['stopped', 'draining'].includes(status.state);
    get('brain-resume').disabled = !paused;
  } catch (error) {
    get('brain-status').textContent = error.message;
    for (const action of ['start', 'stop', 'pause', 'resume']) get(`brain-${action}`).disabled = true;
  }
}
async function request(path, method = 'GET') {
  const response = await fetch(path, {method, credentials: 'same-origin'});
  if (!response.ok) throw new Error(response.status === 401 ? 'Sign in to use worker controls.' : `Request failed (${response.status}).`);
  return response.json();
}
function element(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}
async function refresh() {
  await refreshSilent();
  await refreshBrain();
  await refreshStt();
  await refreshTts();
  await refreshModel();
  const data = await request('/api/workers');
  renderAdmission(data.admission);
  get('workers').replaceChildren();
  for (const worker of data.workers) {
    const card = element('section', '');
    card.className = 'panel';
    card.append(element('h2', worker.worker_id));
    card.append(element('p', `${worker.host} · ${worker.observed_state} · desired: ${worker.desired_state}`));
    card.append(element('p', `Health: ${worker.health}. Heartbeat: ${worker.heartbeat_age_seconds === null ? 'unavailable' : `${worker.heartbeat_age_seconds.toFixed(1)}s ago`}.`));
    card.append(element('p', `Job: ${worker.current_job ?? 'none reported'}. Queue: ${worker.queue_depth ?? 'unavailable'}. Restarts: ${worker.restart_count}.`));
    card.append(element('p', `Logging: ${worker.logging_status}. Dropped events: ${worker.dropped_events ?? 'unavailable'}.`));
    card.append(element('p', `Instance: ${worker.instance_id ?? 'none'}. Memory: ${worker.resource_allocation.rss_bytes ?? 'unavailable'} bytes. Failure: ${worker.last_failure ?? 'none reported'}.`));
    card.append(element('p', worker.restart_consequence));
    for (const action of ['start', 'stop', 'restart', 'pause', 'resume']) {
      const button = element('button', action);
      button.type = 'button';
      button.disabled = !worker.controls.includes(action);
      if (button.disabled) button.title = 'Not supported by this worker view';
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          const result = await request(`/api/workers/${encodeURIComponent(worker.worker_id)}/${action}`, 'POST');
          get('message').textContent = result.ok ? `${worker.worker_id}: ${action} completed.` : 'Worker control failed. Inspect status and logs.';
          get('trace').value = result.trace_id ?? '';
          await refresh();
          await logs(false);
        } catch (error) { get('message').textContent = error.message; }
        finally { button.disabled = false; }
      });
      card.append(button);
    }
    get('workers').append(card);
  }
  if (!get('worker-filter').options.length) {
    for (const worker of data.workers) {
      const option = element('option', worker.worker_id);
      option.value = worker.worker_id;
      get('worker-filter').append(option);
    }
  }
}
async function refreshSilent() {
  try {
    const state = await request('/api/autonomy/silent');
    get('silent-status').textContent = `${state.enabled ? 'Enabled' : 'Disabled'}. Active: ${state.active ? 'yes' : 'no'}. Remaining: ${state.remaining}. Completed: ${state.completed}. ${state.last_reason}.`;
    get('silent-enable').disabled = state.active || state.enabled;
    get('silent-disable').disabled = !state.active && !state.enabled;
  } catch (error) {
    get('silent-status').textContent = error.message;
    get('silent-enable').disabled = get('silent-disable').disabled = true;
  }
}
for (const action of ['enable', 'disable']) {
  get(`silent-${action}`).addEventListener('click', () => showErrors(async () => {
    get('silent-enable').disabled = get('silent-disable').disabled = true;
    try { await request(`/api/autonomy/silent/${action}`, 'POST'); }
    finally { await refreshSilent(); }
  }));
}
async function logs(older) {
  const query = new URLSearchParams({workers: [...get('worker-filter').selectedOptions].map(o => o.value).join(','),
    event_type: get('type').value, severity: get('severity').value, trace_id: get('trace').value, limit: '100'});
  if (get('since').value) query.set('since', new Date(get('since').value).toISOString());
  if (older && cursor) query.set('before', cursor);
  const result = await request(`/api/worker-logs?${query}`);
  cursor = result.next_cursor;
  get('older').disabled = !cursor;
  get('logs').textContent = result.events.map(e => JSON.stringify(e)).join('\n') || 'No matching events in the retained window.';
  get('log-status').textContent = `${result.events.length} events. Reported losses: ${result.reported_dropped_events} (${result.loss_scope}). Skipped records: ${result.skipped_records}. ${result.window_truncated ? 'Read window capped; older records may be outside it.' : 'Within read window.'}`;
}
async function showErrors(action) {
  try { await action(); } catch (error) { get('message').textContent = error.message; }
}
get('refresh').addEventListener('click', () => showErrors(refresh));
for (const action of ['start', 'stop', 'pause', 'resume']) {
  get(`brain-${action}`).addEventListener('click', () => showErrors(async () => {
    for (const action of ['start', 'stop', 'pause', 'resume']) get(`brain-${action}`).disabled = true;
    try { await request(`/api/brain/${action}`, 'POST'); }
    finally { await refreshBrain(); }
  }));
}
get('filters').addEventListener('submit', event => { event.preventDefault(); showErrors(() => logs(false)); });
get('older').addEventListener('click', () => showErrors(() => logs(true)));
showErrors(async () => { await refresh(); await logs(false); });

async function refreshStt() {
  try {
    const state = await request('/api/stt/status');
    get('stt-status').textContent = state.runtime === 'worker'
      ? `Process: ${state.running ? state.pid : 'stopped'}. Transcription: ${state.enabled ? 'enabled' : 'disabled'}. ${state.busy ? 'Busy.' : 'Idle.'}`
      : 'STT uses the in-process runtime. Worker controls require worker mode.';
    for (const action of ['start', 'stop', 'restart']) get(`stt-${action}`).disabled = state.busy || !(state.controls ?? []).includes(action);
  } catch (error) {
    get('stt-status').textContent = error.message;
    for (const action of ['start', 'stop', 'restart']) get(`stt-${action}`).disabled = true;
  }
}
async function sttLogs() {
  const query = new URLSearchParams({event_type: get('stt-type').value, trace_id: get('stt-trace').value});
  const result = await request(`/api/stt/logs?${query}`);
  get('stt-logs').textContent = result.events.map(e => JSON.stringify(e)).join('\n') || 'No matching STT events.';
}
for (const action of ['start', 'stop', 'restart']) {
  get(`stt-${action}`).addEventListener('click', () => showErrors(async () => {
    for (const value of ['start', 'stop', 'restart']) get(`stt-${value}`).disabled = true;
    try {
      const result = await request(`/api/stt/${action}`, 'POST');
      get('stt-trace').value = result.trace_id ?? '';
      await sttLogs();
    } finally { await refreshStt(); }
  }));
}
get('stt-filters').addEventListener('submit', event => { event.preventDefault(); showErrors(sttLogs); });

async function refreshTts() {
  try {
    const state = await request('/api/tts/status');
    get('tts-status').textContent = state.runtime === 'worker'
      ? `Process: ${state.running ? state.pid : 'stopped'}. Synthesis: ${state.enabled ? 'enabled' : 'disabled'}. Resource: ${state.admission_state ?? 'available'}.`
      : 'TTS uses the in-process runtime. Worker controls require worker mode.';
    for (const action of ['start', 'stop', 'restart', 'reconcile']) {
      get(`tts-${action}`).disabled = state.busy || !(state.controls ?? []).includes(action) || (['start', 'restart'].includes(action) && !!state.admission_state);
    }
  } catch (error) {
    get('tts-status').textContent = error.message;
    for (const action of ['start', 'stop', 'restart', 'reconcile']) get(`tts-${action}`).disabled = true;
  }
}
for (const action of ['start', 'stop', 'restart', 'reconcile']) {
  get(`tts-${action}`).addEventListener('click', () => showErrors(async () => {
    for (const value of ['start', 'stop', 'restart', 'reconcile']) get(`tts-${value}`).disabled = true;
    try {
      const result = await request(`/api/tts/${action}`, 'POST');
      get('tts-trace').value = result.trace_id ?? '';
      await ttsLogs();
    }
    finally { await refreshTts(); }
  }));
}
async function ttsLogs() {
  const query = new URLSearchParams({event_type: get('tts-type').value, trace_id: get('tts-trace').value, severity: get('tts-severity').value});
  const result = await request(`/api/tts/logs?${query}`);
  get('tts-logs').textContent = result.events.map(e => JSON.stringify(e)).join('\n') || 'No matching TTS events.';
  get('tts-log-status').textContent = `${result.events.length} events. Reported losses: ${result.reported_dropped_events}. ${result.window_truncated ? 'Read window capped.' : 'Within read window.'}`;
}
get('tts-filters').addEventListener('submit', event => { event.preventDefault(); showErrors(ttsLogs); });

async function refreshModel() {
  try {
    const state = await request('/api/brain-model/status');
    get('model-status').textContent = state.configuration_supported === false
      ? 'Worker mode requires local or mock inference. Hosted inference is unavailable.'
      : state.runtime === 'worker'
        ? `Process: ${state.running ? state.pid : 'stopped'}. Inference: ${state.enabled ? 'enabled' : 'disabled'}. Resource: ${state.admission_state ?? 'available'}.`
        : 'Text inference uses the in-process runtime.';
    for (const action of ['start', 'stop', 'restart']) {
      get(`model-${action}`).disabled = state.busy || state.closed || !(state.controls ?? []).includes(action) || (action !== 'stop' && !!state.admission_state);
    }
  } catch (error) {
    get('model-status').textContent = error.message;
    for (const action of ['start', 'stop', 'restart']) get(`model-${action}`).disabled = true;
  }
}
async function modelLogs() {
  const query = new URLSearchParams({event_type: get('model-type').value, trace_id: get('model-trace').value});
  const result = await request(`/api/brain-model/logs?${query}`);
  get('model-logs').textContent = result.events.map(e => JSON.stringify(e)).join('\n') || 'No matching Model events.';
  get('model-log-status').textContent = `${result.events.length} events. Reported losses: ${result.reported_dropped_events}. ${result.window_truncated ? 'Read window capped.' : 'Within read window.'}`;
}
for (const action of ['start', 'stop', 'restart']) {
  get(`model-${action}`).addEventListener('click', () => showErrors(async () => {
    for (const value of ['start', 'stop', 'restart']) get(`model-${value}`).disabled = true;
    try {
      const result = await request(`/api/brain-model/${action}`, 'POST');
      get('model-trace').value = result.trace_id ?? '';
      await modelLogs();
    } finally { await refreshModel(); }
  }));
}
get('model-filters').addEventListener('submit', event => { event.preventDefault(); showErrors(modelLogs); });

function renderAdmission(data) {
  if (!data) return;
  const tokens = new Set(data.allocations.map(item => item.token));
  get('admission-status').textContent = `${tokens.size} allocated jobs. ${data.waiting.length} waiting. ${data.unresolved.length} unresolved. Deadlines expire waiting work only.`;
  get('admission-details').textContent = JSON.stringify(data, null, 2);
}
get('admission-reconcile').addEventListener('click', () => showErrors(async () => {
  const result = await request('/api/resources/admission/reconcile', 'POST');
  renderAdmission(result);
  get('message').textContent = `${result.released.length} completed allocations released. Unproven work remains reserved.`;
}));
