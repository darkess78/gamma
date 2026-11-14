/* Private activity controls. Authentication remains on dashboard-owned APIs. */
(function () {
  const pendingControls = new Map();
  let enabled = false;
  let audienceEpoch = 0;
  let targets = new Set();
  const status = () => document.getElementById('coordinationStatus');
  const feedback = () => document.getElementById('coordinationControlStatus') || status();
  const selector = () => document.getElementById('coordinationActivityTarget');
  const isPrivate = () => enabled && document.getElementById('monitorPrivateAudience')?.value === 'owner_private';
  function clearTargets() {
    targets = new Set();
    const select = selector();
    if (select) { select.replaceChildren(); select.value = ''; }
  }
  async function refresh() {
    if (!isPrivate()) return;
    const epoch = audienceEpoch;
    try {
      const response = await fetch('/api/coordination/status?audience=owner_private');
      if (!response.ok) return;
      const value = await response.json();
      if (!isPrivate() || epoch !== audienceEpoch) return;
      const activities = value.activities || [];
      const unfinished = activities.filter(a => !['completed', 'blocked', 'needs_checker', 'stopped'].includes(a.status));
      targets = new Set(unfinished.map(a => a.id));
      const select = selector();
      if (select) {
        const previous = select.value;
        select.replaceChildren();
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = unfinished.length ? 'Select an activity to control' : 'No unfinished activities';
        select.appendChild(placeholder);
        for (const activity of unfinished) {
          const option = document.createElement('option');
          option.value = activity.id;
          option.textContent = `${activity.goal} | ${activity.status} | ${activity.id}`;
          select.appendChild(option);
        }
        select.value = targets.has(previous) ? previous : '';
      }
      const lines = activities.map(a => `${a.goal}: ${a.status}. ${a.reason || ''}\nActivity: ${a.id}`
        + (a.worker_run ? `\nWorker: ${a.worker_run}` : '\nWorker has not started.')
        + (a.next_step ? `\nNext: ${a.next_step}` : ''));
      status().textContent = [`${value.awake ? 'Awake' : 'Asleep'}, ${value.focus}.`, ...lines].join('\n\n');
    } catch (_) { /* Keep last observed evidence while the backend is unavailable. */ }
  }
  window.coordinationAction = async function (action, focus) {
    if (document.getElementById('monitorPrivateAudience')?.value !== 'owner_private') {
      feedback().textContent = 'Confirm that the owner is present privately.';
      return;
    }
    const epoch = audienceEpoch;
    const values = { action, audience: 'owner_private' };
    const exercise = document.getElementById('coordinationExercise').value;
    if (action === 'practice') values.exercise_id = exercise;
    if (action === 'permit') values.exercise_ids = [exercise];
    if (focus) values.focus = focus;
    if (['pause', 'resume', 'stop'].includes(action)) {
      const target = selector()?.value;
      if (!target || !targets.has(target)) {
        feedback().textContent = 'Select an unfinished activity before using pause, resume, or stop.';
        return;
      }
      values.run_id = target;
    }
    const fingerprint = JSON.stringify(values);
    let operation = pendingControls.get(fingerprint);
    if (!operation) {
      if (pendingControls.size >= 64) { feedback().textContent = 'Retry an uncertain control before starting more operations.'; return; }
      operation = { id: crypto.randomUUID() };
      pendingControls.set(fingerprint, operation);
    }
    values.request_id = operation.id;
    try {
      const response = await fetch('/api/coordination/control', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values)
      });
      const value = await response.json();
      if (epoch !== audienceEpoch || document.getElementById('monitorPrivateAudience')?.value !== 'owner_private') return;
      if (!response.ok) throw new Error(value.detail || `HTTP ${response.status}`);
      if (pendingControls.get(fingerprint) === operation) pendingControls.delete(fingerprint);
      enabled = true;
      feedback().textContent = value.requested ? `${value.requested} ${value.acknowledged ? 'applied.' : 'requested; waiting for a worker checkpoint.'}` : 'Action accepted.';
      await refresh();
    } catch (error) {
      if (epoch === audienceEpoch && document.getElementById('monitorPrivateAudience')?.value === 'owner_private') feedback().textContent = String(error);
    }
  };
  document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('monitorPrivateAudience')?.addEventListener('change', function () {
      audienceEpoch += 1;
      enabled = this.value === 'owner_private';
      if (enabled) return refresh();
      clearTargets();
      if (status()) status().textContent = 'Private audience not confirmed.';
      if (feedback()) feedback().textContent = 'Private audience not confirmed.';
    });
  });
  window.setInterval(refresh, 5000);
}());
