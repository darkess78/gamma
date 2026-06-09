// api.js - API call handlers and status management for Gamma dashboard
(function () {
  async function action(path, options) {
    options = options || {};
    try {
      if (options.confirmMessage && !window.confirm(options.confirmMessage)) {
        return;
      }
      postClientLog('action_start', { path: path });
      applyOptimisticActionState(path);
      var fetchOptions = { method: 'POST' };
      if (typeof options.body !== 'undefined') {
        fetchOptions.headers = { 'Content-Type': 'application/json' };
        fetchOptions.body = JSON.stringify(options.body);
      }
      var response = await fetch(path, fetchOptions);
      var payload = await response.json();
      if (!response.ok) {
        postClientLog('action_error', { path: path, payload: payload });
        alert(pretty(payload));
        return;
      }
      postClientLog('action_ok', { path: path });
      if (options.redirectUrl) {
        window.location.href = options.redirectUrl;
        return;
      }
      if (path === '/api/dashboard/stop' || path === '/api/all/stop') {
        updateStamp('Stopping...');
        setTextIfChanged('backendHealth', 'Shutdown requested.', 'backendHealthAction');
        return;
      }
      if (path === '/api/providers/tts/test') {
        var nameBeforeTest = ttsLastArtifactName;
        loadStatus().then(function () {
          if (ttsLastArtifactName !== nameBeforeTest && ttsLastArtifactName) {
            ttsPlayerLoad(ttsLastArtifactName);
          } else if (ttsLastArtifactName === nameBeforeTest) {
            startArtifactPoll(nameBeforeTest);
          }
        });
      } else {
        scheduleStatusRefreshes();
      }
    } catch (error) {
      postClientLog('action_exception', { path: path, error: String(error) });
      updateStamp('Action failed');
      setTextIfChanged('backendHealth', 'Dashboard action failed.\n' + String(error), 'backendHealthAction');
    }
  }

  async function loadStatus() {
    try {
      updateStamp('Loading...');
      postClientLog('load_start', { at: new Date().toISOString() });
      var response = await fetch('/api/status?_=' + Date.now(), { cache: 'no-store' });
      if (!response.ok) {
        throw new Error('HTTP ' + response.status);
      }
      var data = await response.json();
      postClientLog('load_ok', { hasShana: !!data.shana, hasMachine: !!data.machine });
      latestData = data;
      updateOutputViewLinks();
      renderPanels(data);
      loadTwitchViewerTrust();
      loadStreamActivity();
    } catch (error) {
      postClientLog('load_exception', { error: String(error) });
      updateStamp('Load failed');
      setTextIfChanged('backendHealth', 'Dashboard failed to render data.\n' + String(error), 'backendHealthLoadError');
    }
  }

  async function loadStreamActivity() {
    try {
      var traceResponse = await fetch('/api/stream/traces/recent?limit=30', { cache: 'no-store' });
      var tracePayload = await traceResponse.json();
      if (!traceResponse.ok) throw new Error(tracePayload.detail || ('traces HTTP ' + traceResponse.status));
      renderHtmlBlockIfChanged('streamTraceFeed', tracePayload, humanStreamTraces(tracePayload), 'streamTraceFeed');
      renderHtmlBlockIfChanged('streamSafetyFeed', tracePayload, humanStreamSafety(tracePayload), 'streamSafetyFeed');
    } catch (error) {
      renderHtmlBlockIfChanged('streamTraceFeed', { error: String(error) }, streamEmptyHtml('Stream trace load failed. ' + String(error)), 'streamTraceFeed');
      renderHtmlBlockIfChanged('streamSafetyFeed', { error: String(error) }, streamEmptyHtml('Safety log load failed. ' + String(error)), 'streamSafetyFeed');
    }

    try {
      var outputResponse = await fetch('/api/stream/outputs/recent?limit=30', { cache: 'no-store' });
      var outputPayload = await outputResponse.json();
      if (!outputResponse.ok) throw new Error(outputPayload.detail || ('outputs HTTP ' + outputResponse.status));
      renderHtmlBlockIfChanged('streamOutputFeed', outputPayload, humanStreamOutputs(outputPayload), 'streamOutputFeed');
    } catch (error) {
      renderHtmlBlockIfChanged('streamOutputFeed', { error: String(error) }, streamEmptyHtml('Stream output load failed. ' + String(error)), 'streamOutputFeed');
    }

    try {
      var queueResponse = await fetch('/api/stream/queue', { cache: 'no-store' });
      var queuePayload = await queueResponse.json();
      if (!queueResponse.ok) throw new Error(queuePayload.detail || ('queue HTTP ' + queueResponse.status));
      renderHtmlBlockIfChanged('streamQueueFeed', queuePayload, humanStreamQueue(queuePayload), 'streamQueueFeed');
    } catch (error) {
      renderHtmlBlockIfChanged('streamQueueFeed', { error: String(error) }, streamEmptyHtml('Stream queue load failed. ' + String(error)), 'streamQueueFeed');
    }

    try {
      var memoryResponse = await fetch('/api/stream/temp-memory?limit=30', { cache: 'no-store' });
      var memoryPayload = await memoryResponse.json();
      if (!memoryResponse.ok) throw new Error(memoryPayload.detail || ('temp memory HTTP ' + memoryResponse.status));
      renderHtmlBlockIfChanged('streamTempMemoryFeed', memoryPayload, humanStreamTempMemory(memoryPayload), 'streamTempMemoryFeed');
    } catch (error) {
      renderHtmlBlockIfChanged('streamTempMemoryFeed', { error: String(error) }, streamEmptyHtml('Temp memory load failed. ' + String(error)), 'streamTempMemoryFeed');
    }

    try {
      var goalResponse = await fetch('/api/stream/self-goals?limit=30', { cache: 'no-store' });
      var goalPayload = await goalResponse.json();
      if (!goalResponse.ok) throw new Error(goalPayload.detail || ('self-goals HTTP ' + goalResponse.status));
      renderHtmlBlockIfChanged('streamSelfGoalFeed', goalPayload, humanStreamSelfGoals(goalPayload), 'streamSelfGoalFeed');
    } catch (error) {
      renderHtmlBlockIfChanged('streamSelfGoalFeed', { error: String(error) }, streamEmptyHtml('Self-goals load failed. ' + String(error)), 'streamSelfGoalFeed');
    }
  }

  async function loadTwitchViewerTrust() {
    try {
      var response = await fetch('/api/twitch/viewer-trust?limit=50', { cache: 'no-store' });
      var payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || ('HTTP ' + response.status));
      }
      renderBlockIfChanged('twitchViewerTrust', payload, humanTwitchViewerTrust(payload), 'twitchViewerTrust');
    } catch (error) {
      renderBlockIfChanged('twitchViewerTrust', { error: String(error) }, 'Viewer trust load failed.\n' + String(error), 'twitchViewerTrust');
    }
  }

  async function saveTwitchViewerTrust() {
    var payload = {
      platform: 'twitch',
      platform_user_id: document.getElementById('twitchTrustUserId').value.trim(),
      display_name: document.getElementById('twitchTrustDisplayName').value.trim(),
      trust_level: document.getElementById('twitchTrustLevel').value,
      pronunciation_alias: document.getElementById('twitchTrustAlias').value.trim(),
      notes: document.getElementById('twitchTrustNotes').value.trim()
    };
    try {
      var response = await fetch('/api/twitch/viewer-trust', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      var result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || ('HTTP ' + response.status));
      }
      renderBlockIfChanged('twitchViewerTrust', result, humanTwitchViewerTrust(result), 'twitchViewerTrust');
    } catch (error) {
      renderBlockIfChanged('twitchViewerTrust', { error: String(error) }, 'Viewer trust save failed.\n' + String(error), 'twitchViewerTrust');
    }
  }

  async function runTwitchReplay() {
    var payload = {
      jsonl: document.getElementById('twitchReplayJsonl').value,
      session_id: document.getElementById('twitchReplaySessionId').value.trim() || 'twitch-replay',
      fast_mode: !!document.getElementById('twitchReplayFastMode').checked,
      synthesize_speech: !!document.getElementById('twitchReplaySpeech').checked
    };
    try {
      renderBlockIfChanged('twitchReplayResult', { status: 'running' }, 'Running replay...', 'twitchReplayResult');
      var response = await fetch('/api/twitch/replay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      var result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || ('HTTP ' + response.status));
      }
      renderBlockIfChanged('twitchReplayResult', result, humanTwitchReplayResult(result), 'twitchReplayResult');
      scheduleStatusRefreshes();
    } catch (error) {
      renderBlockIfChanged('twitchReplayResult', { error: String(error) }, 'Replay failed.\n' + String(error), 'twitchReplayResult');
    }
  }

  async function runTwitchDryRunReplay() {
    try {
      renderBlockIfChanged('twitchReplayResult', { status: 'running' }, 'Running dry-run replay...', 'twitchReplayResult');
      var response = await fetch('/api/twitch/replay/dry-run', { method: 'POST' });
      var result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || ('HTTP ' + response.status));
      }
      renderBlockIfChanged('twitchReplayResult', result, humanTwitchReplayResult(result), 'twitchReplayResult');
      scheduleStatusRefreshes();
    } catch (error) {
      renderBlockIfChanged('twitchReplayResult', { error: String(error) }, 'Dry-run replay failed.\n' + String(error), 'twitchReplayResult');
    }
  }

  async function saveTwitchSettings() {
    var payload = {
      dry_run: !!document.getElementById('twitchDryRun').checked,
      voice_enabled: !!document.getElementById('twitchVoiceEnabled').checked,
      subtitles_enabled: !!document.getElementById('twitchSubtitlesEnabled').checked,
      ambient_chat_enabled: !!document.getElementById('twitchAmbientChatEnabled').checked,
      mention_replies_enabled: !!document.getElementById('twitchMentionRepliesEnabled').checked,
      spam_quips_enabled: !!document.getElementById('twitchSpamQuipsEnabled').checked,
      self_goal_proposals_enabled: !!document.getElementById('twitchSelfGoalProposalsEnabled').checked,
      llm_safety_review_enabled: !!document.getElementById('twitchLlmSafetyReviewEnabled').checked,
      min_speech_gap_seconds: Math.max(0, Number(document.getElementById('twitchMinSpeechGapSeconds').value || 0)),
      max_speech_seconds_per_minute: Math.max(0, Number(document.getElementById('twitchMaxSpeechSecondsPerMinute').value || 0)),
      spam_quip_cooldown_seconds: Math.max(0, Number(document.getElementById('twitchSpamQuipCooldownSeconds').value || 0))
    };
    try {
      var response = await fetch('/api/twitch/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      var result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || ('HTTP ' + response.status));
      }
      renderTwitchSettings(result.settings || payload);
      renderBlockIfChanged('twitchSettingsStatus', result, result.detail || 'Twitch controls saved.', 'twitchSettingsStatus');
      await loadStatus();
    } catch (error) {
      renderBlockIfChanged('twitchSettingsStatus', { error: String(error) }, 'Twitch controls save failed.\n' + String(error), 'twitchSettingsStatus');
    }
  }

  async function stopStreamSpeech() {
    try {
      renderBlockIfChanged('streamStopStatus', { status: 'running' }, 'Stopping speech and clearing subtitles...', 'streamStopStatus');
      var response = await fetch('/api/stream/stop', { method: 'POST' });
      var result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || ('HTTP ' + response.status));
      }
      renderBlockIfChanged('streamStopStatus', result, humanStreamStopResult(result), 'streamStopStatus');
      await loadStreamActivity();
    } catch (error) {
      renderBlockIfChanged('streamStopStatus', { error: String(error) }, 'Stop speech failed.\n' + String(error), 'streamStopStatus');
    }
  }

  async function stopShanaOutput() {
    var targets = ['dashboard_monitor', 'stream_public', 'discord_call'];
    var results = [];
    try {
      renderBlockIfChanged('streamStopStatus', { status: 'running' }, 'Stopping output and clearing targets...', 'streamStopStatus');
      var streamResponse = await fetch('/api/stream/stop', { method: 'POST' });
      results.push({ target: 'stream_stop', ok: streamResponse.ok, status: streamResponse.status });
      for (var i = 0; i < targets.length; i++) {
        var target = targets[i];
        var response = await fetch('/api/performer/targets/' + encodeURIComponent(target) + '/clear', { method: 'POST' });
        results.push({ target: target, ok: response.ok, status: response.status });
      }
      setSubtitleState({ transcript: '', reply: '', partial: '' });
      ttsPlayerClear();
      setTextIfChanged('overviewOutputStatus', 'cleared');
      setTextIfChanged('overviewTurnMini', 'Cleared');
      renderBlockIfChanged('streamStopStatus', { results: results }, humanOutputStopResult(results), 'streamStopStatus');
      await loadStreamActivity();
      await loadStatus();
    } catch (error) {
      renderBlockIfChanged('streamStopStatus', { error: String(error) }, 'Stop output failed.\n' + String(error), 'streamStopStatus');
      postClientLog('stop_output_error', { error: String(error) });
    }
  }

  async function clearStreamTempMemory() {
    try {
      var response = await fetch('/api/stream/temp-memory', { method: 'DELETE' });
      var payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || ('temp memory clear HTTP ' + response.status));
      renderHtmlBlockIfChanged('streamTempMemoryFeed', payload, streamEmptyHtml('Temp memory cleared. Deleted: ' + (payload.deleted || 0)), 'streamTempMemoryFeed');
      await loadStreamActivity();
    } catch (error) {
      renderHtmlBlockIfChanged('streamTempMemoryFeed', { error: String(error) }, streamEmptyHtml('Temp memory clear failed. ' + String(error)), 'streamTempMemoryFeed');
    }
  }

  async function setStreamSelfGoalStatus(actionName) {
    var input = document.getElementById('streamSelfGoalId');
    var goalId = input ? String(input.value || '').trim() : '';
    if (!goalId) {
      renderHtmlBlockIfChanged('streamSelfGoalFeed', { error: 'missing-goal-id' }, streamEmptyHtml('Enter a self-goal id first.'), 'streamSelfGoalFeed');
      return;
    }
    try {
      var response = await fetch('/api/stream/self-goals/' + encodeURIComponent(goalId) + '/' + actionName, { method: 'POST' });
      var payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || ('self-goal HTTP ' + response.status));
      renderHtmlBlockIfChanged('streamSelfGoalFeed', payload, streamEmptyHtml('Goal #' + payload.id + ' is now ' + payload.status + '.'), 'streamSelfGoalFeed');
      await loadStreamActivity();
    } catch (error) {
      renderHtmlBlockIfChanged('streamSelfGoalFeed', { error: String(error) }, streamEmptyHtml('Self-goal update failed. ' + String(error)), 'streamSelfGoalFeed');
    }
  }

  async function clearStreamSelfGoals() {
    try {
      var response = await fetch('/api/stream/self-goals/clear', { method: 'POST' });
      var payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || ('self-goals clear HTTP ' + response.status));
      renderHtmlBlockIfChanged('streamSelfGoalFeed', payload, streamEmptyHtml('Self-goals cleared. Count: ' + (payload.cleared || 0)), 'streamSelfGoalFeed');
      await loadStreamActivity();
    } catch (error) {
      renderHtmlBlockIfChanged('streamSelfGoalFeed', { error: String(error) }, streamEmptyHtml('Self-goals clear failed. ' + String(error)), 'streamSelfGoalFeed');
    }
  }

  async function loadRuntimeStatus() {
    try {
      var response = await fetch('/api/status/runtime?_=' + Date.now(), { cache: 'no-store' });
      if (response.status === 404) {
        runtimeStatusSupported = false;
        await loadStatus();
        return;
      }
      if (!response.ok) {
        throw new Error('HTTP ' + response.status);
      }
      var runtime = await response.json();
      latestData = latestData || {};
      latestData.shana = Object.assign({}, latestData.shana || {}, runtime.shana || {});
      latestData.machine = runtime.machine || {};
      renderRuntimePanels(latestData);
      updateStamp('Last refreshed: ' + new Date().toLocaleString());
    } catch (error) {
      postClientLog('runtime_load_exception', { error: String(error) });
      updateStamp('Runtime refresh failed');
    }
  }

  function scheduleStatusRefreshes() {
    loadStatus();
    setTimeout(function () { loadStatus(); }, 350);
    setTimeout(function () { loadStatus(); }, 1000);
  }

  function scheduleRuntimePoll() {
    setTimeout(async function () {
      if (runtimeStatusSupported) {
        await loadRuntimeStatus();
      } else {
        await loadStatus();
      }
      scheduleRuntimePoll();
    }, runtimePollMs);
  }

  function postClientLog(kind, detail) {
    try {
      fetch('/api/client-log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: kind, detail: detail })
      });
    } catch (error) {
    }
  }

  function applyOptimisticActionState(path) {
    if (!latestData) return;

    latestData.provider_actions = {
      action: path,
      status: 'pending',
      detail: 'Action requested. Waiting for updated state...',
      ran_at: new Date().toISOString()
    };

    if (path === '/api/shana/start') {
      latestData.shana = latestData.shana || {};
      latestData.shana.process = latestData.shana.process || {};
      latestData.shana.process.running = true;
    } else if (path === '/api/shana/stop' || path === '/api/all/stop') {
      latestData.shana = latestData.shana || {};
      latestData.shana.process = latestData.shana.process || {};
      latestData.shana.process.running = false;
      if (path === '/api/all/stop') {
        latestData.twitch = latestData.twitch || {};
        latestData.twitch.worker = latestData.twitch.worker || {};
        latestData.twitch.worker.process = latestData.twitch.worker.process || {};
        latestData.twitch.worker.process.running = false;
      }
    } else if (path === '/api/shana/restart') {
      latestData.shana = latestData.shana || {};
      latestData.shana.process = latestData.shana.process || {};
      latestData.shana.process.running = true;
    } else if (path === '/api/twitch/worker/start') {
      latestData.twitch = latestData.twitch || {};
      latestData.twitch.worker = latestData.twitch.worker || {};
      latestData.twitch.worker.process = latestData.twitch.worker.process || {};
      latestData.twitch.worker.process.running = true;
    } else if (path === '/api/twitch/worker/stop') {
      latestData.twitch = latestData.twitch || {};
      latestData.twitch.worker = latestData.twitch.worker || {};
      latestData.twitch.worker.process = latestData.twitch.worker.process || {};
      latestData.twitch.worker.process.running = false;
    } else if (path === '/api/twitch/eventsub/start') {
      latestData.twitch = latest.twitch || {};
      latestData.twitch.eventsub = latestData.twitch.eventsub || {};
      latestData.twitch.eventsub.process = latestData.twitch.eventsub.process || {};
      latestData.twitch.eventsub.process.running = true;
    } else if (path === '/api/twitch/eventsub/stop') {
      latestData.twitch = latestData.twitch || {};
      latestData.twitch.eventsub = latestData.twitch.eventsub || {};
      latestData.twitch.eventsub.process = latestData.twitch.eventsub.process || {};
      latestData.twitch.eventsub.process.running = false;
    }

    renderPanels(latestData);
  }

  function applyOptimisticTtsSelection(update) {
    if (!latestData) return;
    var providers = latestData.providers || {};
    var tts = providers.tts || {};
    if (typeof update.selected_provider !== 'undefined') tts.selected_provider = update.selected_provider;
    if (typeof update.selected_profile !== 'undefined') tts.selected_profile = update.selected_profile;
    if (typeof update.selected_profile_label !== 'undefined') tts.selected_profile_label = update.selected_profile_label;
    if (typeof update.restart_required !== 'undefined') tts.restart_required = update.restart_required;
    if (typeof update.editor_profile !== 'undefined') tts.editor_profile = update.editor_profile;
    providers.tts = tts;
    latestData.providers = providers;
    renderPanels(latestData);
  }

  function updateStamp(text) {
    var stamp = document.getElementById('stamp');
    if (stamp) stamp.textContent = text;
  }

  function setTextIfChanged(elementId, value, cacheKey) {
    var el = document.getElementById(elementId);
    if (!el) return;
    var key = cacheKey || elementId;
    if (sectionHashes && sectionHashes[key] === value) {
      return;
    }
    if (sectionHashes) sectionHashes[key] = value;
    el.textContent = value;
  }

  function renderBlockIfChanged(elementId, rawValue, humanText, cacheKey) {
    if (!document.getElementById(elementId)) return;
    var key = cacheKey || elementId;
    var nextKey = viewMode === 'json' ? pretty(rawValue) : humanText;
    if (sectionHashes && sectionHashes[key] === nextKey) {
      return;
    }
    if (sectionHashes) sectionHashes[key] = nextKey;
    renderBlock(elementId, rawValue, humanText);
  }

  function renderHtmlBlockIfChanged(elementId, rawValue, humanHtml, cacheKey) {
    var el = document.getElementById(elementId);
    if (!el) return;
    var key = cacheKey || elementId;
    var nextKey = viewMode === 'json' ? pretty(rawValue) : humanHtml;
    if (sectionHashes && sectionHashes[key] === nextKey) {
      return;
    }
    if (sectionHashes) sectionHashes[key] = nextKey;
    if (viewMode === 'json') {
      el.classList.add('json-render');
      el.textContent = pretty(rawValue);
    } else {
      el.classList.remove('json-render');
      el.innerHTML = humanHtml;
    }
  }

  function pretty(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
    }
  }

  function sectionHashesValue(value) {
    var hash = JSON.stringify(value);
    try {
      return JSON.stringify(hash);
    } catch (e) {
      return String(hash);
    }
  }

  var runtimeStatusSupported = true;
  var latestData = null;
  viewMode = localStorage.getItem('gammaDashboardViewMode') || 'human';
  sectionHashes = {};

})();