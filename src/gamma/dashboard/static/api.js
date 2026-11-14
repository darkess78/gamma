// api.js - stream, Twitch and output actions. Renderers for stream feeds live in stream.js.
(function () {
  'use strict';

  function element(id) {
    return document.getElementById(id);
  }

  function setText(id, text) {
    var target = element(id);
    if (target) target.textContent = text;
  }

  function setHtml(id, html) {
    var target = element(id);
    if (target) target.innerHTML = html;
  }

  function empty(message) {
    return window.streamEmptyHtml ? window.streamEmptyHtml(message) : message;
  }

  async function request(path, options) {
    var response = await fetch(path, Object.assign({ cache: 'no-store' }, options || {}));
    var payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || ('HTTP ' + response.status));
    return payload;
  }

  function postJson(path, body) {
    return request(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
  }

  async function loadFeed(path, targets) {
    try {
      var payload = await request(path);
      targets.forEach(function (entry) { setHtml(entry[0], window[entry[1]](payload)); });
    } catch (error) {
      targets.forEach(function (entry) { setHtml(entry[0], empty('Load failed. ' + String(error.message || error))); });
    }
  }

  async function loadStreamActivity() {
    await Promise.all([
      loadFeed('/api/stream/traces/recent?limit=30', [['streamTraceFeed', 'humanStreamTraces'], ['streamSafetyFeed', 'humanStreamSafety']]),
      loadFeed('/api/stream/outputs/recent?limit=30', [['streamOutputFeed', 'humanStreamOutputs']]),
      loadFeed('/api/stream/queue', [['streamQueueFeed', 'humanStreamQueue']]),
      loadFeed('/api/stream/temp-memory?limit=30', [['streamTempMemoryFeed', 'humanStreamTempMemory']]),
      loadFeed('/api/stream/self-goals?limit=30', [['streamSelfGoalFeed', 'humanStreamSelfGoals']])
    ]);
  }

  async function loadTwitchViewerTrust() {
    try {
      var payload = await request('/api/twitch/viewer-trust?limit=50');
      setText('twitchViewerTrust', window.humanTwitchViewerTrust(payload));
    } catch (error) {
      setText('twitchViewerTrust', 'Viewer trust load failed. ' + String(error.message || error));
    }
  }

  async function saveTwitchViewerTrust() {
    var payload = {
      platform: 'twitch',
      platform_user_id: element('twitchTrustUserId').value.trim(),
      display_name: element('twitchTrustDisplayName').value.trim(),
      trust_level: element('twitchTrustLevel').value,
      pronunciation_alias: element('twitchTrustAlias').value.trim(),
      notes: element('twitchTrustNotes').value.trim()
    };
    try {
      await postJson('/api/twitch/viewer-trust', payload);
      await loadTwitchViewerTrust();
    } catch (error) {
      setText('twitchViewerTrust', 'Viewer trust save failed. ' + String(error.message || error));
    }
  }

  async function runReplay(path, body) {
    setText('twitchReplayResult', 'Running replay...');
    try {
      var result = body ? await postJson(path, body) : await request(path, { method: 'POST' });
      setText('twitchReplayResult', window.humanTwitchReplayResult(result));
    } catch (error) {
      setText('twitchReplayResult', 'Replay failed. ' + String(error.message || error));
    }
  }

  function runTwitchReplay() {
    return runReplay('/api/twitch/replay', {
      jsonl: element('twitchReplayJsonl').value,
      session_id: element('twitchReplaySessionId').value.trim() || 'twitch-replay',
      fast_mode: !!element('twitchReplayFastMode').checked,
      synthesize_speech: !!element('twitchReplaySpeech').checked
    });
  }

  function runTwitchDryRunReplay() {
    return runReplay('/api/twitch/replay/dry-run');
  }

  function numberValue(id) {
    return Math.max(0, Number(element(id).value || 0));
  }

  async function saveTwitchSettings() {
    var payload = {
      dry_run: !!element('twitchDryRun').checked,
      voice_enabled: !!element('twitchVoiceEnabled').checked,
      subtitles_enabled: !!element('twitchSubtitlesEnabled').checked,
      ambient_chat_enabled: !!element('twitchAmbientChatEnabled').checked,
      mention_replies_enabled: !!element('twitchMentionRepliesEnabled').checked,
      spam_quips_enabled: !!element('twitchSpamQuipsEnabled').checked,
      self_goal_proposals_enabled: !!element('twitchSelfGoalProposalsEnabled').checked,
      llm_safety_review_enabled: !!element('twitchLlmSafetyReviewEnabled').checked,
      min_speech_gap_seconds: numberValue('twitchMinSpeechGapSeconds'),
      max_speech_seconds_per_minute: numberValue('twitchMaxSpeechSecondsPerMinute'),
      spam_quip_cooldown_seconds: numberValue('twitchSpamQuipCooldownSeconds')
    };
    setText('twitchSettingsStatus', 'Saving...');
    try {
      var result = await postJson('/api/twitch/settings', payload);
      if (window.clearSettingsDirty) window.clearSettingsDirty('twitchForm');
      if (window.renderTwitchSettings) window.renderTwitchSettings(result.settings || payload);
      setText('twitchSettingsStatus', result.detail || 'Twitch settings saved.');
      if (window.loadStatus) window.loadStatus();
    } catch (error) {
      setText('twitchSettingsStatus', 'Save failed. ' + String(error.message || error));
    }
  }

  async function stopStreamSpeech() {
    setText('streamStopStatus', 'Stopping speech and clearing subtitles...');
    try {
      var result = await request('/api/stream/stop', { method: 'POST' });
      setText('streamStopStatus', result.detail || 'Stream speech stopped.');
      if (element('streamActivityFold') && element('streamActivityFold').open) await loadStreamActivity();
    } catch (error) {
      setText('streamStopStatus', 'Stop speech failed. ' + String(error.message || error));
    }
  }

  // Top-bar emergency stop: halt stream speech and clear every output target.
  async function stopShanaOutput() {
    var notify = window.showActionResult || function () {};
    var targets = ['dashboard_monitor', 'stream_public', 'discord_call'];
    var failed = [];
    notify('Stopping output...', '');
    try {
      var stream = await fetch('/api/stream/stop', { method: 'POST' });
      if (!stream.ok) failed.push('stream speech');
      for (var i = 0; i < targets.length; i += 1) {
        var response = await fetch('/api/performer/targets/' + encodeURIComponent(targets[i]) + '/clear', { method: 'POST' });
        if (!response.ok) failed.push(targets[i]);
      }
      if (window.ttsPlayerClear) window.ttsPlayerClear();
      notify(failed.length ? 'Output stopped, but these did not confirm: ' + failed.join(', ') : 'Output stopped and cleared.', failed.length ? 'bad' : 'good');
      if (window.loadStatus) window.loadStatus();
    } catch (error) {
      notify('Stop output failed. ' + String(error.message || error), 'bad');
    }
  }

  async function clearStreamTempMemory() {
    if (!window.confirm('Clear temporary stream memory?')) return;
    try {
      var payload = await request('/api/stream/temp-memory', { method: 'DELETE' });
      setHtml('streamTempMemoryFeed', empty('Temp memory cleared. Deleted: ' + (payload.deleted || 0)));
      await loadStreamActivity();
    } catch (error) {
      setHtml('streamTempMemoryFeed', empty('Temp memory clear failed. ' + String(error.message || error)));
    }
  }

  async function setStreamSelfGoalStatus(actionName) {
    var goalId = String((element('streamSelfGoalId') || {}).value || '').trim();
    if (!goalId) {
      setHtml('streamSelfGoalFeed', empty('Enter a self-goal id first.'));
      return;
    }
    try {
      await request('/api/stream/self-goals/' + encodeURIComponent(goalId) + '/' + actionName, { method: 'POST' });
      await loadStreamActivity();
    } catch (error) {
      setHtml('streamSelfGoalFeed', empty('Self-goal update failed. ' + String(error.message || error)));
    }
  }

  async function clearStreamSelfGoals() {
    if (!window.confirm('Clear all stream self-goals?')) return;
    try {
      await request('/api/stream/self-goals/clear', { method: 'POST' });
      await loadStreamActivity();
    } catch (error) {
      setHtml('streamSelfGoalFeed', empty('Self-goals clear failed. ' + String(error.message || error)));
    }
  }

  // Collapsed panels only fetch while they are open.
  var activityFold = element('streamActivityFold');
  var activityTimer = null;
  if (activityFold) {
    activityFold.addEventListener('toggle', function () {
      if (activityTimer) window.clearInterval(activityTimer);
      activityTimer = null;
      if (activityFold.open) {
        loadStreamActivity();
        activityTimer = window.setInterval(function () {
          if (!document.hidden) loadStreamActivity();
        }, 10000);
      }
    });
  }
  var trustFold = element('viewerTrustFold');
  if (trustFold) {
    trustFold.addEventListener('toggle', function () {
      if (trustFold.open) loadTwitchViewerTrust();
    });
  }

  window.loadStreamActivity = loadStreamActivity;
  window.loadTwitchViewerTrust = loadTwitchViewerTrust;
  window.saveTwitchViewerTrust = saveTwitchViewerTrust;
  window.runTwitchReplay = runTwitchReplay;
  window.runTwitchDryRunReplay = runTwitchDryRunReplay;
  window.saveTwitchSettings = saveTwitchSettings;
  window.stopStreamSpeech = stopStreamSpeech;
  window.stopShanaOutput = stopShanaOutput;
  window.clearStreamTempMemory = clearStreamTempMemory;
  window.setStreamSelfGoalStatus = setStreamSelfGoalStatus;
  window.clearStreamSelfGoals = clearStreamSelfGoals;
})();
