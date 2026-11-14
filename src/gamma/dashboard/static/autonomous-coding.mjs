// autonomous-coding.mjs - Coding page: owner goals, work progress and proposed diffs to review.
if (window.GAMMA_DASHBOARD_PAGE === 'coding') {
  const form = document.getElementById('autonomousCodingForm');
  const list = document.getElementById('autonomousCodingJobs');
  const message = document.getElementById('autonomousCodingMessage');
  const ACTIVE = ['queued', 'running', 'deferred', 'paused'];
  const TONES = {
    awaiting_owner_review: 'live', running: 'good', queued: 'good', deferred: 'warn', paused: 'warn',
    already_satisfied: 'good', completed: 'good', applied: 'good',
    blocked: 'bad', review_rejected: 'bad', interrupted: 'bad', stopped: 'off',
  };
  const openDetails = new Set();
  const proposals = new Map();
  let submitting = false;
  let lastRendered = '';

  async function request(url, options) {
    const response = await fetch(url, options);
    const value = await response.json();
    if (!response.ok) throw new Error(value.detail || 'Coding request failed');
    return value;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function label(value) {
    return String(value || '').replace(/_/g, ' ');
  }

  function fold(job, key, title, fill) {
    const details = el('details', 'fold');
    const id = `${job.id}:${key}`;
    details.open = openDetails.has(id);
    details.addEventListener('toggle', () => {
      if (details.open) openDetails.add(id); else openDetails.delete(id);
      if (details.open) fill(details);
    });
    details.append(el('summary', '', title));
    if (details.open) fill(details);
    return details;
  }

  async function showProposal(job, details) {
    let pre = details.querySelector('pre');
    if (!pre) { pre = el('pre', 'log diff', 'Loading proposal...'); details.append(pre); }
    if (!proposals.has(job.id)) {
      try {
        const value = await request(`/api/coding/work/${encodeURIComponent(job.id)}/proposal`);
        proposals.set(job.id, value.diff || '(empty diff)');
      } catch (error) {
        pre.textContent = error.message;
        return;
      }
    }
    pre.textContent = proposals.get(job.id);
  }

  function jobCard(job) {
    const card = el('article', 'job-card');
    const head = el('div', 'job-head');
    head.append(el('span', `badge ${TONES[job.status] || 'off'}`, label(job.status)));
    if (job.stage && job.stage !== job.status) head.append(el('span', 'muted small', label(job.stage)));
    if (job.created_at) head.append(el('span', 'muted small', new Date(job.created_at * 1000).toLocaleString()));
    card.append(head, el('p', 'job-goal', job.goal));
    if (job.reason) card.append(el('p', 'muted', job.reason));
    const tasks = job.tasks || [];
    if (tasks.length) {
      const ul = el('ul', 'task-list');
      for (const task of tasks) {
        ul.append(el('li', '', `${task.id}: ${label(task.status)}${task.attempts ? `, ${task.attempts} attempt${task.attempts === 1 ? '' : 's'}` : ''}`));
      }
      card.append(ul);
    }
    if (job.learning) {
      card.append(el('p', 'muted small', job.learning.recorded
        ? `Learning recorded: ${(job.learning.lesson_ids || []).join(', ')}`
        : `Learning not recorded${job.learning.error ? `: ${job.learning.error}` : ''}`));
    }
    if (job.proposal) {
      card.append(fold(job, 'proposal', 'Proposed diff', (details) => showProposal(job, details)));
    }
    card.append(fold(job, 'raw', 'Work events (raw)', (details) => {
      if (details.querySelector('pre')) return;
      details.append(el('pre', 'log', JSON.stringify({ events: job.events, application: job.application, learning: job.learning }, null, 2)));
    }));
    if (ACTIVE.includes(job.status)) {
      const row = el('div', 'button-row');
      for (const action of ['pause', 'resume', 'stop']) {
        const button = el('button', action === 'stop' ? 'ghost danger-outline small' : 'ghost small', action[0].toUpperCase() + action.slice(1));
        button.type = 'button';
        button.addEventListener('click', async () => {
          try {
            await request(`/api/coding/work/${encodeURIComponent(job.id)}/control`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }) });
            message.textContent = `${action} requested; it takes effect at the next safe boundary.`;
            await refresh();
          } catch (error) { message.textContent = error.message; }
        });
        row.append(button);
      }
      card.append(row);
    }
    return card;
  }

  async function refresh() {
    try {
      const value = await request('/api/coding/work');
      const jobs = value.jobs || [];
      const key = JSON.stringify(jobs);
      if (key === lastRendered) return;
      lastRendered = key;
      list.replaceChildren();
      if (!jobs.length) {
        list.append(el('p', 'muted', 'No coding work yet. Start a goal above.'));
        return;
      }
      const review = jobs.filter((job) => job.status === 'awaiting_owner_review');
      if (review.length) list.append(el('h3', 'subhead', `Waiting for your review (${review.length})`));
      for (const job of review) list.append(jobCard(job));
      const rest = jobs.filter((job) => job.status !== 'awaiting_owner_review');
      if (review.length && rest.length) list.append(el('h3', 'subhead', 'Other work'));
      for (const job of rest) list.append(jobCard(job));
    } catch (error) { message.textContent = error.message; }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submitting) return;
    submitting = true;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const value = await request('/api/coding/work', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          goal: document.getElementById('autonomousCodingGoal').value,
          model: document.getElementById('autonomousCodingModel').value,
          checker_model: document.getElementById('autonomousCodingChecker').value,
          budget_seconds: Number(document.getElementById('autonomousCodingBudget').value),
        }),
      });
      message.textContent = `Gamma accepted ${value.job.id}.`;
      await refresh();
    } catch (error) { message.textContent = error.message; }
    finally { submitting = false; button.disabled = false; }
  });

  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 3000);
}
