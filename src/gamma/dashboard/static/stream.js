// stream.js - Stream activity functions for Gamma dashboard
(function () {
  function streamEmptyHtml(message) {
    return '<div class="stream-empty">' + escapeHtml(message) + '</div>';
  }

  function streamBadge(label, tone) {
    var safeTone = tone ? ' stream-badge-' + tone : '';
    return '<span class="stream-badge' + safeTone + '">' + escapeHtml(label || 'n/a') + '</span>';
  }

  function streamDecisionTone(value, reason) {
    var text = String(value || reason || '').toLowerCase();
    if (text.indexOf('block') >= 0 || text.indexOf('drop') >= 0 || text.indexOf('reject') >= 0 || text.indexOf('failed') >= 0) return 'bad';
    if (text.indexOf('soften') >= 0 || text.indexOf('defer') >= 0 || text.indexOf('skip') >= 0 || text.indexOf('timeout') >= 0 || text.indexOf('dry') >= 0) return 'warn';
    if (text.indexOf('speak') >= 0 || text.indexOf('allow') >= 0 || text.indexOf('approve') >= 0 || text.indexOf('queued') >= 0) return 'good';
    return 'info';
  }

  function streamCardHtml(parts) {
    return '<article class="stream-card">' + parts.filter(Boolean).join('') + '</article>';
  }

  function streamHeaderHtml(metaHtml) {
    return '<div class="stream-card-header">' + metaHtml.filter(Boolean).join('') + '</div>';
  }

  function streamTitleHtml(title, subtitle) {
    return '<div><div class="stream-card-title">' + escapeHtml(title || 'Unknown') + '</div>'
      + (subtitle ? '<div class="stream-card-subtitle">' + escapeHtml(subtitle) + '</div>' : '')
      + '</div>';
  }

  function streamQuoteHtml(text, className) {
    if (!text) return '';
    return '<div class="' + (className || 'stream-quote') + '">' + escapeHtml(oneLine(text, 260)) + '</div>';
  }

  function streamKvHtml(items) {
    var rows = items.filter(function (item) { return item && item.value !== '' && typeof item.value !== 'undefined' && item.value !== null; }).map(function (item) {
      return '<div><dt>' + escapeHtml(item.label) + '</dt><dd>' + escapeHtml(item.value) + '</dd></div>';
    });
    if (!rows.length) return '';
    return '<dl class="stream-kv">' + rows.join('') + '</dl>';
  }

  function oneLine(value, maxLength) {
    var text = String(value || '').replace(/\s+/g, ' ').trim();
    if (text.length <= maxLength) return text;
    return text.slice(0, Math.max(0, maxLength - 3)) + '...';
  }

  function humanStreamTraces(payload) {
    var items = payload && Array.isArray(payload.items) ? payload.items : [];
    if (!items.length) return streamEmptyHtml('No recent stream traces.');
    return items.slice(-12).reverse().map(function (item) {
      var input = item.input_event || {};
      var actor = input.actor || {};
      var metadata = input.metadata || {};
      var decision = item.decision || {};
      var decisionMeta = decision.metadata || {};
      var response = item.assistant_response || {};
      var outputs = Array.isArray(item.output_events) ? item.output_events : [];
      var outputTypes = outputs.map(function (event) { return event.type || 'output'; }).join(', ') || 'none';
      var speaker = actor.display_name || actor.platform_id || actor.source || 'unknown';
      var when = fmtLocalDateTime(item.recorded_at || input.occurred_at);
      var detailRows = [
        { label: 'Reason', value: decision.reason || 'n/a' },
        { label: 'Mode', value: decision.response_mode || 'n/a' },
        { label: 'Priority', value: typeof input.priority === 'undefined' ? 'n/a' : input.priority },
        { label: 'Outputs', value: outputTypes }
      ];
      if (decisionMeta.dry_run) detailRows.push({ label: 'Dry run', value: 'yes, voice suppressed: ' + (decisionMeta.dry_run_voice_suppressed ? 'yes' : 'no') });
      if (decisionMeta.would_decision) detailRows.push({ label: 'Would do', value: decisionMeta.would_decision + ' / ' + (decisionMeta.would_reason || 'n/a') });
      if (metadata.input_safety && Array.isArray(metadata.input_safety.reasons) && metadata.input_safety.reasons.length) {
        detailRows.push({ label: 'Input safety', value: (metadata.input_safety.category || 'classified') + ' / ' + metadata.input_safety.reasons.join(', ') });
      }
      return streamCardHtml([
        streamHeaderHtml([
          '<span class="stream-time">' + escapeHtml(when) + '</span>',
          streamBadge(input.kind || 'event', 'info'),
          streamBadge(decision.decision || 'n/a', streamDecisionTone(decision.decision, decision.reason))
        ]),
        streamTitleHtml(speaker, actor.platform_id || actor.source || ''),
        streamQuoteHtml(input.text, 'stream-quote'),
        streamKvHtml(detailRows),
        streamQuoteHtml(response.spoken_text, 'stream-reply')
      ]);
    }).join('');
  }

  function humanStreamSafety(payload) {
    var items = payload && Array.isArray(payload.items) ? payload.items : [];
    var safetyItems = [];
    items.forEach(function (item) {
      var input = item.input_event || {};
      var metadata = input.metadata || {};
      var safety = metadata.input_safety || item.safety_decision || {};
      if (!safety || (!safety.category && !safety.action && !safety.reasons)) return;
      safetyItems.push({ item: item, input: input, safety: safety });
    });
    if (!safetyItems.length) return streamEmptyHtml('No recent safety-classified stream events.');
    return safetyItems.slice(-12).reverse().map(function (entry) {
      var item = entry.item;
      var input = entry.input;
      var safety = entry.safety;
      var reasons = Array.isArray(safety.reasons) && safety.reasons.length ? safety.reasons.join(', ') : 'n/a';
      if (reasons === 'n/a' && Array.isArray(safety.matched_rules) && safety.matched_rules.length) reasons = safety.matched_rules.join(', ');
      var decision = item.decision || {};
      var category = safety.category || safety.action || 'classified';
      return streamCardHtml([
        streamHeaderHtml([
          '<span class="stream-time">' + escapeHtml(fmtLocalDateTime(item.recorded_at || input.occurred_at)) + '</span>',
          streamBadge(category, streamDecisionTone(safety.action || category, reasons)),
          safety.stage ? streamBadge(safety.stage, 'info') : '',
          safety.review_timeout ? streamBadge('timeout', 'warn') : ''
        ]),
        streamTitleHtml((input.actor && (input.actor.display_name || input.actor.platform_id)) || input.kind || 'Stream event', decision.reason || 'No decision reason'),
        streamKvHtml([
          { label: 'Decision', value: (decision.decision || 'n/a') + ' / ' + (decision.reason || 'n/a') },
          { label: 'Drop', value: safety.should_drop ? 'yes' : 'no' },
          { label: 'Approved', value: safety.playback_approved ? 'yes' : 'no' },
          { label: 'Reasons', value: reasons }
        ]),
        streamQuoteHtml(safety.safe_prompt_text || input.text || 'n/a', 'stream-quote')
      ]);
    }).join('');
  }

  function humanStreamOutputs(payload) {
    var items = payload && Array.isArray(payload.items) ? payload.items : [];
    if (!items.length) return streamEmptyHtml('No recent stream output events.');
    return items.slice(-12).reverse().map(function (item) {
      var event = item.output_event || {};
      var payload = event.payload || {};
      var adapterPayload = item.adapter_payload || {};
      var detail = payload.text || payload.emotion || payload.motion || adapterPayload.subtitle || '';
      return streamCardHtml([
        streamHeaderHtml([
          '<span class="stream-time">' + escapeHtml(fmtLocalDateTime(item.recorded_at || event.occurred_at)) + '</span>',
          streamBadge(event.type || 'output', 'info')
        ]),
        streamQuoteHtml(detail || pretty(payload || adapterPayload), 'stream-quote'),
        streamKvHtml([
          { label: 'Input', value: event.input_event_id || '' },
          { label: 'Turn', value: event.turn_id || '' }
        ])
      ]);
    }).join('');
  }

  function humanStreamQueue(payload) {
    var slots = payload && payload.slots ? payload.slots : {};
    var names = Object.keys(slots);
    if (!names.length) return streamEmptyHtml('No pending stream speech.');
    return names.sort().map(function (slotName) {
      var item = slots[slotName] || {};
      var actor = item.actor || {};
      var speaker = actor.display_name || actor.platform_id || actor.source || 'unknown';
      return streamCardHtml([
        streamHeaderHtml([
          streamBadge(slotName, 'info'),
          streamBadge(item.decision || 'queued', streamDecisionTone(item.decision, item.reason))
        ]),
        streamTitleHtml(speaker, (item.kind || 'event') + ' queued ' + fmtLocalDateTime(item.queued_at)),
        streamKvHtml([
          { label: 'Reason', value: item.reason || 'n/a' },
          {
            label: 'Gap',
            value: (typeof item.elapsed_seconds === 'undefined' ? 'n/a' : item.elapsed_seconds + 's') + ' / ' + (typeof item.min_gap_seconds === 'undefined' ? 'n/a' : item.min_gap_seconds + 's')
          },
          { label: 'Replaced', value: item.replaced_event_id || '' }
        ]),
        streamQuoteHtml(item.text || 'n/a', 'stream-quote')
      ]);
    }).join('');
  }

  function humanStreamTempMemory(payload) {
    var items = payload && Array.isArray(payload.items) ? payload.items : [];
    if (!items.length) return streamEmptyHtml('No temporary stream memory.');
    return items.slice(0, 12).map(function (item) {
      var metadata = item.metadata || {};
      var decision = metadata.last_decision || metadata.decision || '';
      var reason = metadata.last_reason || metadata.reason || '';
      return streamCardHtml([
        streamHeaderHtml([
          streamBadge(item.bucket || 'bucket', 'info'),
          decision ? streamBadge(decision, streamDecisionTone(decision, reason)) : ''
        ]),
        streamTitleHtml(item.key || 'key', 'Updated ' + fmtLocalDateTime(item.updated_at)),
        streamQuoteHtml(item.value || '', 'stream-quote'),
        streamKvHtml([{ label: 'Reason', value: reason || '' }])
      ]);
    }).join('');
  }

  function humanStreamSelfGoals(payload) {
    var items = payload && Array.isArray(payload.items) ? payload.items : [];
    if (!items.length) return streamEmptyHtml('No stream self-goals.');
    return items.slice(0, 12).map(function (item) {
      return streamCardHtml([
        streamHeaderHtml([
          streamBadge('#' + item.id, 'info'),
          streamBadge(item.status || 'status', streamDecisionTone(item.status, ''))
        ]),
        streamTitleHtml(item.title || 'goal', 'Updated ' + fmtLocalDateTime(item.updated_at)),
        streamQuoteHtml(item.description || '', 'stream-quote')
      ]);
    }).join('');
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

  function humanDiscordRuntime(runtime) {
    var state = runtime.enabled ? 'enabled' : 'disabled';
    var configured = runtime.configured ? 'configured' : 'not configured';
    var running = runtime.running ? 'running' : 'stopped';
    var output = runtime.output_enabled ? 'output on' : 'output off';
    var counts = 'inputs ' + (runtime.input_count || 0) + ', outputs ' + (runtime.output_count || 0);
    return state + ', ' + configured + ', ' + running + ', ' + output + ', ' + counts + (runtime.last_error ? ' / error ' + runtime.last_error : '');
  }

  function humanVTubeStudioAdapter(adapter) {
    var state = adapter.enabled ? 'enabled' : 'disabled';
    var configured = adapter.configured ? 'configured' : 'not configured';
    var client = adapter.client || {};
    var runner = adapter.runner || {};
    var connected = client.connected ? 'connected' : 'disconnected';
    var auth = client.authenticated ? 'auth ok' : (client.token_requested ? 'token requested' : 'auth pending');
    var runnerState = runner.running ? 'runner on' : 'runner off';
    var action = adapter.last_action && adapter.last_action.action_type ? ' / last ' + adapter.last_action.action_type : '';
    var error = adapter.last_error || client.last_error || runner.last_error;
    return state + ', ' + configured + ', ' + connected + ', ' + auth + ', ' + runnerState + action + (error ? ' / error ' + error : '');
  }

  function humanTwitchViewerTrust(payload) {
    var items = payload && Array.isArray(payload.items) ? payload.items : [];
    if (!items.length) return 'No viewer trust overrides saved.';
    return items.map(function (item) {
      var name = item.display_name ? (' / ' + item.display_name) : '';
      var alias = item.pronunciation_alias ? (' / say: ' + item.pronunciation_alias) : '';
      var notes = item.notes ? ('\n  notes: ' + item.notes) : '';
      return item.platform_user_id + name + ' -> ' + item.trust_level + alias + notes;
    }).join('\n\n');
  }

  function humanTwitchReplayResult(payload) {
    if (!payload || !payload.ok) return 'No replay run yet.';
    var lines = [];
    if (payload.scenario) lines.push('Scenario: ' + humanizeKey(payload.scenario));
    lines.push('Replay events posted: ' + payload.count);
    if (payload.summary) {
      lines.push('Safety categories: ' + compactCounts(payload.summary.safety_categories || {}));
      lines.push('Decisions: ' + compactNestedCounts(payload.summary.decisions_by_kind || {}));
    }
    var results = Array.isArray(payload.results) ? payload.results : [];
    for (var i = 0; i < Math.min(results.length, 8); i += 1) {
      var result = results[i] || {};
      var input = result.input_event || {};
      var decision = result.decision || {};
      lines.push((i + 1) + '. ' + (input.kind || 'event') + ' -> ' + (decision.decision || 'n/a') + ' / ' + (decision.reason || 'n/a'));
    }
    if (results.length > 8) lines.push('...');
    return lines.join('\n');
  }

  function humanProviderAction(action) {
    if (!action || !action.status) {
      return 'No provider action has been run yet.';
    }
    var lines = [
      'Action: ' + humanizeKey(action.action),
      'Status: ' + humanizeKey(action.status),
      'Detail: ' + (action.detail || 'n/a'),
      'Ran at: ' + fmtLocalDateTime(action.ran_at)
    ];
    if (action.provider) lines.push('Provider: ' + providerLabel(action.provider));
    if (action.label) lines.push('Target: ' + action.label);
    if (typeof action.returncode !== 'undefined') {
      lines.push('Return code: ' + action.returncode);
    }
    if (typeof action.duration_ms !== 'undefined') {
      lines.push('Duration: ' + fmtDurationMs(action.duration_ms));
    }
    if (action.stdout) {
      lines.push('');
      lines.push('Output:');
      lines.push(action.stdout);
    }
    if (action.stderr) {
      lines.push('');
      lines.push('Error output:');
      lines.push(action.stderr);
    }
    return lines.join('\n');
  }

  function outputActorLabel(actor) {
    actor = actor || {};
    var source = actor.source || 'unknown';
    var name = actor.display_name || actor.platform_id || 'unknown';
    var roles = Array.isArray(actor.roles) && actor.roles.length ? ' [' + actor.roles.join(', ') + ']' : '';
    return source + ':' + name + roles;
  }

  function outputInputLabel(input) {
    input = input || {};
    var kind = input.kind || 'unknown';
    return input.session_id ? kind + ' / ' + input.session_id : kind;
  }

  function outputRecentTargetLabel(event) {
    if (!event) return 'none';
    var actor = event.payload && event.payload.actor ? ' / ' + outputActorLabel(event.payload.actor) : '';
    return '#' + (event.sequence || 'n/a') + ' / ' + (event.type || 'unknown') + ' / ' + (event.turn_id || 'n/a') + actor;
  }

  function humanPerformerBus(payload) {
    payload = payload || {};
    var stats = payload.stats || {};
    var byTarget = stats.subscribers_by_target || {};
    var mutedTargets = Array.isArray(stats.muted_targets) ? stats.muted_targets : [];
    var subscribers = Array.isArray(stats.subscribers) ? stats.subscribers : [];
    var recent = payload.recent_event || null;
    var recentByTarget = payload.recent_by_target || {};
    var adapters = payload.adapters || {};
    var lines = [];
    lines.push('Output bus: ' + (payload.ok ? 'reachable' : 'unreachable'));
    lines.push('Subscribers: ' + (stats.subscriber_count || 0) + ' / history: ' + (stats.history_count || 0) + ' / seq: ' + (stats.last_sequence || 0));
    lines.push('Targets: ' + compactCounts(byTarget));
    lines.push('Muted targets: ' + (mutedTargets.length ? mutedTargets.join(', ') : 'none'));
    if (adapters.vtube_studio) {
      lines.push('VTube Studio: ' + humanVTubeStudioAdapter(adapters.vtube_studio));
    }
    if (adapters.discord) {
      lines.push('Discord: ' + humanDiscordRuntime(adapters.discord));
    }
    if (subscribers.length) {
      lines.push('');
      lines.push('Connected clients:');
      subscribers.forEach(function (subscriber) {
        var name = humanizeKey(subscriber.client_name || 'unknown_client');
        var target = subscriber.target_policy || 'stream_public';
        var host = subscriber.client_host || 'unknown host';
        lines.push('- ' + name + ' / ' + target + ' / ' + host);
      });
    }
    if (recent) {
      var recentPayload = recent.payload || {};
      var actor = recentPayload.actor || {};
      var input = recentPayload.input || {};
      lines.push('');
      lines.push('Last event: ' + (recent.type || 'unknown'));
      lines.push('Sequence: ' + (recent.sequence || 'n/a'));
      lines.push('Target: ' + (recent.target_policy || 'stream_public'));
      lines.push('Turn: ' + (recent.turn_id || 'n/a'));
      lines.push('Input: ' + outputInputLabel(input));
      lines.push('Actor: ' + outputActorLabel(actor));
      lines.push('At: ' + fmtLocalDateTime(recent.occurred_at));
      outputTargetPolicies(stats, recentByTarget).forEach(function (targetPolicy) {
        lines.push(humanizeKey(targetPolicy) + ' latest: ' + outputRecentTargetLabel(recentByTarget[targetPolicy]));
      });
    } else if (payload.detail) {
      lines.push('Detail: ' + payload.detail);
    } else {
      lines.push('Last event: none');
    }
    return lines.join('\n');
  }

  function outputTargetPolicies(stats, recentByTarget) {
    var policies = Array.isArray(stats.target_policies) ? stats.target_policies.slice() : [];
    Object.keys(stats.subscribers_by_target || {}).forEach(function (targetPolicy) {
      if (policies.indexOf(targetPolicy) === -1) policies.push(targetPolicy);
    });
    Object.keys(recentByTarget || {}).forEach(function (targetPolicy) {
      if (policies.indexOf(targetPolicy) === -1) policies.push(targetPolicy);
    });
    if (!policies.length) {
      policies = ['stream_public', 'dashboard_monitor'];
    }
    return policies;
  }

  function compactCounts(counts) {
    var keys = Object.keys(counts || {}).sort();
    if (!keys.length) return 'none';
    return keys.map(function (key) { return humanizeKey(key) + '=' + counts[key]; }).join(', ');
  }

  function compactNestedCounts(counts) {
    var keys = Object.keys(counts || {}).sort();
    if (!keys.length) return 'none';
    return keys.map(function (key) {
      return humanizeKey(key) + '[' + compactCounts(counts[key] || {}) + ']';
    }).join(', ');
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

  function fmtDurationMs(value) {
    var ms = Number(value);
    if (!isFinite(ms) || ms < 0) return 'n/a';
    if (ms < 1000) return Math.round(ms) + ' ms';
    return (ms / 1000).toFixed(ms >= 10000 ? 1 : 2) + ' sec';
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

  var viewMode = localStorage.getItem('gammaDashboardViewMode') || 'human';
  var sectionHashes = {};

  function renderBlock(elementId, rawValue, humanText) {
    var el = document.getElementById(elementId);
    if (!el) return;
    if (viewMode === 'json') {
      el.textContent = pretty(rawValue);
    } else {
      el.innerHTML = escapeHtml(humanText);
    }
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

  function pretty(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
    }
  }

})();