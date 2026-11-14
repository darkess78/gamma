// autonomous-coding.mjs - Coding page: owner goals, work progress and proposed diffs to review.
if (window.GAMMA_DASHBOARD_PAGE === 'coding') {
  const form = document.getElementById('autonomousCodingForm');
  const list = document.getElementById('autonomousCodingJobs');
  const message = document.getElementById('autonomousCodingMessage');
  const ACTIVE = ['queued', 'running', 'deferred', 'paused'];
  const TONES = {
    awaiting_owner_review: 'live', running: 'good', queued: 'good', deferred: 'warn', paused: 'warn',
    needs_owner: 'warn', blocked: 'bad', failed: 'bad', interrupted: 'bad', stopped: 'off',
  };
  const STAGES = {
    starting: 'starting', splitting: 'splitting into tasks', working: 'working on a task',
    suite: 'running the full test suite', waiting_for_gpu: 'waiting for the GPU', resumed: 'resumed',
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
    if (job.stage && job.stage !== job.status && ACTIVE.includes(job.status)) {
      head.append(el('span', 'muted small', STAGES[job.stage] || label(job.stage)));
    }
    if (job.created_at) head.append(el('span', 'muted small', new Date(job.created_at * 1000).toLocaleString()));
    card.append(head, el('p', 'job-goal', job.goal));
    if (job.reason) card.append(el('p', 'muted', job.reason));
    const tasks = job.tasks || [];
    if (tasks.length) {
      const ul = el('ul', 'task-list');
      for (const task of tasks) {
        const li = el('li', '');
        li.append(el('strong', '', `${task.title || task.id}`), document.createTextNode(` (${label(task.status)}`
          + `${task.steps ? `, ${task.steps} step${task.steps === 1 ? '' : 's'}` : ''})`));
        if (task.files && task.files.length) li.append(el('div', 'muted small', `${task.files.join(', ')}; checked by ${task.check}`));
        if (task.summary) li.append(el('div', 'small', task.summary));
        ul.append(li);
      }
      card.append(ul);
    }
    if (job.suite) {
      card.append(el('p', `small ${job.suite.passed ? '' : 'muted'}`, job.suite.passed ? 'Full test suite passed.' : 'Full test suite failed.'));
    }
    if (job.proposal) {
      card.append(fold(job, 'proposal', 'Proposed diff', (details) => showProposal(job, details)));
    }
    card.append(fold(job, 'raw', 'Work events (raw)', (details) => {
      if (details.querySelector('pre')) return;
      details.append(el('pre', 'log', JSON.stringify({ events: job.events, suite: job.suite, last_transport: job.last_transport }, null, 2)));
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
          budget_seconds: Number(document.getElementById('autonomousCodingBudget').value),
        }),
      });
      message.textContent = value.process && value.process.ok === false
        ? 'The goal was saved, but the coding worker could not start.'
        : 'Queued. Shana will split it into tasks and start when the GPU is free.';
      await refresh();
    } catch (error) { message.textContent = error.message; }
    finally { submitting = false; button.disabled = false; }
  });

  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 3000);
}
