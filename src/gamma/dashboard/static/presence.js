// presence.js - Shana Presence controls for Gamma dashboard
(function () {
  'use strict';

  function element(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    var target = element(id);
    if (target) target.textContent = value == null ? 'n/a' : String(value);
  }

  function modeLabel(mode) {
    var value = String(mode || 'sleep').replace(/_/g, ' ');
    return value.replace(/\b\w/g, function (ch) { return ch.toUpperCase(); });
  }

  function boolText(value) {
    return value ? 'on' : 'off';
  }

  function renderPresenceStatus(payload) {
    payload = payload || {};
    var state = payload.state || {};
    var runtime = payload.runtime || {};
    var shana = runtime.shana || {};
    var shanaProcess = shana.process || {};
    var shanaHealth = shana.api_health || {};
    var twitch = runtime.twitch || {};
    var worker = twitch.worker || {};
    var eventsub = twitch.eventsub || {};
    var ready = twitch.stream_ready || {};
    var performer = runtime.performer || {};
    var stats = performer.stats || {};
    var outputs = state.outputs || {};
    var autonomy = state.autonomy || {};
    var safety = state.safety || {};
    var inputs = state.inputs || {};
    var muted = Array.isArray(stats.muted_targets) ? stats.muted_targets : [];
    var mode = String(state.mode || 'sleep');

    setText('presenceModeStatus', [
      'Mode: ' + modeLabel(mode),
      state.requires_confirmation ? 'Needs confirmation before public output resumes.' : '',
      'Desired: ' + modeLabel(state.desired_mode || mode),
      'Updated: ' + (state.updated_at || 'n/a')
    ].filter(Boolean).join('\n'));

    setText('presenceRuntimeStatus', [
      'Shana process: ' + (shanaProcess.running ? 'running' : 'stopped'),
      'Shana API: ' + (shanaHealth.ok ? 'healthy' : (shanaHealth.detail || 'unavailable')),
      'Twitch IRC: ' + ((worker.process || {}).running ? 'running' : 'stopped'),
      'EventSub: ' + ((eventsub.process || {}).running ? 'running' : 'stopped'),
      'Readiness: ' + (ready.mode || 'unknown')
    ].join('\n'));

    setText('presenceOutputStatus', [
      'Dashboard monitor: ' + boolText(outputs.dashboard_monitor),
      'Stream public: ' + boolText(outputs.stream_public),
      'Voice: ' + boolText(outputs.voice),
      'Subtitles: ' + boolText(outputs.subtitles),
      'Local mic: ' + boolText(inputs.local_mic),
      'Ambient: ' + boolText(autonomy.ambient_chat_enabled),
      'Proactive: ' + boolText(autonomy.proactive_idle_enabled),
      'Safety review: ' + boolText(safety.llm_safety_review_enabled),
      'Dry run: ' + boolText(safety.dry_run),
      'Muted targets: ' + (muted.length ? muted.join(', ') : 'none')
    ].join('\n'));

    ['sleep', 'wake', 'go_live', 'break'].forEach(function (name) {
      var id = 'presence' + modeLabel(name).replace(/\s+/g, '') + 'Button';
      var button = element(id);
      if (!button) return;
      button.classList.toggle('active', mode === name);
      button.setAttribute('aria-pressed', mode === name ? 'true' : 'false');
    });

    setText('overviewPresenceStatus', modeLabel(mode));
    setText('overviewPresenceMini', modeLabel(mode));
  }

  async function loadPresence() {
    try {
      var response = await fetch('/api/presence?_=' + Date.now(), { cache: 'no-store' });
      var payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || ('HTTP ' + response.status));
      renderPresenceStatus(payload);
      return payload;
    } catch (error) {
      setText('presenceModeStatus', 'Presence load failed.\n' + String(error));
      throw error;
    }
  }

  async function setPresenceMode(mode) {
    var normalized = String(mode || '').trim();
    var body = { mode: normalized };
    if (normalized === 'go_live') {
      if (!window.confirm('Go Live enables public stream output for this Shana backend session.')) {
        return;
      }
      body.confirm_public_output = true;
    }
    setText('presenceModeStatus', 'Setting Presence to ' + modeLabel(normalized) + '...');
    try {
      var response = await fetch('/api/presence/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      var payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || ('HTTP ' + response.status));
      await loadPresence();
      if (window.loadStatus) window.setTimeout(window.loadStatus, 350);
      return payload;
    } catch (error) {
      setText('presenceModeStatus', 'Presence update failed.\n' + String(error));
      throw error;
    }
  }

  window.renderPresenceStatus = renderPresenceStatus;
  window.loadPresence = loadPresence;
  window.setPresenceMode = setPresenceMode;

  if (String(window.GAMMA_DASHBOARD_PAGE || '') === 'presence') {
    window.setTimeout(function () {
      loadPresence().catch(function () {});
    }, 0);
  }
})();
