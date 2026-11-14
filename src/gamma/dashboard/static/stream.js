// stream.js - HTML/text renderers for stream feeds, viewer trust and replay results.
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
        streamQuoteHtml(response.display_text || response.spoken_text, 'stream-reply')
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

  window.streamEmptyHtml = streamEmptyHtml;
  window.humanStreamTraces = humanStreamTraces;
  window.humanStreamSafety = humanStreamSafety;
  window.humanStreamOutputs = humanStreamOutputs;
  window.humanStreamQueue = humanStreamQueue;
  window.humanStreamTempMemory = humanStreamTempMemory;
  window.humanStreamSelfGoals = humanStreamSelfGoals;
  window.humanTwitchViewerTrust = humanTwitchViewerTrust;
  window.humanTwitchReplayResult = humanTwitchReplayResult;
})();
