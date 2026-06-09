// render.js - Rendering and panel update functions for Gamma dashboard
(function () {
  function renderRuntimePanels(data) {
    var process = data.shana && data.shana.process ? data.shana.process : {};
    var health = data.shana && data.shana.api_health ? data.shana.api_health : {};
    var machine = data.machine || {};
    var gpu = machine.gpu || {};

    setTextIfChanged('running', process.running ? 'Yes' : 'No');
    setTextIfChanged('pid', process.pid || 'n/a');
    setTextIfChanged('procCpu', process.running ? ((process.cpu_percent || 0).toFixed(1) + '%') : 'n/a');
    setTextIfChanged('procMem', process.running ? fmtBytes(process.rss_bytes) : 'n/a');
    setTextIfChanged('hostCpu', ((machine.cpu_percent || 0).toFixed(1) + '%'));
    setTextIfChanged('hostRam', machine.memory ? (machine.memory.percent.toFixed(1) + '%') : 'n/a');
    setTextIfChanged('hostDisk', machine.disk ? (machine.disk.percent.toFixed(1) + '%') : 'n/a');
    if (gpu.ok && gpu.gpus && gpu.gpus.length) {
      var gpuLines = gpu.gpus.map(function (entry, idx) {
        var label = String(entry.label || ('GPU ' + idx));
        var name = String(entry.name || 'Unknown GPU');
        var usedMb = Number(entry.memory_used_mb || 0);
        var totalMb = Number(entry.memory_total_mb || 0);
        var util = Number(entry.utilization_percent || 0);
        var temp = Number(entry.temperature_c || 0);
        return label + ' [' + name + ']\n'
          + util + '% util / '
          + usedMb + ' MB'
          + (totalMb ? ' of ' + totalMb + ' MB' : '')
          + (temp ? ' / ' + temp + ' C' : '');
      });
      setTextIfChanged('hostGpu', gpuLines.join('\n\n'));
    } else {
      setTextIfChanged('hostGpu', gpu.detail || 'n/a');
    }

    renderBlockIfChanged('machineMeta', {
      sampled_at: machine.sampled_at || null,
      refresh_interval_seconds: machine.refresh_interval_seconds || null,
      gpu_enabled: typeof machine.gpu_enabled === 'undefined' ? null : machine.gpu_enabled
    }, humanMachineMeta(machine), 'machineMeta');

    renderBlockIfChanged('backendHealth', {
      shana_url: data.shana && data.shana.url ? data.shana.url : null,
      api_health: health
    }, humanBackendHealth(data, health), 'backendHealth');

    var backendOk = !!health.ok;
    updateStatusChip(
      'stickyShanaStatus',
      'Shana: ' + (process.running ? 'running' + (process.pid ? ' #' + process.pid : '') : 'stopped'),
      process.running ? 'good' : 'bad'
    );
    updateStatusChip(
      'stickyBackendStatus',
      'API ' + (backendOk ? 'OK' : 'Down'),
      backendOk ? 'good' : 'warn'
    );
  }

  function humanBackendHealth(data, health) {
    var process = data.shana && data.shana.process ? data.shana.process : {};
    var lines = [
      'Shana URL: ' + data.shana.url,
      'Process: ' + (process.running ? 'Running' : 'Stopped'),
      'API: ' + fmtHealthStatus(health)
    ];
    if (process.running && process.pid) lines.push('PID: ' + process.pid);
    if (process.running && typeof process.cpu_percent !== 'undefined') lines.push('CPU: ' + (Number(process.cpu_percent || 0).toFixed(1) + '%'));
    if (process.running && typeof process.rss_bytes !== 'undefined') lines.push('Memory: ' + fmtBytes(process.rss_bytes));
    if (health.detail) lines.push('Detail: ' + health.detail);
    return lines.join('\n');
  }

  function fmtHealthStatus(health) {
    if (!health) return 'unknown';
    if (health.ok) return 'Healthy';
    return health.detail ? 'Unavailable (' + health.detail + ')' : 'Unavailable';
  }

  function fmtBytes(bytes) {
    if (!bytes && bytes !== 0) return 'n/a';
    var units = ['B', 'KB', 'MB', 'GB', 'TB'];
    var size = bytes;
    var idx = 0;
    while (size >= 1024 && idx < units.length - 1) {
      size /= 1024;
      idx += 1;
    }
    return size.toFixed(idx === 0 ? 0 : 1) + ' ' + units[idx];
  }

  function renderTwitchSettings(settings) {
    settings = settings || {};
    var map = {
      twitchDryRun: 'dry_run',
      twitchVoiceEnabled: 'voice_enabled',
      twitchSubtitlesEnabled: 'subtitles_enabled',
      twitchAmbientChatEnabled: 'ambient_chat_enabled',
      twitchMentionRepliesEnabled: 'mention_replies_enabled',
      twitchSpamQuipsEnabled: 'spam_quips_enabled',
      twitchSelfGoalProposalsEnabled: 'self_goal_proposals_enabled',
      twitchLlmSafetyReviewEnabled: 'llm_safety_review_enabled'
    };
    Object.keys(map).forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.checked = !!settings[map[id]];
    });
    var minGap = document.getElementById('twitchMinSpeechGapSeconds');
    if (minGap) minGap.value = typeof settings.min_speech_gap_seconds === 'undefined' ? 5 : settings.min_speech_gap_seconds;
    var speechBudget = document.getElementById('twitchMaxSpeechSecondsPerMinute');
    if (speechBudget) speechBudget.value = typeof settings.max_speech_seconds_per_minute === 'undefined' ? 20 : settings.max_speech_seconds_per_minute;
    var spamGap = document.getElementById('twitchSpamQuipCooldownSeconds');
    if (spamGap) spamGap.value = typeof settings.spam_quip_cooldown_seconds === 'undefined' ? 60 : settings.spam_quip_cooldown_seconds;
  }

  function humanMachineMeta(machine) {
    var lines = [
      'Last sample: ' + fmtLocalDateTime(machine.sampled_at),
      'Refresh interval: ' + fmtSeconds(machine.refresh_interval_seconds),
      'GPU polling: ' + (machine.gpu_enabled ? 'Enabled' : 'Disabled')
    ];
    return lines.join('\n');
  }

  function renderTwitchWorker(worker) {
    renderBlockIfChanged('twitchWorkerStatus', worker, humanTwitchWorker(worker), 'twitchWorkerStatus');
  }

  function renderTwitchEventSub(eventsub) {
    renderBlockIfChanged('twitchEventSubStatus', eventsub, humanTwitchEventSub(eventsub), 'twitchEventSubStatus');
  }

  function renderTwitchViewerTrust(payload) {
    renderBlockIfChanged('twitchViewerTrust', payload, humanTwitchViewerTrust(payload), 'twitchViewerTrust');
  }

  function renderTwitchReplayResult(payload) {
    renderBlockIfChanged('twitchReplayResult', payload, humanTwitchReplayResult(payload), 'twitchReplayResult');
  }

  function humanTwitchWorker(worker) {
    worker = worker || {};
    var process = worker.process || {};
    var controls = worker.controls || {};
    var state = worker.state || {};
    var lines = [
      'Configured: ' + (worker.configured ? 'Yes' : 'No'),
      'Running: ' + (process.running ? 'Yes' : 'No'),
      'Channel: ' + (worker.channel || 'n/a')
    ];
    if (Array.isArray(worker.missing_config) && worker.missing_config.length) lines.push('Missing config: ' + worker.missing_config.join(', '));
    if (process.pid) lines.push('PID: ' + process.pid);
    if (state.status || state.updated_at) {
      lines.push('');
      lines.push('IRC State:');
      lines.push('Status: ' + (state.status || 'n/a') + ' / connected: ' + (state.connected ? 'yes' : 'no'));
      lines.push('Updated: ' + fmtLocalDateTime(state.updated_at));
      if (typeof state.reconnects !== 'undefined') lines.push('Reconnects: ' + state.reconnects);
      if (typeof state.message_count !== 'undefined') lines.push('Messages ingested: ' + state.message_count);
      if (state.last_message_kind) lines.push('Last message kind: ' + humanizeKey(state.last_message_kind));
      if (state.last_actor_display_name) lines.push('Last actor: ' + state.last_actor_display_name);
      if (state.last_posted_event_kind) lines.push('Last posted event: ' + humanizeKey(state.last_posted_event_kind));
      if (state.last_message_id) lines.push('Last message id: ' + state.last_message_id);
      if (state.last_post_error) lines.push('Last post error: ' + state.last_post_error);
      if (state.detail) lines.push('Detail: ' + state.detail);
    }
    lines.push('');
    lines.push('Controls:');
    lines.push('Dry run: ' + (controls.dry_run ? 'On' : 'Off'));
    lines.push('Voice: ' + (controls.voice_enabled ? 'On' : 'Off'));
    lines.push('Subtitles: ' + (controls.subtitles_enabled ? 'On' : 'Off'));
    lines.push('Ambient chat: ' + (controls.ambient_chat_enabled ? 'On' : 'Off'));
    lines.push('Mention replies: ' + (controls.mention_replies_enabled ? 'On' : 'Off'));
    lines.push('Spam quips: ' + (controls.spam_quips_enabled ? 'On' : 'Off'));
    lines.push('Self-goal proposals: ' + (controls.self_goal_proposals_enabled ? 'On' : 'Off'));
    lines.push('LLM safety review: ' + (controls.llm_safety_review_enabled ? 'On' : 'Off'));
    lines.push('Speech gap: ' + (typeof controls.min_speech_gap_seconds === 'undefined' ? 'n/a' : controls.min_speech_gap_seconds + ' sec'));
    lines.push('Speech budget: ' + (typeof controls.max_speech_seconds_per_minute === 'undefined' ? 'n/a' : controls.max_speech_seconds_per_minute + ' sec/min'));
    lines.push('Spam quip gap: ' + (typeof controls.spam_quip_cooldown_seconds === 'undefined' ? 'n/a' : controls.spam_quip_cooldown_seconds + ' sec'));
    if (Array.isArray(worker.ignored_bots) && worker.ignored_bots.length) lines.push('Ignored bots: ' + worker.ignored_bots.join(', '));
    if (worker.stdout_path) lines.push('Stdout: ' + worker.stdout_path);
    if (worker.stderr_path) lines.push('Stderr: ' + worker.stderr_path);
    return lines.join('\n');
  }

  function humanTwitchEventSub(eventsub) {
    eventsub = eventsub || {};
    var process = eventsub.process || {};
    var state = eventsub.state || {};
    var lines = [
      'Configured: ' + (eventsub.configured ? 'Yes' : 'No'),
      'Enabled: ' + (eventsub.enabled ? 'Yes' : 'No'),
      'Running: ' + (process.running ? 'Yes' : 'No'),
      'Broadcaster ID: ' + (eventsub.broadcaster_user_id || 'n/a')
    ];
    if (Array.isArray(eventsub.missing_config) && eventsub.missing_config.length) lines.push('Missing config: ' + eventsub.missing_config.join(', '));
    if (process.pid) lines.push('PID: ' + process.pid);
    if (state.status || state.updated_at) {
      lines.push('');
      lines.push('EventSub State:');
      lines.push('Status: ' + (state.status || 'n/a') + ' / connected: ' + (state.connected ? 'yes' : 'no'));
      lines.push('Updated: ' + fmtLocalDateTime(state.updated_at));
      if (state.session_id) lines.push('Session: ' + state.session_id);
      if (typeof state.notification_count !== 'undefined') lines.push('Notifications: ' + state.notification_count);
      if (state.last_message_kind) lines.push('Last message kind: ' + humanizeKey(state.last_message_kind));
      if (state.last_subscription_type) lines.push('Last subscription: ' + state.last_subscription_type);
      if (state.last_posted_event_kind) lines.push('Last posted event: ' + humanizeKey(state.last_posted_event_kind));
      if (state.last_actor_display_name) lines.push('Last actor: ' + state.last_actor_display_name);
      if (typeof state.subscription_ok_count !== 'undefined') lines.push('Subscriptions OK: ' + state.subscription_ok_count);
      if (typeof state.subscription_error_count !== 'undefined') lines.push('Subscription errors: ' + state.subscription_error_count);
      if (Array.isArray(state.subscriptions) && state.subscriptions.length) {
        var subscriptionTypes = state.subscriptions.map(function (subscription) {
          return (subscription.ok ? 'OK ' : 'ERR ') + (subscription.type || 'subscription');
        });
        lines.push('Subscription types: ' + subscriptionTypes.join(', '));
      }
      if (state.last_post_error) lines.push('Last post error: ' + state.last_post_error);
      if (state.detail) lines.push('Detail: ' + state.detail);
    }
    return lines.join('\n');
  }

  function humanizeKey(value) {
    var text = String(value || '').trim();
    if (!text) return 'n/a';
    return text
      .replace(/^\/api\//, '')
      .replace(/[\/_-]+/g, ' ')
      .replace(/\s+/g, ' ')
      .replace(/\b\w/g, function (ch) { return ch.toUpperCase(); });
  }

  function fmtLocalDateTime(value, fallback) {
    if (!value) return fallback || 'n/a';
    var date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit'
    });
  }

  function renderAssistantSettings(settingsPayload) {
    var settings = settingsPayload || {};
    var bindings = {
      assistantSpeechFilterLevel: settings.speech_filter_level || 'strict',
      assistantHardBlockEnabled: !!settings.speech_filter_hard_block_enabled,
      assistantHeuristicEnabled: !!settings.speech_filter_heuristic_enabled,
      assistantLlmEnabled: !!settings.speech_filter_llm_enabled,
      assistantAutoRewrite: !!settings.speech_filter_auto_rewrite,
      assistantLlmModel: settings.speech_filter_llm_model || '',
      assistantLlmTemperature: typeof settings.speech_filter_llm_temperature === 'number' ? settings.speech_filter_llm_temperature : 0,
      assistantSafetyReviewTimeout: settings.stream_safety_review_timeout_seconds || 2,
      assistantSafetyReviewTimeoutAction: settings.stream_safety_review_timeout_action || 'skip',
      assistantRouterProfile: settings.llm_router_profile || 'balanced',
      assistantRouterHostedEscalation: !!settings.llm_router_allow_hosted_escalation,
      assistantRouterPersonaHosted: !!settings.llm_router_persona_hosted_fallback_enabled,
      assistantRouterPersonaHeavyHosted: !!settings.llm_router_persona_heavy_hosted_fallback_enabled,
      assistantRouterLightWords: settings.llm_router_chat_light_max_input_words,
      assistantRouterComplexWords: settings.llm_router_complex_max_input_words,
      assistantStateEnabled: !!settings.assistant_state_enabled,
      assistantEmotionDecayTurns: settings.assistant_emotion_decay_turns,
      assistantEmotionEpisodeThreshold: settings.assistant_emotion_episode_threshold,
      assistantEmotionPatternThreshold: settings.assistant_emotion_pattern_threshold,
      assistantProactiveIdleEnabled: !!settings.proactive_idle_enabled,
      assistantProactiveIdleMinSilence: settings.proactive_idle_min_silence_seconds,
      assistantProactiveIdleTargetSilence: settings.proactive_idle_target_silence_seconds,
      assistantProactiveIdleCooldown: settings.proactive_idle_cooldown_seconds,
      assistantProactiveIdleMaxAttempts: settings.proactive_idle_max_attempts_per_topic
    };
    Object.keys(bindings).forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!bindings[id];
      else if (typeof bindings[id] !== 'undefined' && bindings[id] !== null) el.value = bindings[id];
    });
  }

  function humanAssistantEmotion(data) {
    var state = data && data.state ? data.state : {};
    var episodes = data && Array.isArray(data.episodes) ? data.episodes : [];
    var patterns = data && Array.isArray(data.patterns) ? data.patterns : [];
    var lines = [
      'Current emotion: ' + (state.current_emotion || 'neutral'),
      'Intensity: ' + (typeof state.intensity === 'number' ? state.intensity.toFixed(2) : 'n/a'),
      'Decay turns: ' + (typeof state.decay_turns_remaining === 'number' ? state.decay_turns_remaining : 'n/a')
    ];
    if (state.cause_summary) lines.push('Cause: ' + state.cause_summary);
    if (episodes.length) {
      lines.push('');
      lines.push('Recent emotional episodes:');
      for (var i = Math.max(0, episodes.length - 4); i < episodes.length; i += 1) {
        lines.push('- [' + (episodes[i].emotion || 'neutral') + '] ' + (episodes[i].event_summary || 'n/a'));
      }
    }
    if (patterns.length) {
      lines.push('');
      lines.push('Emotional patterns:');
      for (var j = Math.max(0, patterns.length - 4); j < patterns.length; j += 1) {
        lines.push('- ' + (patterns[j].pattern_text || 'n/a') + ' (evidence ' + (patterns[j].evidence_count || 0) + ')');
      }
    }
    return lines.join('\n');
  }

  function humanMemoryStats(stats) {
    var lines = [
      'Backend: ' + (stats.backend || 'n/a'),
      'Database: ' + (stats.database || 'n/a'),
      'Profile facts: ' + (typeof stats.profile_count === 'undefined' ? 'n/a' : stats.profile_count),
      'Episodic memories: ' + (typeof stats.episodic_count === 'undefined' ? 'n/a' : stats.episodic_count),
      'Session-scoped episodic memories: ' + (typeof stats.session_scoped_episodic_count === 'undefined' ? 'n/a' : stats.session_scoped_episodic_count),
      'Known people: ' + (typeof stats.known_people_count === 'undefined' ? 'n/a' : stats.known_people_count)
    ];
    return lines.join('\n');
  }

  function humanKnownPeople(people) {
    if (!people || !people.length) {
      return 'No known people stored yet.';
    }
    var lines = [];
    var i;
    for (i = 0; i < people.length; i += 1) {
      var person = people[i];
      var rel = person.relationship_to_user ? ' (' + person.relationship_to_user + ')' : '';
      lines.push('- ' + (person.name || 'Unnamed') + rel);
    }
    return lines.join('\n');
  }

  function humanRecentMemories(items) {
    if (!items || !items.length) {
      return 'No stored memories yet.';
    }
    var lines = ['Latest stored memory items by save order:'];
    for (var i = 0; i < items.length; i += 1) {
      var item = items[i] || {};
      var label = item.kind === 'episodic' ? 'Episodic' : 'Fact';
      var subject = item.subject_name ? ' [' + item.subject_name + ']' : '';
      var scope = item.session_id ? ' {session' + item.session_id + '}' : '';
      var when = item.created_at ? ' @ ' + fmtLocalTime(item.created_at, 'n/a') : '';
      lines.push('- ' + label + subject + scope + when + ': ' + (item.summary || 'n/a'));
    }
    return lines.join('\n');
  }

  function fmtLocalTime(value, fallback) {
    if (!value) return fallback || 'n/a';
    var date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleTimeString([], {
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit'
    });
  }

  function humanAssistantSettingsPayload(settingsPayload) {
    var settings = settingsPayload || {};
    return {
      speech_filter_level: settings.speech_filter_level || 'strict',
      speech_filter_hard_block_enabled: !!settings.speech_filter_hard_block_enabled,
      speech_filter_heuristic_enabled: !!settings.speech_filter_heuristic_enabled,
      speech_filter_llm_enabled: !!settings.speech_filter_llm_enabled,
      speech_filter_auto_rewrite: !!settings.speech_filter_auto_rewrite,
      speech_filter_llm_model: settings.speech_filter_llm_model || '',
      speech_filter_llm_temperature: typeof settings.speech_filter_llm_temperature === 'number' ? settings.speech_filter_llm_temperature : 0,
      stream_safety_review_timeout_seconds: settings.stream_safety_review_timeout_seconds || 2,
      stream_safety_review_timeout_action: settings.stream_safety_review_timeout_action || 'skip',
      llm_router_profile: settings.llm_router_profile || 'balanced',
      llm_router_allow_hosted_escalation: !!settings.llm_router_allow_hosted_escalation,
      llm_router_persona_hosted_fallback_enabled: !!settings.llm_router_persona_hosted_fallback_enabled,
      llm_router_persona_heavy_hosted_fallback_enabled: !!settings.llm_router_persona_heavy_hosted_fallback_enabled,
      llm_router_chat_light_max_input_words: settings.llm_router_chat_light_max_input_words,
      llm_router_complex_max_input_words: settings.llm_router_complex_max_input_words,
      assistant_state_enabled: !!settings.assistant_state_enabled,
      assistant_emotion_decay_turns: settings.assistant_emotion_decay_turns,
      assistant_emotion_episode_threshold: settings.assistant_emotion_episode_threshold,
      assistant_emotion_pattern_threshold: settings.assistant_emotion_pattern_threshold,
      proactive_idle_enabled: !!settings.proactive_idle_enabled,
      proactive_idle_min_silence_seconds: settings.proactive_idle_min_silence_seconds,
      proactive_idle_target_silence_seconds: settings.proactive_idle_target_silence_seconds,
      proactive_idle_cooldown_seconds: settings.proactive_idle_cooldown_seconds,
      proactive_idle_max_attempts_per_topic: settings.proactive_idle_max_attempts_per_topic
    };
  }

  function updateStatusChip(elementId, text, tone) {
    var el = document.getElementById(elementId);
    if (!el) return;
    el.textContent = text;
    el.classList.remove('good', 'warn', 'bad');
    if (tone) el.classList.add(tone);
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

  function renderBlock(elementId, rawValue, humanText) {
    var el = document.getElementById(elementId);
    if (!el) return;
    if (viewMode === 'json') {
      el.textContent = pretty(rawValue);
    } else {
      el.innerHTML = escapeHtml(humanText);
    }
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

  function pretty(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  var viewMode = localStorage.getItem('gammaDashboardViewMode') || 'human';
  var sectionHashes = {};
})();