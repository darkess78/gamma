if (window.GAMMA_DASHBOARD_PAGE === 'improvement') {
  const form = document.getElementById('autonomousCodingForm');
  const list = document.getElementById('autonomousCodingJobs');
  const message = document.getElementById('autonomousCodingMessage');
  let submitting = false;
  async function request(url, options) {
    const response = await fetch(url, options);
    const value = await response.json();
    if (!response.ok) throw new Error(value.detail || 'Coding request failed');
    return value;
  }
  async function refresh() {
    try {
      const value = await request('/api/coding/work');
      list.replaceChildren();
      for (const job of value.jobs) {
        const card = document.createElement('article'); card.className = 'improvement-item';
        const title = document.createElement('strong'); title.textContent = `${job.status} · ${job.stage}`;
        const goal = document.createElement('p'); goal.textContent = job.goal;
        card.append(title, goal);
        if (job.reason) {const reason = document.createElement('p'); reason.textContent = job.reason; card.append(reason);}
        for (const task of job.tasks || []) {
          const text = document.createElement('p'); text.textContent = `${task.id}: ${task.status}, ${task.attempts || 0} attempts`;
          card.append(text);
        }
        const details = document.createElement('details'); const summary = document.createElement('summary');
        summary.textContent = 'Work events and verified application';
        if (job.learning) {
          const learning = document.createElement('p');
          learning.textContent = job.learning.recorded
            ? `Editable learning recorded: ${job.learning.lesson_ids.join(', ')}`
            : `Learning not recorded${job.learning.error ? ': ' + job.learning.error : ''}`;
          card.append(learning);
        }
        const output = document.createElement('pre'); output.textContent = JSON.stringify({events:job.events,application:job.application,learning:job.learning},null,2);
        details.append(summary,output);card.append(details);
        if (['queued','running','deferred','paused'].includes(job.status)) {
          for (const action of ['pause','resume','stop']) {
            const button = document.createElement('button'); button.textContent = action;
            button.addEventListener('click', async () => {
              try {await request(`/api/coding/work/${encodeURIComponent(job.id)}/control`,{
                method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
                message.textContent = `${action} requested at the next safe boundary`; await refresh();
              } catch (error) {message.textContent = error.message;}
            });card.append(button);
          }
        }
        list.append(card);
      }
    } catch (error) {message.textContent = error.message;}
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (submitting) return; submitting = true;
    form.querySelector('button').disabled = true;
    try {
      const value = await request('/api/coding/work',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({goal:document.getElementById('autonomousCodingGoal').value,
          model:document.getElementById('autonomousCodingModel').value,
          checker_model:document.getElementById('autonomousCodingChecker').value,
          budget_seconds:Number(document.getElementById('autonomousCodingBudget').value)})});
      message.textContent = `Gamma accepted ${value.job.id}`; await refresh();
    } catch (error) {message.textContent = error.message;}
    finally {submitting=false;form.querySelector('button').disabled=false;}
  });
  refresh();setInterval(refresh,3000);
}
