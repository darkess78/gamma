// Bounded self-improvement activity and operator controls.
const dashboardPage = String(window.GAMMA_DASHBOARD_PAGE || '').trim().toLowerCase();

if (dashboardPage === 'improvement') {
  let requestInFlight = false;
  let pollTimer = null;

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    const target = byId(id);
    if (target) target.textContent = value == null ? 'n/a' : String(value);
  }

  function node(tagName, className, text) {
    const target = document.createElement(tagName);
    if (className) target.className = className;
    if (text != null) target.textContent = String(text);
    return target;
  }

  function replace(targetId, children, emptyText) {
    const target = byId(targetId);
    if (!target) return;
    target.replaceChildren();
    if (!children.length) {
      target.appendChild(node('div', 'improvement-empty', emptyText));
      return;
    }
    children.forEach((child) => target.appendChild(child));
  }

  function displayTime(value) {
    if (!value) return 'Not recorded';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
  }

  function displayMetricValue(metric) {
    if (!metric || metric.value == null) return 'No sample';
    const value = Number(metric && metric.value);
    if (!Number.isFinite(value)) return 'No sample';
    const unit = String(metric.unit || '');
    if (unit === 'percent') return value.toFixed(2) + '%';
    if (unit === 'ms') return value.toFixed(value >= 100 ? 0 : 2) + ' ms';
    return value.toFixed(2) + (unit ? ' ' + unit : '');
  }

  function statusPill(text, state) {
    const pill = node('span', 'improvement-item-state', text);
    pill.dataset.state = String(state || 'unknown');
    return pill;
  }

  function detailRow(label, value) {
    const row = node('div', 'improvement-detail-row');
    row.appendChild(node('span', '', label));
    row.appendChild(node('strong', '', value));
    return row;
  }

  function renderSummary(payload) {
    const state = payload.state || {};
    const current = payload.current_series || null;
    const latest = payload.latest_series || null;
    const observation = payload.latest_observation || null;
    const chip = byId('improvementStateLabel');
    if (chip) {
      chip.textContent = state.label || 'Unknown';
      chip.dataset.state = state.code || 'unknown';
    }
    setText('improvementStateDetail', state.detail || 'No improvement state is available.');
    setText('improvementCurrentDomain', current ? current.domain : 'None active');
    setText(
      'improvementAttemptSummary',
      current
        ? current.attempt_count + ' / ' + current.maximum_attempts
        : (latest ? 'Last ' + latest.attempt_count + ' / ' + latest.maximum_attempts : 'No series')
    );
    setText('improvementSeriesCount', (payload.scan || {}).series_discovered || 0);
    setText('improvementEvidenceTime', observation ? displayTime(observation.generated_at) : 'No observation');
    setText('improvementUpdatedAt', 'Dashboard refreshed ' + displayTime(payload.generated_at));
  }

  function seriesCard(series, currentLabel) {
    const card = node('article', 'improvement-item');
    const heading = node('div', 'improvement-item-head');
    heading.appendChild(node('strong', '', currentLabel || series.id));
    heading.appendChild(statusPill(series.status, series.status));
    card.appendChild(heading);
    card.appendChild(node('p', 'improvement-item-copy', series.hypothesis));
    const details = node('div', 'improvement-detail-grid');
    details.appendChild(detailRow('Series', series.id));
    details.appendChild(detailRow('Domain', series.domain));
    details.appendChild(detailRow('Change class', series.change_class));
    details.appendChild(detailRow('Models', (series.models || []).join(', ') || 'None'));
    details.appendChild(detailRow('Attempts', series.attempt_count + ' of ' + series.maximum_attempts));
    details.appendChild(detailRow('Baseline', series.baseline_commit || 'Unknown'));
    const sourceScope = Array.isArray(series.source_scope) ? series.source_scope : [];
    if (sourceScope.length) {
      const scopeLabel = sourceScope.join(', ') + (series.source_scope_truncated ? ', …' : '');
      details.appendChild(detailRow('Source scope', scopeLabel));
    }
    if (series.terminal_reason) details.appendChild(detailRow('Terminal reason', series.terminal_reason));
    card.appendChild(details);
    return card;
  }

  function renderCurrentWork(payload) {
    const current = payload.current_series || null;
    const latest = payload.latest_series || null;
    const items = [];
    if (current) {
      items.push(seriesCard(current, current.status === 'running' ? 'Active isolated series' : 'Next isolated series'));
    } else if (latest) {
      const note = node('article', 'improvement-item');
      note.appendChild(node('strong', '', 'No series is active'));
      note.appendChild(node('p', 'improvement-item-copy', 'The most recent iteration is shown below. It has not changed the live checkout.'));
      items.push(note, seriesCard(latest, 'Most recent series'));
    }
    replace('improvementCurrentWork', items, 'No isolated improvement series has been recorded.');
  }

  function renderPolicy(payload) {
    const policy = payload.policy || {};
    const card = node('article', 'improvement-item');
    const rows = node('div', 'improvement-detail-grid');
    rows.appendChild(detailRow('Contract', policy.contract_loaded ? 'Version ' + policy.contract_version : 'Unavailable'));
    rows.appendChild(detailRow('Isolated candidates', policy.isolated_experiments_enabled ? 'Enabled' : 'Disabled'));
    rows.appendChild(detailRow('Recurring loop', policy.recurring_experiments_enabled ? 'Enabled' : 'Disabled'));
    rows.appendChild(detailRow('Automatic promotion', policy.automatic_promotion_enabled ? 'Enabled' : 'Not available'));
    card.appendChild(rows);
    if (payload.policy_error) card.appendChild(node('p', 'improvement-warning', payload.policy_error));
    replace('improvementPolicy', [card], 'Improvement control policy is unavailable.');
  }

  function renderMetrics(payload) {
    const observation = payload.latest_observation || null;
    const metrics = observation && Array.isArray(observation.metrics) ? observation.metrics : [];
    const roleOrder = { objective: 0, guardrail: 1, diagnostic: 2 };
    metrics.sort((left, right) => {
      const roleDelta = (roleOrder[left.role] ?? 9) - (roleOrder[right.role] ?? 9);
      return roleDelta || String(left.id).localeCompare(String(right.id));
    });
    const items = metrics.slice(0, 18).map((metric) => {
      const card = node('article', 'improvement-metric');
      const heading = node('div', 'improvement-item-head');
      heading.appendChild(node('strong', '', metric.id));
      heading.appendChild(statusPill(metric.role, metric.sufficient_data ? 'measured' : 'insufficient'));
      card.appendChild(heading);
      card.appendChild(node('div', 'improvement-metric-value', displayMetricValue(metric)));
      card.appendChild(node('div', 'improvement-item-meta', metric.statistic + ' · ' + metric.sample_count + ' samples'));
      return card;
    });
    replace('improvementMetrics', items, 'No aggregate observation has been recorded yet.');
  }

  function renderOpportunities(payload) {
    const observation = payload.latest_observation || null;
    const opportunities = observation && Array.isArray(observation.opportunities) ? observation.opportunities : [];
    const items = opportunities.map((opportunity) => {
      const card = node('article', 'improvement-item');
      const heading = node('div', 'improvement-item-head');
      heading.appendChild(node('strong', '', opportunity.domain + ' · ' + opportunity.kind));
      heading.appendChild(statusPill(opportunity.priority, opportunity.priority));
      card.appendChild(heading);
      card.appendChild(node('p', 'improvement-item-copy', opportunity.evidence));
      card.appendChild(node('p', 'improvement-next-step', 'Next: ' + opportunity.suggested_next_step));
      return card;
    });
    replace('improvementOpportunities', items, 'The latest observation did not identify a bounded next opportunity.');
  }

  function renderSeries(payload) {
    const series = Array.isArray(payload.recent_series) ? payload.recent_series : [];
    replace(
      'improvementRecentSeries',
      series.map((item) => seriesCard(item)),
      'No recent series are available.'
    );
  }

  function renderAttempts(payload) {
    const attempts = Array.isArray(payload.recent_attempts) ? payload.recent_attempts : [];
    const items = attempts.map((attempt) => {
      const card = node('article', 'improvement-item');
      const heading = node('div', 'improvement-item-head');
      heading.appendChild(node('strong', '', attempt.series_id + ' · attempt ' + attempt.attempt_number));
      heading.appendChild(statusPill(attempt.outcome, attempt.outcome));
      card.appendChild(heading);
      card.appendChild(node('div', 'improvement-item-meta', 'Model: ' + (attempt.actual_model || attempt.requested_model || 'Unknown')));
      if ((attempt.rejection_codes || []).length) {
        card.appendChild(node('p', 'improvement-warning', 'Evidence result: ' + attempt.rejection_codes.join(', ')));
      }
      card.appendChild(node('div', 'improvement-item-meta', 'Completed: ' + displayTime(attempt.completed_at)));
      return card;
    });
    replace('improvementRecentAttempts', items, 'No candidate attempts have completed.');
  }

  function renderSafeguards(payload) {
    const safeguards = Array.isArray(payload.safeguards) ? payload.safeguards : [];
    const items = safeguards.map((safeguard) => {
      const card = node('article', 'improvement-safeguard');
      const heading = node('div', 'improvement-item-head');
      heading.appendChild(node('strong', '', safeguard.label));
      heading.appendChild(statusPill(safeguard.enforced ? 'Enforced' : 'Check policy', safeguard.enforced ? 'enforced' : 'attention'));
      card.appendChild(heading);
      card.appendChild(node('p', 'improvement-item-copy', safeguard.detail));
      return card;
    });
    replace('improvementSafeguards', items, 'Safety status is unavailable.');
  }

  async function controlWork(requestId, action) {
    if (action === 'reject' && !window.confirm('Reject this isolated candidate? Its audit evidence will be retained and nothing will be promoted.')) {
      return;
    }
    const message = byId('improvementWorkMessage');
    if (message) message.textContent = action.charAt(0).toUpperCase() + action.slice(1) + ' requested…';
    try {
      const response = await fetch('/api/improvement/work/' + encodeURIComponent(requestId) + '/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'HTTP ' + response.status);
      if (message) message.textContent = 'Work request ' + action + ' accepted.';
      await loadImprovementStatus();
    } catch (error) {
      if (message) message.textContent = 'Control failed: ' + String(error);
    }
  }

  function workButton(label, className, requestId, action) {
    const button = node('button', className, label);
    button.type = 'button';
    button.addEventListener('click', () => controlWork(requestId, action));
    return button;
  }

  function renderWorkQueue(payload) {
    const queue = payload.work_queue || {};
    const requests = Array.isArray(queue.requests) ? queue.requests : [];
    setText(
      'improvementWorkerState',
      queue.worker_running
        ? 'Worker running · PID ' + (queue.worker_pid || 'unknown')
        : 'Worker idle'
    );
    const items = requests.map((request) => {
      const card = node('article', 'improvement-item');
      const heading = node('div', 'improvement-item-head');
      heading.appendChild(node('strong', '', request.goal));
      heading.appendChild(statusPill(request.status, request.status));
      card.appendChild(heading);
      const details = node('div', 'improvement-detail-grid');
      details.appendChild(detailRow('Mode', request.selection_mode));
      details.appendChild(detailRow('Stage', request.stage));
      details.appendChild(detailRow('Budget', request.budget_minutes + ' minutes'));
      details.appendChild(detailRow('Cycles', request.cycle_count + ' of ' + request.maximum_cycles));
      details.appendChild(detailRow('Models', (request.models || []).join(', ')));
      details.appendChild(detailRow('Started', displayTime(request.started_at)));
      card.appendChild(details);
      if (request.result_summary) card.appendChild(node('p', 'improvement-next-step', request.result_summary));
      const events = Array.isArray(request.events) ? request.events : [];
      if (events.length) {
        const latest = events[events.length - 1];
        card.appendChild(node('p', 'improvement-item-meta', displayTime(latest.at) + ' · ' + latest.message));
      }
      if (request.status === 'review_ready') {
        const controls = node('div', 'improvement-control-row');
        controls.appendChild(workButton('Reject candidate', 'ghost danger-outline', request.id, 'reject'));
        card.appendChild(controls);
      } else if (!['rejected', 'exhausted', 'failed', 'stopped'].includes(request.status)) {
        const controls = node('div', 'improvement-control-row');
        if (request.status === 'paused') {
          controls.appendChild(workButton('Resume', 'secondary', request.id, 'resume'));
        } else {
          controls.appendChild(workButton('Pause safely', 'ghost', request.id, 'pause'));
        }
        controls.appendChild(workButton('Stop safely', 'ghost danger-outline', request.id, 'stop'));
        card.appendChild(controls);
      }
      return card;
    });
    replace('improvementWorkQueue', items, 'No bounded work requests have been queued.');
  }

  function render(payload) {
    renderSummary(payload);
    renderCurrentWork(payload);
    renderPolicy(payload);
    renderMetrics(payload);
    renderOpportunities(payload);
    renderSeries(payload);
    renderAttempts(payload);
    renderSafeguards(payload);
    renderWorkQueue(payload);
  }

  function renderFailure(error) {
    const chip = byId('improvementStateLabel');
    if (chip) {
      chip.textContent = 'Status unavailable';
      chip.dataset.state = 'attention';
    }
    setText('improvementStateDetail', 'The read-only improvement status request failed. ' + String(error));
    setText('improvementUpdatedAt', 'Refresh failed ' + new Date().toLocaleString());
  }

  async function loadImprovementStatus() {
    if (requestInFlight) return;
    requestInFlight = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch('/api/improvement/status?_=' + Date.now(), {
        cache: 'no-store',
        signal: controller.signal
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'HTTP ' + response.status);
      render(payload);
    } catch (error) {
      renderFailure(error && error.name === 'AbortError' ? 'Request timed out.' : error);
    } finally {
      window.clearTimeout(timeout);
      requestInFlight = false;
    }
  }

  const refreshButton = byId('improvementRefreshButton');
  if (refreshButton) refreshButton.addEventListener('click', loadImprovementStatus);
  const workForm = byId('improvementWorkForm');
  if (workForm) workForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const startButton = byId('improvementStartButton');
    const message = byId('improvementWorkMessage');
    const selectionMode = String(byId('improvementSelectionMode').value || 'directed');
    let goal = String(byId('improvementGoal').value || '').trim();
    if (!goal && selectionMode === 'automatic') {
      goal = 'Choose the highest-priority measurable Gamma improvement and develop an isolated validated candidate.';
    }
    if (goal.length < 12) {
      if (message) message.textContent = 'Describe the improvement goal in at least 12 characters.';
      return;
    }
    const models = String(byId('improvementModels').value || '')
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean);
    const focus = String(byId('improvementFocusDomain').value || '').trim();
    const payload = {
      goal,
      selection_mode: selectionMode,
      focus_domains: focus ? [focus] : [],
      models,
      budget_minutes: Number(byId('improvementBudget').value),
      maximum_cycles: Number(byId('improvementMaximumCycles').value),
      maximum_attempts_per_series: Number(byId('improvementMaximumAttempts').value)
    };
    if (startButton) startButton.disabled = true;
    if (message) message.textContent = 'Queuing bounded autonomous work…';
    try {
      const response = await fetch('/api/improvement/work', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'HTTP ' + response.status);
      if (message) message.textContent = 'Queued ' + result.request.id + '. Gamma is starting safely.';
      await loadImprovementStatus();
    } catch (error) {
      if (message) message.textContent = 'Could not queue work: ' + String(error);
    } finally {
      if (startButton) startButton.disabled = false;
    }
  });
  loadImprovementStatus();
  pollTimer = window.setInterval(loadImprovementStatus, 10000);
  window.addEventListener('beforeunload', () => {
    if (pollTimer) window.clearInterval(pollTimer);
  });
}
