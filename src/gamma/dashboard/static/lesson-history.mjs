// The model-authored history API is exposed through the existing lesson cards.
const container = document.getElementById('codingLessons');
function attach() {
  for (const card of container?.querySelectorAll('[data-lesson-id]') || []) {
    if (card.querySelector('[data-lesson-history]')) continue;
    const details = document.createElement('details');
    details.dataset.lessonHistory = 'true';
    const summary = document.createElement('summary');
    summary.textContent = 'Revision history';
    const content = document.createElement('div');
    details.append(summary, content);
    details.addEventListener('toggle', async () => {
      if (!details.open) return;
      content.textContent = 'Loading revisions…';
      try {
        const response = await fetch('/api/coding/lessons/' + encodeURIComponent(card.dataset.lessonId) + '/history');
        if (!response.ok) throw new Error('History unavailable');
        const result = await response.json();
        content.replaceChildren();
        for (const revision of result.history || []) {
          const entry = document.createElement('article');
          const metadata = document.createElement('p');
          metadata.textContent = [revision.updated_at || revision.timestamp, revision.provenance || revision.authority,
            revision.active ? 'enabled' : 'disabled'].filter(Boolean).join(' · ');
          const text = document.createElement('p');
          text.textContent = revision.text;
          const evidence = document.createElement('p');
          evidence.textContent = 'Evidence: ' + (revision.evidence_runs || []).join(', ');
          entry.append(metadata, text, evidence); content.append(entry);
        }
        if (!content.children.length) content.textContent = 'No revisions recorded yet.';
      } catch { content.textContent = 'Revision history could not be loaded.'; }
    });
    card.append(details);
  }
}
if (container) {
  new MutationObserver(attach).observe(container, { childList: true });
  attach();
}
