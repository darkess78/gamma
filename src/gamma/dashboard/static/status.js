// status.js - loads the status payload for the current page and renders it.
// Each page polls one bounded /api/status/* endpoint; the top bar polls the cheap header.
(function () {
  'use strict';

  var PAGE_ENDPOINTS = {
    dashboard: '/api/status/summary',
    shana: '/api/status/settings',
    people: '/api/status/memory',
    integrations: '/api/status/summary',
    system: '/api/status/diagnostics'
  };
  var page = String(window.GAMMA_DASHBOARD_PAGE || 'dashboard');
  var latestStatus = null;

  function element(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    var target = element(id);
    if (target) target.textContent = value == null ? 'n/a' : String(value);
  }

  // Forms marked data-settings-form stop taking server values once the owner edits them,
  // so the 10 second poll never wipes unsaved changes. Saving clears the mark.
  function editable(target) {
    return target && document.activeElement !== target && !target.closest('[data-settings-form][data-dirty]');
  }

  function setValue(id, value) {
    var target = element(id);
    if (editable(target)) target.value = value == null ? '' : String(value);
  }

  function setChecked(id, value) {
    var target = element(id);
    if (editable(target)) target.checked = !!value;
  }

  function markDirty(event) {
    var form = event.target && event.target.closest ? event.target.closest('[data-settings-form]') : null;
    if (form) form.setAttribute('data-dirty', '');
  }

  function clearSettingsDirty(id) {
    var target = element(id);
    if (target) target.removeAttribute('data-dirty');
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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
    return isFinite(number) ? number.toFixed(0) + '%' : 'n/a';
  }

  function healthText(health) {
    if (!health) return 'unknown';
    if (health.ok) return 'healthy';
    return health.detail || 'unavailable';
  }

  function modeLabel(mode) {
    var labels = { sleep: 'Asleep', wake: 'Awake', go_live: 'Live', break: 'On break' };
    return labels[String(mode || 'sleep')] || String(mode || 'unknown').replace(/_/g, ' ');
  }

  // A health row: tone is good / warn / bad / off.
  function healthItem(label, value, tone) {
    return '<li><span class="dot ' + escapeHtml(tone || 'off') + '" aria-hidden="true"></span>'
      + '<span class="health-label">' + escapeHtml(label) + '</span>'
      + '<span class="health-value">' + escapeHtml(value) + '</span></li>';
  }

  function setHtml(id, html) {
    var target = element(id);
    if (target) target.innerHTML = html;
  }

  function providerSummary(provider) {
    provider = provider || {};
    var parts = [provider.provider || 'not configured'];
    var profile = provider.profile_label || provider.selected_profile_label;
    if (profile) parts.push(profile);  // a voice profile names the voice better than a model id
    else if (provider.model) parts.push(provider.model);
    return parts.join(' / ');
  }

  function providerTone(provider) {
    var health = (provider || {}).health;
    if (!health) return provider && provider.provider ? 'warn' : 'off';
    return health.ok ? 'good' : 'bad';
  }

  function processState(service) {
    service = service || {};
    var running = !!((service.process || {}).running);
    if (running) return { text: 'running', tone: 'good' };
    if (service.configured === false) {
      var missing = (service.missing_config || []).join(', ');
      return { text: 'not configured' + (missing ? ' (missing ' + missing + ')' : ''), tone: 'off' };
    }
    if (service.enabled === false) return { text: 'disabled', tone: 'off' };
    return { text: 'stopped', tone: 'warn' };
  }

  function gpuHtml(gpu) {
    if (!gpu || !gpu.ok || !Array.isArray(gpu.gpus) || !gpu.gpus.length) {
      return '<p class="muted small">GPU: ' + escapeHtml(gpu && gpu.detail ? gpu.detail : 'not reported') + '</p>';
    }
    return gpu.gpus.map(function (entry) {
      var used = Number(entry.memory_used_mb || 0);
      var total = Number(entry.memory_total_mb || 0);
      var percent = total ? Math.min(100, Math.round(used / total * 100)) : 0;
      return '<div class="gpu"><div class="gpu-head"><strong>' + escapeHtml((entry.label || 'GPU') + ' ' + (entry.name || ''))
        + '</strong><span>' + escapeHtml(Number(entry.utilization_percent || 0) + '% busy'
        + (entry.temperature_c ? ' / ' + entry.temperature_c + ' C' : '')) + '</span></div>'
        + '<div class="meter" role="img" aria-label="' + escapeHtml(percent + '% memory used') + '"><span style="width:' + percent + '%"></span></div>'
        + '<div class="muted small">' + escapeHtml(used + ' of ' + (total || '?') + ' MB') + '</div></div>';
    }).join('');
  }

  // ---------------------------------------------------------------- top bar
  function renderChip(data) {
    var shana = data.shana || {};
    var process = shana.process || {};
    var health = shana.api_health || {};
    var mode = ((data.presence || {}).state || {}).mode;
    var chip = element('stickyShanaStatus');
    if (!chip) return;
    var text;
    var tone;
    if (process.running && health.ok) {
      text = 'Shana: ' + modeLabel(mode || 'sleep');
      tone = mode === 'go_live' ? 'live' : 'good';
    } else if (process.running) {
      text = 'Shana: API down';
      tone = 'bad';
    } else if (health.ok) {
      text = 'Shana: ' + modeLabel(mode || 'sleep');
      tone = 'warn';
    } else {
      text = 'Shana: stopped';
      tone = 'bad';
    }
    chip.textContent = text;
    chip.className = 'status-chip ' + tone;
  }

  // ---------------------------------------------------------------- home
  function renderHome(data) {
    var shana = data.shana || {};
    var providers = data.providers || {};
    var twitch = data.twitch || {};
    var discord = (data.discord || {}).text_worker || {};
    var health = shana.api_health || {};
    var items = [
      healthItem('Shana', (shana.process || {}).running ? 'running, API ' + healthText(health) : 'process stopped',
        (shana.process || {}).running && health.ok ? 'good' : 'bad'),
      healthItem('Brain', providerSummary(providers.llm), providerTone(providers.llm)),
      healthItem('Hearing', providerSummary(providers.stt), providerTone(providers.stt)),
      healthItem('Voice', providerSummary(providers.tts), providerTone(providers.tts))
    ];
    var irc = processState(twitch.worker);
    items.push(healthItem('Twitch chat', irc.text, irc.tone));
    if (discord.summary || discord.process) {
      var discordRunning = !!((discord.process || {}).running);
      items.push(healthItem('Discord', (discord.summary || {}).label || (discordRunning ? 'running' : 'stopped'),
        (discord.summary || {}).connected ? 'good' : (discordRunning ? 'warn' : 'off')));
    }
    setHtml('homeHealth', items.join(''));
    setHtml('homeGpu', gpuHtml((data.machine || {}).gpu));
    var mood = ((data.assistant || {}).emotion) || {};
    if (element('homeMood')) {
      var intensity = Number(mood.intensity || 0);
      setText('homeMood', (mood.current_emotion || 'neutral') + (intensity ? ' (' + intensity.toFixed(2) + ')' : ''));
    }
  }

  // ---------------------------------------------------------------- shana
  function providerCards(providers, targetId) {
    var target = element(targetId);
    if (!target) return;
    providers = providers || {};
    var titles = { llm: 'Brain (LLM)', stt: 'Hearing (speech recognition)', tts: 'Voice (TTS)' };
    target.innerHTML = ['llm', 'stt', 'tts'].map(function (name) {
      var provider = providers[name] || {};
      var health = provider.health || {};
      var rows = [
        ['Provider', provider.provider || 'Not configured'],
        ['Model / profile', provider.model || provider.profile_label || provider.selected_profile_label || 'Default'],
        ['Device', provider.device ? provider.device + ':' + (provider.device_index || 0) : (health.device || 'Not reported')],
        ['Health', health.ok ? 'Healthy' : (health.detail || 'Unavailable')]
      ];
      if (name === 'llm' && provider.router_profile) rows.push(['Router', provider.router_profile]);
      if (name === 'tts' && provider.endpoint) rows.push(['Endpoint', provider.endpoint]);
      return '<section class="provider-card"><h3><span class="dot ' + providerTone(provider) + '" aria-hidden="true"></span>'
        + escapeHtml(titles[name]) + '</h3><dl>'
        + rows.map(function (row) {
          return '<div><dt>' + escapeHtml(row[0]) + '</dt><dd>' + escapeHtml(row[1]) + '</dd></div>';
        }).join('') + '</dl></section>';
    }).join('');
  }

  function replaceSelectOptions(select, options, selectedValue) {
    if (!select || document.activeElement === select) return;
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
        profileOptions.push({ value: String(profile.id || ''), label: String(profile.label || profile.id || 'Unnamed') });
      }
    });
    replaceSelectOptions(element('ttsProviderSelect'), providerOptions, selectedProvider);
    replaceSelectOptions(element('ttsProfileSelect'), profileOptions, selectedProfile);

    var qwenSelected = selectedProvider === 'qwen-tts';
    ['ttsStartButton', 'ttsStopButton'].forEach(function (id) {
      var button = element(id);
      if (button) button.hidden = !qwenSelected;
    });
    var testButton = element('ttsTestButton');
    var testControl = tts.test_control || {};
    if (testButton) testButton.disabled = testControl.enabled === false;
    setText('ttsControlNote', [
      'Speaking with ' + (tts.profile_label || tts.provider || 'nothing') + ' (' + healthText(tts.health) + ').',
      tts.restart_required ? 'Restart Shana on the System page to switch to ' + (tts.selected_profile_label || selectedProfile || selectedProvider) + '.' : '',
      testControl.reason || ''
    ].filter(Boolean).join(' '));

    var editor = tts.editor_profile || {};
    setValue('ttsEditorProfileId', editor.id);
    setValue('ttsEditorLabel', editor.label);
    setValue('ttsEditorDescription', editor.description);
    setValue('ttsEditorValues', JSON.stringify(editor.values || {}, null, 2));
    setText('ttsEditorHelp', 'Editing ' + (editor.label || selectedProfile || 'the default profile')
      + ' for ' + (editor.provider || selectedProvider || 'the selected engine') + '.');
  }

  function renderArtifacts(artifacts) {
    var target = element('ttsArtifactList');
    if (!target) return;
    artifacts = Array.isArray(artifacts) ? artifacts : [];
    target.innerHTML = artifacts.length ? artifacts.map(function (artifact) {
      var name = escapeHtml(artifact.name || '');
      return '<div class="artifact-row"><button class="link-button" type="button" data-artifact-play="' + name + '">' + name + '</button>'
        + '<span class="muted small">' + escapeHtml(artifact.modified_at ? new Date(artifact.modified_at).toLocaleString() : '')
        + ' / ' + escapeHtml(formatBytes(artifact.size_bytes)) + '</span>'
        + '<button class="ghost danger-outline small" type="button" data-artifact-delete="' + name + '">Delete</button></div>';
    }).join('') : '<p class="muted">No generated audio files yet.</p>';
  }

  function renderFloorEvents(events) {
    var target = element('assistantFloorEvents');
    if (!target) return;
    setText('assistantFloorCount', events.length ? String(events.length) + (events.length >= 20 ? '+' : '') : '0');
    target.innerHTML = events.length ? events.map(function (event) {
      var when = event.at ? new Date(event.at).toLocaleString() : '';
      return '<div class="artifact-row"><div><div>' + escapeHtml(event.text || '') + '</div>'
        + '<span class="muted small">' + escapeHtml(when) + ' / ' + escapeHtml(event.source || 'speech')
        + ' / ' + escapeHtml((event.layers || []).join(', ')) + ': ' + escapeHtml((event.rules || []).join(', '))
        + '</span></div></div>';
    }).join('') : '<p class="muted">Nothing stopped yet.</p>';
  }

  function renderAssistant(data) {
    var assistant = data.assistant || {};
    var settings = assistant.settings || {};
    var state = (assistant.emotion_memory || {}).state || {};
    if (element('assistantEmotion')) {
      setText('assistantEmotion', 'Current mood: ' + (state.current_emotion || 'neutral')
        + ' (intensity ' + Number(state.intensity || 0).toFixed(2) + ').');
    }
    if (!assistant.settings) return;
    setValue('assistantSpeechFilterLevel', settings.speech_filter_level || 'strict');
    setChecked('assistantWordListEnabled', settings.speech_filter_word_list_enabled);
    setChecked('assistantExtraSafetyEnabled', settings.speech_filter_extra_enabled);
    var floorOn = settings.speech_filter_hard_block_enabled !== false;
    var reviewerOn = !!settings.speech_filter_llm_enabled;
    setText('assistantFloorStatus', floorOn
      ? 'Always on. Slurs, threats, sexual content, self-harm, dangerous help and private info stop the whole reply'
        + (reviewerOn ? ', checked by word lists and the reviewer model.' : '. The reviewer model is off in the config file, so only the word lists and patterns check it.')
      : 'OFF in the config file. Nothing stops severe speech. Set speech_filter_hard_block_enabled = true.');
    renderFloorEvents(assistant.floor_events || []);
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

  // ---------------------------------------------------------------- people
  function renderMemory(data) {
    var memory = data.memory_db || {};
    var stats = memory.stats || {};
    setText('memoryStats', Number(stats.profile_count || 0) + ' profile facts, '
      + Number(stats.episodic_count || 0) + ' episodic memories, '
      + Number(stats.known_people_count || 0) + ' known people.');
    if (typeof window.renderMemoryRecords === 'function') window.renderMemoryRecords(memory);
  }

  // ---------------------------------------------------------------- integrations
  var OUTPUT_TARGETS = [
    ['dashboard_monitor', 'Talk / monitor'],
    ['stream_public', 'Stream (performer, subtitles)'],
    ['discord_call', 'Discord call']
  ];

  function renderIntegrations(data) {
    var twitch = data.twitch || {};
    var irc = processState(twitch.worker);
    var eventsub = processState(twitch.eventsub);
    var worker = twitch.worker || {};
    var items = [
      healthItem('Chat worker', irc.text + (worker.channel ? ' / #' + worker.channel : ''), irc.tone),
      healthItem('EventSub', eventsub.text, eventsub.tone)
    ];
    setHtml('twitchStatusList', items.join(''));
    renderTwitchSettings(worker.controls);
    var ready = twitch.stream_ready || {};
    if (element('streamReadyStatus')) {
      var readyText = ready.ok ? 'Stream integrations report ready.' : (ready.detail || 'Stream readiness checks have not passed.');
      if (ready.mode) readyText = 'Mode: ' + ready.mode + '. ' + readyText;
      if (ready.blocker_count) readyText += ' Blockers: ' + ready.blocker_count + '.';
      setText('streamReadyStatus', readyText);
    }
    var performer = data.performer || {};
    var stats = performer.stats || {};
    var muted = Array.isArray(stats.muted_targets) ? stats.muted_targets : [];
    var subscribers = stats.subscribers_by_target || {};
    var target = element('outputTargetControls');
    if (target) {
      if (performer.ok === false && !performer.stats) {
        target.innerHTML = '<p class="muted">Output bus unavailable: ' + escapeHtml(performer.detail || 'no detail') + '</p>';
      } else {
        target.innerHTML = OUTPUT_TARGETS.map(function (entry) {
          var isMuted = muted.indexOf(entry[0]) !== -1;
          var count = Number(subscribers[entry[0]] || 0);
          return '<div class="target-row"><span class="dot ' + (isMuted ? 'warn' : 'good') + '" aria-hidden="true"></span>'
            + '<span class="health-label">' + escapeHtml(entry[1]) + '</span>'
            + '<span class="health-value">' + (isMuted ? 'muted' : 'on') + ', ' + count + ' viewer' + (count === 1 ? '' : 's') + '</span>'
            + '<button class="ghost small" type="button" data-output-target="' + entry[0] + '" data-output-action="' + (isMuted ? 'unmute' : 'mute') + '">'
            + (isMuted ? 'Unmute' : 'Mute') + '</button></div>';
        }).join('');
      }
    }
  }

  function renderTwitchSettings(controls) {
    if (!controls) return;
    setChecked('twitchDryRun', controls.dry_run);
    setChecked('twitchVoiceEnabled', controls.voice_enabled);
    setChecked('twitchSubtitlesEnabled', controls.subtitles_enabled);
    setChecked('twitchLlmSafetyReviewEnabled', controls.llm_safety_review_enabled);
    setChecked('twitchMentionRepliesEnabled', controls.mention_replies_enabled);
    setChecked('twitchAmbientChatEnabled', controls.ambient_chat_enabled);
    setChecked('twitchSpamQuipsEnabled', controls.spam_quips_enabled);
    setChecked('twitchSelfGoalProposalsEnabled', controls.self_goal_proposals_enabled);
    setValue('twitchMinSpeechGapSeconds', controls.min_speech_gap_seconds);
    setValue('twitchMaxSpeechSecondsPerMinute', controls.max_speech_seconds_per_minute);
    setValue('twitchSpamQuipCooldownSeconds', controls.spam_quip_cooldown_seconds);
  }

  // ---------------------------------------------------------------- system
  function renderSystem(data) {
    var shana = data.shana || {};
    var process = shana.process || {};
    var health = shana.api_health || {};
    var machine = data.machine || {};
    var logs = shana.logs || {};
    var twitch = data.twitch || {};
    var performer = data.performer || {};
    setText('running', process.running ? 'Yes' : 'No');
    setText('pid', process.pid || 'n/a');
    setText('procCpu', process.running ? formatPercent(process.cpu_percent) : 'n/a');
    setText('procMem', process.running ? formatBytes(process.rss_bytes) : 'n/a');
    setText('hostCpu', formatPercent(machine.cpu_percent));
    setText('hostRam', machine.memory ? formatPercent(machine.memory.percent) : 'n/a');
    setText('hostDisk', machine.disk ? formatPercent(machine.disk.percent) : 'n/a');
    setHtml('hostGpu', gpuHtml(machine.gpu));
    setText('machineMeta', 'Sampled ' + (machine.sampled_at ? new Date(machine.sampled_at).toLocaleTimeString() : 'n/a')
      + ', every ' + Number(machine.refresh_interval_seconds || 0) + ' sec. GPU polling '
      + (machine.gpu_enabled ? 'on' : 'off') + '.');
    var irc = processState(twitch.worker);
    var eventsub = processState(twitch.eventsub);
    var systemProbe = shana.system_status || {};
    setHtml('backendHealth', [
      healthItem('Dashboard', 'running at ' + ((data.dashboard || {}).url || location.origin), 'good'),
      healthItem('Shana process', process.running ? 'running' + (process.pid ? ' (PID ' + process.pid + ')' : '') : 'stopped', process.running ? 'good' : 'bad'),
      healthItem('Shana API', healthText(health), health.ok ? 'good' : 'bad'),
      healthItem('System status probe', healthText(systemProbe), systemProbe.ok ? 'good' : 'bad'),
      healthItem('Output bus', performer.ok ? 'healthy' : (performer.detail || 'unavailable'), performer.ok ? 'good' : 'bad'),
      healthItem('Twitch chat worker', irc.text, irc.tone),
      healthItem('Twitch EventSub', eventsub.text, eventsub.tone)
    ].join(''));
    providerCards(data.providers, 'systemProviders');
    var timing = (data.timings || {}).summary || {};
    setText('recentTimings', timing.count
      ? 'Last ' + timing.count + ' turns: average ' + timing.avg_total_ms + ' ms, fastest ' + timing.min_total_ms + ' ms, slowest ' + timing.max_total_ms + ' ms.'
      : 'No conversation timings recorded yet.');
    setText('stdoutLog', logs.stdout_tail || 'No stdout output.');
    setText('stderrLog', logs.stderr_tail || 'No stderr output.');
    renderPresenceInternals((data.presence || {}).state || {});
    renderStartupAdmission(data);
    renderSidecarAllocations(data);
    var rawFold = element('rawStatusFold');
    if (rawFold && rawFold.open) setText('rawStatus', JSON.stringify(data, null, 2));
  }

  function onOff(value) {
    return value ? 'on' : 'off';
  }

  function renderPresenceInternals(state) {
    if (!element('presenceInternals')) return;
    var outputs = state.outputs || {};
    var inputs = state.inputs || {};
    var autonomy = state.autonomy || {};
    var scheduler = autonomy.scheduler || {};
    var safety = state.safety || {};
    var wake = state.wake || {};
    setText('presenceInternals', [
      'Mode: ' + (state.mode || 'n/a') + ' (desired ' + (state.desired_mode || state.mode || 'n/a') + '), updated ' + (state.updated_at || 'n/a'),
      'Outputs: monitor ' + onOff(outputs.dashboard_monitor) + ', stream ' + onOff(outputs.stream_public)
        + ', voice ' + onOff(outputs.voice) + ', subtitles ' + onOff(outputs.subtitles) + ', local mic ' + onOff(inputs.local_mic),
      'Autonomy: ambient ' + onOff(autonomy.ambient_chat_enabled) + ', proactive ' + onOff(autonomy.proactive_idle_enabled),
      'Safety: LLM review ' + onOff(safety.llm_safety_review_enabled) + ', dry run ' + onOff(safety.dry_run),
      'Scheduler: ' + (scheduler.status || 'idle') + ' (' + (scheduler.reason || 'not checked') + '), last check '
        + (scheduler.last_checked_at || 'never') + ', next ' + (scheduler.next_check_at || 'not scheduled')
        + ', attempts for topic ' + (scheduler.attempts_for_topic || 0),
      'Wake opening: ' + onOff(wake.enabled !== false) + ', last result ' + (wake.last_status || 'none')
        + (wake.suppression_reason ? ' (' + wake.suppression_reason + ')' : '') + ', last wake ' + (wake.last_event_at || 'never')
    ].join('\n'));
  }

  function renderStartupAdmission(data) {
    var target = element('startupAdmission');
    if (!target) return;
    var admission = data.startup_admission || {};
    var entries = Array.isArray(admission.entries) ? admission.entries.slice().reverse() : [];
    var summary = admission.summary || {};
    if (!entries.length) {
      target.textContent = 'No startup admission decisions have been recorded.';
      return;
    }
    var lines = ['Selected ' + Number(summary.selected_count || 0) + ', rejected ' + Number(summary.rejected_count || 0)
      + ', skipped ' + Number(summary.skipped_count || 0) + ', bypassed ' + Number(summary.bypassed_count || 0) + '.', ''];
    entries.forEach(function (entry) {
      var eventName = String(entry.event || '').replace('resource.startup_admission.', '') || 'unknown';
      var selected = entry.target_id
        ? entry.target_id + ' (' + (entry.endpoint_ref || entry.device || entry.kind || 'target') + ')'
        : (entry.requested_device ? 'explicit device ' + entry.requested_device : 'no target');
      lines.push(eventName + ': ' + (entry.kind || 'sidecar') + ' ' + (entry.provider || '') + ' ' + (entry.model || '') + ' -> ' + selected);
      var detail = [];
      if (entry.estimated_vram_mb != null) detail.push('estimate ' + entry.estimated_vram_mb + ' MB' + (entry.estimate_source ? ' (' + entry.estimate_source + ')' : ''));
      if (entry.free_vram_mb != null) detail.push(entry.free_vram_mb + ' MB free, ' + Number(entry.projected_headroom_mb || 0) + ' MB projected');
      if (entry.reason || entry.status) detail.push('reason: ' + (entry.reason || entry.status));
      if (entry.rejected_count) {
        detail.push('rejected: ' + Object.keys(entry.rejected || {}).map(function (id) { return id + '=' + entry.rejected[id]; }).join(', '));
      }
      if (Array.isArray(entry.validation_errors) && entry.validation_errors.length) detail.push('validation: ' + entry.validation_errors.join(' / '));
      if (detail.length) lines.push('  ' + detail.join('; '));
    });
    target.textContent = lines.join('\n');
  }

  function renderSidecarAllocations(data) {
    var target = element('sidecarAllocations');
    if (!target) return;
    var allocations = data.sidecar_allocations || {};
    var entries = Array.isArray(allocations.entries) ? allocations.entries.slice().reverse() : [];
    var summary = allocations.summary || {};
    if (!entries.length) {
      target.textContent = 'No sidecar allocation observations have been recorded.';
      return;
    }
    var lines = ['Fresh ' + Number(summary.fresh_count || 0) + ', stale ' + Number(summary.stale_count || 0)
      + '. Observed ' + Number(summary.observed_vram_mb || 0) + ' MB vs estimated ' + Number(summary.estimated_vram_mb || 0) + ' MB.', ''];
    entries.forEach(function (entry) {
      var gpus = Array.isArray(entry.gpu_allocations) && entry.gpu_allocations.length
        ? entry.gpu_allocations.map(function (item) {
          return 'GPU ' + (item.gpu_index == null ? '?' : item.gpu_index) + ' ' + Number(item.used_memory_mb || 0) + ' MB';
        }).join(', ')
        : 'no GPU process match';
      lines.push((entry.kind || 'sidecar') + ' ' + (entry.provider || '') + ': ' + (entry.stale ? 'stale' : 'fresh')
        + ', PID ' + (entry.pid || 'n/a') + ', observed ' + Number(entry.observed_vram_mb || 0) + ' MB, estimated '
        + Number(entry.estimated_vram_mb || 0) + ' MB, ' + gpus);
    });
    target.textContent = lines.join('\n');
  }

  // ---------------------------------------------------------------- loading
  function renderStatus(data) {
    latestStatus = data;
    window.gammaDashboardStatus = data;
    renderChip(data);
    if (page === 'dashboard') renderHome(data);
    if (page === 'shana') {
      providerCards(data.providers, 'providers');
      renderTtsControls((data.providers || {}).tts);
      renderArtifacts(data.recent_artifacts);
      renderAssistant(data);
    }
    if (page === 'people') renderMemory(data);
    if (page === 'integrations') renderIntegrations(data);
    if (page === 'system') renderSystem(data);
    setText('stamp', 'Updated ' + new Date().toLocaleTimeString());
  }

  async function fetchJson(path) {
    var response = await fetch(path + (path.indexOf('?') === -1 ? '?' : '&') + '_=' + Date.now(), { cache: 'no-store' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }

  async function loadStatus() {
    var endpoint = PAGE_ENDPOINTS[page];
    if (!endpoint) return loadHeader();
    try {
      renderStatus(await fetchJson(endpoint));
    } catch (error) {
      setText('stamp', 'Status request failed: ' + String(error));
      var chip = element('stickyShanaStatus');
      if (chip) {
        chip.textContent = 'Dashboard: unreachable';
        chip.className = 'status-chip bad';
      }
    }
  }

  async function loadHeader() {
    try {
      renderChip(await fetchJson('/api/status/header'));
    } catch (_error) {
      var chip = element('stickyShanaStatus');
      if (chip) {
        chip.textContent = 'Status unknown';
        chip.className = 'status-chip warn';
      }
    }
  }

  async function action(path, options) {
    options = options || {};
    if (options.confirmMessage && !window.confirm(options.confirmMessage)) return;
    var notify = window.showActionResult || function () {};
    notify('Working...', '');
    try {
      var fetchOptions = { method: 'POST' };
      if (typeof options.body !== 'undefined') {
        fetchOptions.headers = { 'Content-Type': 'application/json' };
        fetchOptions.body = JSON.stringify(options.body);
      }
      var response = await fetch(path, fetchOptions);
      var payload = await response.json();
      if (!response.ok || payload.ok === false) throw new Error(payload.detail || ('HTTP ' + response.status));
      notify(payload.detail || 'Done.', 'good');
      window.setTimeout(loadStatus, 350);
      return payload;
    } catch (error) {
      notify('Failed: ' + String(error.message || error), 'bad');
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
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
      clearSettingsDirty('ttsEditorForm');
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

  document.addEventListener('input', markDirty);
  document.addEventListener('change', markDirty);

  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('button') : null;
    if (!button) return;
    if (button.dataset.outputTarget) {
      action('/api/performer/targets/' + encodeURIComponent(button.dataset.outputTarget) + '/' + button.dataset.outputAction)
        .catch(function () {});
    } else if (button.dataset.artifactPlay && window.ttsPlayerLoad) {
      window.ttsPlayerLoad(button.dataset.artifactPlay);
    } else if (button.dataset.artifactDelete && window.ttsArtifactDelete) {
      window.ttsArtifactDelete(button.dataset.artifactDelete);
    }
  });

  var rawFold = element('rawStatusFold');
  if (rawFold) {
    rawFold.addEventListener('toggle', function () {
      if (rawFold.open && latestStatus) setText('rawStatus', JSON.stringify(latestStatus, null, 2));
    });
  }

  window.loadStatus = loadStatus;
  window.action = action;
  window.selectTtsProvider = selectTtsProvider;
  window.selectTtsProfile = selectTtsProfile;
  window.gammaRenderStatus = renderStatus;
  window.clearSettingsDirty = clearSettingsDirty;
  window.renderTwitchSettings = renderTwitchSettings;

  loadStatus();
  var pollTimer = window.setInterval(function () {
    if (!document.hidden) loadStatus();
  }, 10000);
  window.addEventListener('beforeunload', function () {
    window.clearInterval(pollTimer);
  });
})();
