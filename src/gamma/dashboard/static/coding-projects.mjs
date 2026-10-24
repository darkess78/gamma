if (window.GAMMA_DASHBOARD_PAGE === 'improvement') {
  const form = document.getElementById('codingProjectForm');
  const message = document.getElementById('codingProjectMessage');
  const jobs = document.getElementById('codingProjectJobs');
  let pending = false;
  async function request(url, options) {
    const response = await fetch(url, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Request failed');
    return result;
  }
  async function refresh() {
    try {
      const result = await request('/api/coding/projects');
      jobs.replaceChildren();
      for (const job of result.jobs || []) {
        const card = document.createElement('article');
        card.className = 'improvement-item';
        const heading = document.createElement('strong');
        heading.textContent = `${job.status} · ${job.stage || 'queued'}`;
        const goal = document.createElement('p');
        goal.textContent = job.goal;
        card.append(heading, goal);
        for (const task of job.result?.tasks || []) {
          const detail = document.createElement('p');
          detail.textContent = `${task.id}: ${task.status}, ${task.attempts} attempts`;
          card.append(detail);
        }
        if (job.error) {
          const error = document.createElement('p'); error.textContent = job.reason || job.error; card.append(error);
        }
        const evidence = document.createElement('details');
        const summary = document.createElement('summary'); summary.textContent = 'Plan, checks and apply evidence';
        const text = document.createElement('pre'); text.textContent = JSON.stringify(job.result || job.events, null, 2);
        evidence.append(summary, text); card.append(evidence);
        if (job.status === 'needs_checker') {
          const checker = document.createElement('button'); checker.textContent = 'Continue with checker help';
          checker.addEventListener('click', async () => {
            checker.disabled = true;
            try {
              const result = await request(`/api/coding/projects/${encodeURIComponent(job.id)}/checker`, {method:'POST'});
              message.textContent = `Checker continuation ${result.job.id} queued`;
              await refresh();
            } catch (error) { message.textContent = error.message; checker.disabled = false; }
          });
          card.append(checker);
        }
        if (['queued','running'].includes(job.status)) {
          const stop = document.createElement('button'); stop.textContent = 'Stop at next safe boundary';
          stop.addEventListener('click', async () => {
            stop.disabled = true;
            try { await request(`/api/coding/projects/${encodeURIComponent(job.id)}/stop`, {method:'POST'}); }
            catch (error) { message.textContent = error.message; }
          });
          card.append(stop);
        }
        jobs.append(card);
      }
    } catch (error) { message.textContent = error.message; }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (pending) return; pending = true;
    form.querySelector('button').disabled = true;
    try {
      const result = await request('/api/coding/projects', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({goal:document.getElementById('codingProjectGoal').value, model:document.getElementById('codingProjectModel').value,
          checker_model:document.getElementById('codingProjectCheckerModel').value})});
      message.textContent = `Gamma job ${result.job.id}: ${result.job.status}`;
      await refresh();
    } catch (error) { message.textContent = error.message; }
    finally { pending = false; form.querySelector('button').disabled = false; }
  });
  refresh(); setInterval(refresh, 4000);
}
