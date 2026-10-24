if (window.GAMMA_DASHBOARD_PAGE === 'improvement') {
  const section = document.createElement('section');
  const heading = document.createElement('h3'); heading.textContent = 'Local LoRA candidates';
  const note = document.createElement('p');
  note.textContent = 'Isolated Qwen3 4B trial: recheck fictional examples, train an adapter, compare eight withheld coding tasks. Production stays on its current model.';
  const start = document.createElement('button'); start.textContent = 'Run isolated LoRA trial';
  const status = document.createElement('p'); const list = document.createElement('div');
  section.append(heading, note, start, status, list);
  document.getElementById('autonomousCodingForm').parentElement.append(section);
  async function request(url, options) {
    const response = await fetch(url, options); const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Candidate request failed');
    return result;
  }
  async function refresh() {
    try {
      const result = await request('/api/coding/candidates'); list.replaceChildren();
      for (const candidate of result.candidates) {
        const card = document.createElement('article');
        const text = document.createElement('p');
        text.textContent = `${candidate.status} · ${candidate.stage} · ${candidate.id}`;
        const detail = document.createElement('pre'); detail.textContent = JSON.stringify(candidate, null, 2);
        card.append(text, detail);
        if (['preparing', 'running'].includes(candidate.status)) {
          const stop = document.createElement('button'); stop.textContent = 'Stop at safe boundary';
          stop.onclick = async () => {
            try {await request(`/api/coding/candidates/${encodeURIComponent(candidate.id)}/stop`, {method:'POST'});
              status.textContent = 'Stop requested';}
            catch (error) {status.textContent = error.message;}
          };
          card.append(stop);
        }
        list.append(card);
      }
    } catch (error) {status.textContent = error.message;}
  }
  start.onclick = async () => {
    start.disabled = true;
    try {await request('/api/coding/candidates', {method:'POST'}); status.textContent = 'Trial started; results will appear here';}
    catch (error) {status.textContent = error.message;}
    finally {start.disabled = false; await refresh();}
  };
  refresh(); setInterval(refresh, 5000);
}
