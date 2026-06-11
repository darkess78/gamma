// Shared status loading and rendering for every Gamma dashboard page.
(function () {
  'use strict';

  var latestStatus = null;
  var pollTimer = null;

  function element(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    var target = element(id);
    if (target) target.textContent = value == null ? 'n/a' : String(value);
  }

  function setValue(id, value) {
    var target = element(id);
    if (target && document.activeElement !== target) target.value = value == null ? '' : String(value);
  }

  function setChecked(id, value) {
    var target = element(id);
    if (target && document.activeElement !== target) target.checked = !!value;
  }

  function formatBytes(bytes) {
    var value = Number(bytes);
    if (!isFinite(value) || value < 0) return 'n/a';
    var units = ['B', 'KB', 'MB', 'GB', 'TB'];
    var index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return value.toFixed(index ? 1 : 0) + ' ' + units[index];
  }

  function formatPercent(value) {
    var number = Number(value);
    return isFinite(number) ? number.toFixed(1) + '%' : 'n/a';
  }

  function healthText(health) {
    if (!health) return 'unknown';
    if (health.ok) return health.detail && health.detail !== 'ready' ? 'healthy (' + health.detail + ')' : 'healthy';
    return health.detail || 'unavailable';
  }

  function providerLine(name, provider) {
    provider = provider || {};
    var parts = [provider.provider || 'not configured'];
    if (provider.model) parts.push(provider.model);
    if (provider.profile_label) parts.push(provider.profile_label);
    if (provider.device) {
      parts.push(provider.device + (typeof provider.device_index !== 'undefined' ? ':' + provider.device_index : ''));
    }
    if (provider.health && provider.health.device && parts.indexOf(provider.health.device) === -1) {
      parts.push(provider.health.device);
    }
    parts.push(healthText(provider.health));
    return name.toUpperCase() + ': ' + parts.filter(Boolean).join(' / ');
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function renderProviders(providers) {
    var target = element('providers');
    if (!target) return;
    providers = providers || {};
    target.classList.add('provider-status-grid');
    target.innerHTML = ['llm', 'stt', 'tts'].map(function (name) {
      var provider = providers[name] || {};
      var health = provider.health || {};
      var rows = [
        ['Provider', provider.provider || 'Not configured'],
        ['Model / profile', provider.model || provider.profile_label || provider.selected_profile_label || 'Default'],
        ['Runtime device', provider.device ? provider.device + ':' + (provider.device_index || 0) : (health.device || 'Not reported')],
        ['Health', health.ok ? 'Healthy' : (health.detail || 'Unavailable')]
      ];
      if (name === 'llm') rows.push(['Vision', provider.supports_vision || provider.local_supports_vision ? 'Available' : 'Not enabled']);
      if (name === 'tts' && provider.endpoint) rows.push(['Endpoint', provider.endpoint]);
      if (name === 'tts' && provider.restart_required) rows.push(['Pending change', 'Restart Shana to apply the saved voice selection']);
      return '<section class="provider-status-card"><h3>' + name.toUpperCase() + '</h3>'
        + rows.map(function (row) {
          return '<div class="provider-status-row"><span>' + escapeHtml(row[0]) + '</span><strong>'
            + escapeHtml(row[1]) + '</strong></div>';
        }).join('') + '</section>';
    }).join('');
  }

  function renderArtifacts(artifacts) {
    var target = element('ttsArtifactList');
    if (!target) return;
    artifacts = Array.isArray(artifacts) ? artifacts : [];
    target.innerHTML = artifacts.length ? artifacts.map(function (artifact) {
      var encodedName = encodeURIComponent(String(artifact.name || ''));
      return '<div class="tts-artifact-row"><button class="ghost artifact-name" onclick="ttsPlayerLoad(decodeURIComponent(\''
        + encodedName + '\'))">' + escapeHtml(artifact.name) + '</button><span>'
        + (artifact.modified_at ? new Date(artifact.modified_at).toLocaleString() : 'Time unavailable')
        + '</span><span>' + formatBytes(artifact.size_bytes) + '</span><button class="ghost danger-outline" onclick="ttsArtifactDelete(decodeURIComponent(\''
        + encodedName + '\'))">Delete</button></div>';
    }).join('') : '<div class="empty-state">No generated audio files were found in the configured TTS output directory.</div>';
  }

  function renderTwitch(data) {
    var twitch = data.twitch || {};
    var worker = twitch.worker || {};
    var eventsub = twitch.eventsub || {};
    var ready = twitch.stream_ready || {};
    var workerProcess = worker.process || {};
    var eventProcess = eventsub.process || {};
    setText('twitchWorkerStatus', worker.configured
      ? 'IRC worker: ' + (workerProcess.running ? 'running' : 'stopped') + '\nChannel: ' + (worker.channel || 'not set')
      : 'IRC worker is not configured. Missing: ' + ((worker.missing_config || []).join(', ') || 'configuration details unavailable'));
    setText('twitchEventSubStatus', eventsub.configured
      ? 'EventSub: ' + (eventProcess.running ? 'running' : 'stopped')
      : 'EventSub is not configured. Missing: ' + ((eventsub.missing_config || []).join(', ') || 'configuration details unavailable'));
    setText('streamReadyStatus', ready.ok ? 'Stream integrations report ready.' : (ready.detail || ready.mode || 'Stream readiness checks have not passed.'));
    setText('performerBusStatus', (data.performer || {}).ok ? 'Output bus is healthy.' : ((data.performer || {}).detail || 'Output bus is unavailable.'));
  }

  function formatProviders(providers) {
    providers = providers || {};
    var lines = [
      providerLine('llm', providers.llm),
      providerLine('stt', providers.stt),
      providerLine('tts', providers.tts)
    ];
    var tts = providers.tts || {};
    if (tts.endpoint) lines.push('TTS endpoint: ' + tts.endpoint);
    if (tts.selected_profile_label) lines.push('Selected voice: ' + tts.selected_profile_label);
    if (tts.restart_required) lines.push('Pending: restart Shana to apply the saved TTS selection.');
    return lines.join('\n');
  }

  function replaceSelectOptions(select, options, selectedValue) {
    if (!select) return;
    select.replaceChildren();
    options.forEach(function (entry) {
      var option = document.createElement('option');
      option.value = entry.value;
      option.textContent = entry.label;
      option.selected = entry.value === selectedValue;
      select.appendChild(option);
    });
    select.disabled = false;
  }

  function renderTtsControls(tts) {
    tts = tts || {};
    var selectedProvider = String(tts.selected_provider || tts.provider || '');
    var selectedProfile = String(tts.selected_profile || '');
    var providerOptions = (tts.available_providers || []).map(function (provider) {
      return { value: String(provider), label: String(provider) };
    });
    var profileOptions = [{ value: '', label: 'Default' }];
    (tts.available_profiles || []).forEach(function (profile) {
      if (String(profile.provider || '').toLowerCase() === selectedProvider.toLowerCase()) {
        profileOptions.push({
          value: String(profile.id || ''),
          label: String(profile.label || profile.id || 'Unnamed')
        });
      }
    });
    replaceSelectOptions(element('ttsProviderSelect'), providerOptions, selectedProvider);
    replaceSelectOptions(element('ttsProfileSelect'), profileOptions, selectedProfile);

    var qwenSelected = selectedProvider === 'qwen-tts';
    ['ttsStartButton', 'ttsStopButton'].forEach(function (id) {
      var button = element(id);
      if (!button) return;
      button.hidden = !qwenSelected;
      button.disabled = !qwenSelected;
    });
    var testButton = element('ttsTestButton');
    if (testButton) testButton.disabled = (tts.test_control || {}).enabled === false;
    ['llmTestButton', 'sttTestButton', 'voiceTestButton'].forEach(function (id) {
      var button = element(id);
      if (button) button.disabled = false;
    });
    setText('ttsControlNote', [
      'Running: ' + (tts.profile_label || tts.provider || 'n/a'),
      'Health: ' + healthText(tts.health),
      'Selected: ' + (tts.selected_profile_label || selectedProfile || selectedProvider || 'default'),
      tts.restart_required ? 'Restart Shana to apply this selection.' : 'Selection matches the running service.',
      (tts.test_control || {}).reason || ''
    ].filter(Boolean).join('\n'));

    var editor = tts.editor_profile || {};
    var values = editor.values || {};
    var editorShell = element('ttsProfileEditorShell');
    var statusShell = element('ttsProfileEditorStatusShell');
    if (editorShell) editorShell.style.display = '';
    if (statusShell) statusShell.style.display = '';
    setValue('ttsEditorProfileId', editor.id);
    setValue('ttsEditorLabel', editor.label);
    setValue('ttsEditorDescription', editor.description);
    setValue('ttsEditorValues', JSON.stringify(values, null, 2));
    setValue('ttsEditorQwenEndpoint', values.qwen_tts_endpoint);
    setValue('ttsEditorQwenReferenceAudio', values.qwen_tts_reference_audio);
    setValue('ttsEditorQwenReferenceText', values.qwen_tts_reference_text);
    setValue('ttsEditorQwenSpeaker', values.qwen_tts_speaker);
    setValue('ttsEditorQwenLanguage', values.qwen_tts_language);
    setValue('ttsEditorQwenInstruct', values.qwen_tts_instruct);
    setValue('ttsEditorQwenExtraJson', values.qwen_tts_extra_json ? JSON.stringify(values.qwen_tts_extra_json, null, 2) : '');
    setText('ttsEditorHelp', 'Editing ' + (editor.label || selectedProfile || 'the default profile')
      + ' for provider ' + (editor.provider || selectedProvider || 'n/a') + '.');
  }

  function formatGpu(gpu) {
    if (!gpu || !gpu.ok || !Array.isArray(gpu.gpus) || !gpu.gpus.length) {
      return gpu && gpu.detail ? gpu.detail : 'n/a';
    }
    return gpu.gpus.map(function (entry) {
      return (entry.label || 'GPU') + ' ' + (entry.name || '')
        + '\n' + Number(entry.utilization_percent || 0) + '% util'
        + ' / ' + Number(entry.memory_used_mb || 0) + ' MB'
        + (entry.memory_total_mb ? ' of ' + entry.memory_total_mb + ' MB' : '')
        + (entry.temperature_c ? ' / ' + entry.temperature_c + ' C' : '');
    }).join('\n\n');
  }

  function formatMemory(data) {
    var memory = data.memory_db || {};
    var stats = memory.stats || {};
    setText('memoryStats', [
      'Backend: ' + (stats.backend || 'n/a'),
      'Database: ' + (stats.database || 'n/a'),
      'Profile facts: ' + Number(stats.profile_count || 0),
      'Episodic memories: ' + Number(stats.episodic_count || 0),
      'Session memories: ' + Number(stats.session_scoped_episodic_count || 0),
      'Known people: ' + Number(stats.known_people_count || 0)
    ].join('\n'));
    if (typeof window.renderMemoryRecords === 'function') {
      window.renderMemoryRecords(memory);
    }
  }

  function renderAssistant(data) {
    var assistant = data.assistant || {};
    var settings = assistant.settings || {};
    var emotion = assistant.emotion_memory || {};
    var state = emotion.state || {};
    setText('assistantEmotion', [
      'Current emotion: ' + (state.current_emotion || 'neutral'),
      'Intensity: ' + Number(state.intensity || 0).toFixed(2),
      'Decay turns: ' + Number(state.decay_turns_remaining || 0),
      'Episodes: ' + (Array.isArray(emotion.episodes) ? emotion.episodes.length : 0),
      'Patterns: ' + (Array.isArray(emotion.patterns) ? emotion.patterns.length : 0)
    ].join('\n'));
    setValue('assistantSpeechFilterLevel', settings.speech_filter_level || 'strict');
    setChecked('assistantHardBlockEnabled', settings.speech_filter_hard_block_enabled);
    setChecked('assistantHeuristicEnabled', settings.speech_filter_heuristic_enabled);
    setChecked('assistantLlmEnabled', settings.speech_filter_llm_enabled);
    setChecked('assistantAutoRewrite', settings.speech_filter_auto_rewrite);
    setValue('assistantLlmModel', settings.speech_filter_llm_model);
    setValue('assistantLlmTemperature', settings.speech_filter_llm_temperature);
    setValue('assistantSafetyReviewTimeout', settings.stream_safety_review_timeout_seconds);
    setValue('assistantSafetyReviewTimeoutAction', settings.stream_safety_review_timeout_action || 'skip');
    setValue('assistantRouterProfile', settings.llm_router_profile || 'balanced');
    setChecked('assistantRouterHostedEscalation', settings.llm_router_allow_hosted_escalation);
    setChecked('assistantRouterPersonaHosted', settings.llm_router_persona_hosted_fallback_enabled);
    setChecked('assistantRouterPersonaHeavyHosted', settings.llm_router_persona_heavy_hosted_fallback_enabled);
    setValue('assistantRouterLightWords', settings.llm_router_chat_light_max_input_words);
    setValue('assistantRouterComplexWords', settings.llm_router_complex_max_input_words);
    setChecked('assistantStateEnabled', settings.assistant_state_enabled);
    setValue('assistantEmotionDecayTurns', settings.assistant_emotion_decay_turns);
    setValue('assistantEmotionEpisodeThreshold', settings.assistant_emotion_episode_threshold);
    setValue('assistantEmotionPatternThreshold', settings.assistant_emotion_pattern_threshold);
    setChecked('assistantProactiveIdleEnabled', settings.proactive_idle_enabled);
    setValue('assistantProactiveIdleMinSilence', settings.proactive_idle_min_silence_seconds);
    setValue('assistantProactiveIdleTargetSilence', settings.proactive_idle_target_silence_seconds);
    setValue('assistantProactiveIdleCooldown', settings.proactive_idle_cooldown_seconds);
    setValue('assistantProactiveIdleMaxAttempts', settings.proactive_idle_max_attempts_per_topic);
  }

  function updateChip(id, text, tone) {
    var chip = element(id);
    if (!chip) return;
    chip.textContent = text;
    chip.classList.remove('good', 'warn', 'bad');
    if (tone) chip.classList.add(tone);
  }

  function renderOverview(data) {
    var shana = data.shana || {};
    var process = shana.process || {};
    var health = shana.api_health || {};
    var providers = data.providers || {};
    var twitch = data.twitch || {};
    var workerRunning = !!(twitch.worker && twitch.worker.process && twitch.worker.process.running);
    var eventsubRunning = !!(twitch.eventsub && twitch.eventsub.process && twitch.eventsub.process.running);
    var memoryStats = (data.memory_db || {}).stats || {};
    var performer = data.performer || {};
    var current = performer.recent_event || {};
    var streamReady = twitch.stream_ready || {};
    setText('overviewLiveStatus', 'Voice ready');
    setText('overviewOutputStatus', current.type || 'idle');
    setText('overviewShanaStatus', process.running ? 'Running' : 'Stopped');
    setText('overviewStreamStatus', streamReady.mode || 'not configured');
    setText('overviewMemoryStatus', (Number(memoryStats.profile_count || 0) + Number(memoryStats.episodic_count || 0)) + ' items');
    setText('overviewProviderStatus', ['llm', 'stt', 'tts'].filter(function (name) {
      return providers[name] && providers[name].provider;
    }).length + '/3 configured');
    setText('overviewShanaMini', process.running ? 'ON' : 'OFF');
    setText('overviewApiMini', health.ok ? 'OK' : healthText(health));
    setText('overviewWorkerMini', Number(workerRunning) + Number(eventsubRunning) + ' active');
    setText('overviewTurnMini', current.type ? current.type + ' #' + (current.sequence || '?') : 'Idle');
    setText('overviewLiveMini', 'Ready');
    setText('overviewStreamMini', streamReady.ok ? 'Ready' : (streamReady.mode || 'Not ready'));
    setText('overviewTwitchMini', 'IRC ' + (workerRunning ? 'on' : 'off') + ' / EventSub ' + (eventsubRunning ? 'on' : 'off'));
    setText('overviewMemoryMini', Number(memoryStats.known_people_count || 0) + ' people');
    var warnings = [];
    if (!health.ok) warnings.push('API unavailable');
    if (!((providers.tts || {}).health || {}).ok) warnings.push('TTS unavailable');
    if (!process.running) warnings.push('Shana stopped');
    setText('overviewWarningsMini', warnings.length ? warnings.join(' / ') : 'No current warnings');
  }

  function renderStatus(data) {
    latestStatus = data;
    window.gammaDashboardStatus = data;
    var shana = data.shana || {};
    var process = shana.process || {};
    var health = shana.api_health || {};
    var systemProbe = shana.system_status || {};
    var machine = data.machine || {};
    var logs = shana.logs || {};
    var twitch = data.twitch || {};
    var workerRunning = !!(twitch.worker && twitch.worker.process && twitch.worker.process.running);
    var eventsubRunning = !!(twitch.eventsub && twitch.eventsub.process && twitch.eventsub.process.running);

    setText('running', process.running ? 'Yes' : 'No');
    setText('pid', process.pid || 'n/a');
    setText('procCpu', process.running ? formatPercent(process.cpu_percent) : 'n/a');
    setText('procMem', process.running ? formatBytes(process.rss_bytes) : 'n/a');
    setText('hostCpu', formatPercent(machine.cpu_percent));
    setText('hostRam', machine.memory ? formatPercent(machine.memory.percent) : 'n/a');
    setText('hostDisk', machine.disk ? formatPercent(machine.disk.percent) : 'n/a');
    setText('hostGpu', formatGpu(machine.gpu));
    setText('machineMeta', [
      'Last sample: ' + (machine.sampled_at || 'n/a'),
      'Refresh interval: ' + Number(machine.refresh_interval_seconds || 0) + ' sec',
      'GPU polling: ' + (machine.gpu_enabled ? 'enabled' : 'disabled')
    ].join('\n'));
    setText('backendHealth', [
      'Dashboard: healthy at ' + ((data.dashboard || {}).url || location.origin),
      'Shana process: ' + (process.running ? 'running' : 'stopped') + (process.pid ? ' (PID ' + process.pid + ')' : ''),
      'Shana API: ' + healthText(health),
      'System status probe: ' + healthText(systemProbe),
      'Output bus: ' + ((data.performer || {}).ok ? 'healthy' : ((data.performer || {}).detail || 'unavailable')),
      'Twitch IRC: ' + (workerRunning ? 'running' : 'stopped'),
      'Twitch EventSub: ' + (eventsubRunning ? 'running' : 'stopped')
    ].join('\n'));
    renderProviders(data.providers);
    renderArtifacts(data.recent_artifacts);
    renderTtsControls((data.providers || {}).tts);
    setText('providerActions', (data.provider_actions && data.provider_actions.detail) || 'No provider action has been run yet.');
    setText('recentTimings', 'Samples: ' + Number(((data.timings || {}).summary || {}).count || 0));
    setText('stdoutLog', logs.stdout_tail || 'No stdout output.');
    setText('stderrLog', logs.stderr_tail || 'No stderr output.');
    setText('navbarDashboardStatus', 'healthy');
    setText('navbarApiStatus', healthText(health));
    setText('navbarWorkerStatus', (Number(workerRunning) + Number(eventsubRunning)) + ' active');
    setText('outputViewApiStatus', (data.performer || {}).ok ? 'healthy' : 'unavailable');
    setText('stamp', 'Last refreshed: ' + new Date().toLocaleString());
    updateChip('stickyShanaStatus', 'Shana: ' + (process.running ? 'running' : 'stopped'), process.running ? 'good' : 'bad');
    updateChip('stickyBackendStatus', 'API: ' + (health.ok ? 'healthy' : 'down'), health.ok ? 'good' : 'bad');
    updateChip('stickyTwitchStatus', 'Twitch: ' + (workerRunning || eventsubRunning ? 'active' : 'stopped'), workerRunning || eventsubRunning ? 'good' : 'warn');
    formatMemory(data);
    renderAssistant(data);
    renderOverview(data);
    renderTwitch(data);
  }

  async function loadStatus() {
    setText('stamp', 'Loading...');
    try {
      var response = await fetch('/api/status?_=' + Date.now(), { cache: 'no-store' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      renderStatus(await response.json());
    } catch (error) {
      setText('stamp', 'Load failed');
      setText('backendHealth', 'Dashboard status request failed.\n' + String(error));
      updateChip('stickyBackendStatus', 'API: unavailable', 'bad');
    }
  }

  async function action(path, options) {
    options = options || {};
    if (options.confirmMessage && !window.confirm(options.confirmMessage)) return;
    try {
      var fetchOptions = { method: 'POST' };
      if (typeof options.body !== 'undefined') {
        fetchOptions.headers = { 'Content-Type': 'application/json' };
        fetchOptions.body = JSON.stringify(options.body);
      }
      var response = await fetch(path, fetchOptions);
      var payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || ('HTTP ' + response.status));
      setText('providerActions', payload.detail || 'Action completed.');
      if (options.redirectUrl) window.location.href = options.redirectUrl;
      else window.setTimeout(loadStatus, 350);
      return payload;
    } catch (error) {
      setText('providerActions', 'Action failed: ' + String(error));
      throw error;
    }
  }

  async function saveTtsSelection(path, payload) {
    setText('ttsControlNote', 'Saving selection...');
    try {
      var response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      var result = await response.json();
      if (!response.ok) throw new Error(result.detail || ('HTTP ' + response.status));
      await loadStatus();
    } catch (error) {
      setText('ttsControlNote', 'Selection failed: ' + String(error));
      if (latestStatus) renderTtsControls((latestStatus.providers || {}).tts);
    }
  }

  function selectTtsProvider() {
    var select = element('ttsProviderSelect');
    if (select) return saveTtsSelection('/api/providers/tts/select', { provider: select.value });
  }

  function selectTtsProfile() {
    var select = element('ttsProfileSelect');
    if (select) return saveTtsSelection('/api/providers/tts/profile', { profile: select.value });
  }

  window.loadStatus = loadStatus;
  window.action = action;
  window.selectTtsProvider = selectTtsProvider;
  window.selectTtsProfile = selectTtsProfile;
  window.gammaRenderStatus = renderStatus;

  loadStatus();
  pollTimer = window.setInterval(loadStatus, 10000);
  window.addEventListener('beforeunload', function () {
    if (pollTimer) window.clearInterval(pollTimer);
  });
})();
