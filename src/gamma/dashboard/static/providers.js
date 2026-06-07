// providers.js - Provider management functions for Gamma dashboard
(function () {
  var pretty = function (value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
    }
  };

  var escapeHtml = function (value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  };

  var fmtArtifactTimestamp = function (filename) {
    var m = filename.match(/(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/);
    if (!m) return '';
    var d = new Date(Date.UTC(+m[1], +m[2]-1, +m[3], +m[4], +m[5], +m[6]));
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  var fmtBytes = function (bytes) {
    if (!bytes && bytes !== 0) return 'n/a';
    var units = ['B', 'KB', 'MB', 'GB', 'TB'];
    var size = bytes;
    var idx = 0;
    while (size >= 1024 && idx < units.length - 1) {
      size /= 1024;
      idx += 1;
    }
    return size.toFixed(idx === 0 ? 0 : 1) + ' ' + units[idx];
  };

  var providerLabel = function (value, kind) {
    var normalized = String(value || '').toLowerCase();
    if (normalized === 'local' && kind === 'stt') return 'Local Whisper';
    if (normalized === 'local' && kind === 'tts') return 'GPT-SoVITS';
    if (normalized === 'local') return 'Local';
    if (normalized === 'faster-whisper' || normalized === 'faster_whisper') return 'Faster Whisper';
    if (normalized === 'gpt-sovits' || normalized === 'gpt_sovits') return 'GPT-SoVITS';
    if (normalized === 'qwen-tts' || normalized === 'qwen_tts' || normalized === 'qwen' || normalized === 'qwentts') return 'Qwen3-TTS';
    if (normalized === 'openai') return 'OpenAI';
    if (normalized === 'stt') return 'STT';
    if (normalized === 'llm') return 'LLM';
    if (!normalized) return 'n/a';
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
  };

  var fmtHealthStatus = function (health) {
    if (!health) return 'unknown';
    if (health.ok) return 'Healthy';
    return health.detail ? 'Unavailable (' + health.detail + ')' : 'Unavailable';
  };

  function humanProviders(providers) {
    var lines = [];
    var llm = providers.llm || {};
    var stt = providers.stt || {};
    var tts = providers.tts || {};
    lines.push('LLM: ' + providerLabel(llm.provider, 'llm') + (llm.model ? ' using ' + llm.model : ''));
    if (llm.endpoint) lines.push('LLM endpoint: ' + llm.endpoint);
    if (llm.health) lines.push('LLM health: ' + fmtHealthStatus(llm.health));
    if (llm.router_capabilities && llm.router_capabilities.length) {
      for (var c = 0; c < llm.router_capabilities.length; c += 1) {
        var capability = llm.router_capabilities[c] || {};
        if (!capability.health) continue;
        lines.push(
          'LLM capability: ' +
          providerLabel(capability.provider, 'llm') +
          ' [' + formatRouterScope(capability.scope) + '] ' +
          fmtHealthStatus(capability.health)
        );
      }
    }
    lines.push('LLM router: ' + (llm.router_enabled ? 'Enabled' : 'Disabled'));
    if (llm.router_enabled) {
      lines.push('Router profile: ' + (llm.router_profile || 'balanced'));
      lines.push('Router default: ' + providerLabel(llm.router_default_provider, 'llm') + (llm.router_default_model ? ' using ' + llm.router_default_model : ''));
      lines.push('Hosted escalation: ' + (llm.router_hosted_escalation ? 'Enabled' : 'Disabled'));
      if (llm.router_hosted_escalation) {
        lines.push('Hosted route: ' + providerLabel(llm.router_hosted_provider, 'llm') + (llm.router_hosted_model ? ' using ' + llm.router_hosted_model : ''));
      }
      if (llm.router_failure_backoff_seconds) lines.push('Failure backoff: ' + llm.router_failure_backoff_seconds + ' sec');
      if (llm.provider_backoff_entries && llm.provider_backoff_entries.length) {
        var backoffLines = [];
        for (var b = 0; b < llm.provider_backoff_entries.length; b += 1) {
          var backoffEntry = llm.provider_backoff_entries[b] || {};
          backoffLines.push(
            providerLabel(backoffEntry.provider, 'llm') + ' [' + formatRouterScope(backoffEntry.scope) + ']: ' + backoffEntry.seconds + ' sec'
          );
        }
        if (backoffLines.length) lines.push('Active backoff: ' + backoffLines.join(' | '));
      }
      if (llm.last_route) {
        lines.push(
          'Last route: ' +
          providerLabel(llm.last_route.provider, 'llm') +
          (llm.last_route.model ? ' using ' + llm.last_route.model : '') +
          ' [' + (llm.last_route.route_family || 'route') + ', ' + (llm.last_route.status || 'n/a') + ']'
        );
      }
      if (llm.route_summary && llm.route_summary.provider_counts) {
        var providerCounts = [];
        for (var providerName in llm.route_summary.provider_counts) {
          if (!Object.prototype.hasOwnProperty.call(llm.route_summary.provider_counts, providerName)) continue;
          providerCounts.push(providerLabel(providerName, 'llm') + ': ' + llm.route_summary.provider_counts[providerName]);
        }
        if (providerCounts.length) lines.push('Recent route mix: ' + providerCounts.join(' | '));
      }
      if (llm.route_summary && llm.route_summary.route_family_counts) {
        var familyCounts = [];
        for (var familyName in llm.route_summary.route_family_counts) {
          if (!Object.prototype.hasOwnProperty.call(llm.route_summary.route_family_counts, familyName)) continue;
          familyCounts.push(familyName + ': ' + llm.route_summary.route_family_counts[familyName]);
        }
        if (familyCounts.length) lines.push('Recent route families: ' + familyCounts.join(' | '));
      }
    }
    lines.push('');
    lines.push('STT: ' + providerLabel(stt.provider, 'stt') + (stt.model ? ' using ' + stt.model : ''));
    if (stt.device) lines.push('STT device: ' + stt.device);
    if (stt.health) lines.push('STT health: ' + fmtHealthStatus(stt.health));
    lines.push('');
    lines.push('TTS running: ' + providerLabel(tts.provider, 'tts') + (tts.model ? ' using ' + tts.model : ''));
    if (tts.profile_label) lines.push('TTS voice profile: ' + tts.profile_label);
    if (tts.selected_provider) lines.push('TTS saved selection: ' + providerLabel(tts.selected_provider, 'tts'));
    if (tts.selected_profile_label) lines.push('TTS saved profile: ' + tts.selected_profile_label);
    lines.push('TTS restart required: ' + (tts.restart_required ? 'Yes' : 'No'));
    if (tts.endpoint) lines.push('TTS endpoint: ' + tts.endpoint);
    if (typeof tts.rvc_enabled !== 'undefined') lines.push('RVC post-process: ' + (tts.rvc_enabled ? 'Enabled' : 'Disabled'));
    if (tts.rvc_model_name) lines.push('RVC model: ' + tts.rvc_model_name);
    if (typeof tts.rvc_formant !== 'undefined' && tts.rvc_formant !== null) lines.push('RVC formant: ' + tts.rvc_formant);
    if (tts.health) lines.push('TTS health: ' + fmtHealthStatus(tts.health));
    return lines.join('\n');
  }

  function formatRouterScope(scope) {
    return String(scope || 'text').replace(/_/g, ' ');
  }

  function humanTtsEditorHelp(provider) {
    var normalized = String(provider || '').toLowerCase();
    var lines = ['Values JSON stores provider-specific settings for this profile.'];
    if (normalized === 'piper') {
      lines.push('Common keys: piper_model_path, piper_config_path, rvc_enabled, rvc_model_name, rvc_pitch, rvc_formant.');
    } else if (normalized === 'local' || normalized === 'gpt-sovits' || normalized === 'gpt_sovits') {
      lines.push('Common keys: gpt_sovits_reference_audio, gpt_sovits_prompt_text, gpt_sovits_prompt_lang, gpt_sovits_text_lang, gpt_sovits_extra_json.');
      lines.push('gpt_sovits_extra_json supports: top_k, top_p, temperature, text_split_method, batch_size, speed_factor, fragment_interval, seed.');
    } else if (isQwenProvider(normalized)) {
      lines.push('Required: qwen_tts_endpoint, qwen_tts_reference_audio, qwen_tts_reference_text, qwen_tts_language.');
      lines.push('Optional: qwen_tts_speaker (CustomVoice models only), qwen_tts_instruct (style hint).');
      lines.push('qwen_tts_extra_json generation params: temperature (0.9), top_k (50), top_p (1.0),');
      lines.push('  repetition_penalty (1.0), max_new_tokens (2048), min_new_tokens (50).');
      lines.push('Increase min_new_tokens if words feel cut off at the end.');
    } else if (normalized === 'openai') {
      lines.push('Common keys: tts_model, tts_voice, tts_format.');
    } else {
      lines.push('Use a flat JSON object of provider settings.');
    }
    lines.push('');
    lines.push('Example:');
    lines.push(ttsProfileExample(provider));
    return lines.join('\n');
  }

  function isQwenProvider(p) {
    var n = String(p || '').toLowerCase();
    return n === 'qwen-tts' || n === 'qwen_tts' || n === 'qwen' || n === 'qwentts';
  }

  function ttsProfileExample(provider) {
    var normalized = String(provider || '').toLowerCase();
    if (normalized === 'piper') {
      return {
        piper_model_path: './data/piper/en_US-lessac-medium.onnx',
        piper_config_path: './data/piper/en_US-lessac-medium.onnx.json',
        rvc_enabled: false
      };
    }
    if (normalized === 'local' || normalized === 'gpt-sovits' || normalized === 'gpt_sovits') {
      return {
        gpt_sovits_reference_audio: './data/GPT-SoVITS/reference/my_ref.wav',
        gpt_sovits_prompt_text: 'Exact words spoken in the reference clip.',
        gpt_sovits_prompt_lang: 'en',
        gpt_sovits_text_lang: 'en',
        gpt_sovits_extra_json: {
          top_k: 4,
          top_p: 0.82,
          temperature: 0.62,
          text_split_method: 'cut4',
          batch_size: 1,
          speed_factor: 0.97,
          seed: 11
        }
      };
    }
    if (isQwenProvider(normalized)) {
      return {
        qwen_tts_endpoint: 'http://127.0.0.1:9882/tts',
        qwen_tts_reference_audio: './data/GPT-SoVITS/reference/my_ref_mono32k.wav',
        qwen_tts_reference_text: 'Exact words spoken in the reference transcript.',
        qwen_tts_language: 'English',
        qwen_tts_extra_json: {
          temperature: 0.9,
          top_k: 50,
          top_p: 1.0,
          repetition_penalty: 1.0,
          max_new_tokens: 2048,
          min_new_tokens: 50
        }
      };
    }
    if (normalized === 'openai') {
      return {
        tts_model: 'gpt-4o-mini-tts',
        tts_voice: 'alloy',
        tts_format: 'wav'
      };
    }
    return {};
  }

  function collectTtsProfilePayload(overrideId, overrideLabel) {
    var providerSelect = document.getElementById('ttsProviderSelect');
    var profileId = overrideId || document.getElementById('ttsEditorProfileId').value.trim();
    var label = overrideLabel || document.getElementById('ttsEditorLabel').value.trim();
    var description = document.getElementById('ttsEditorDescription').value.trim();
    var valuesText = document.getElementById('ttsEditorValues').value.trim() || '{}';
    var provider = providerSelect ? providerSelect.value : '';
    var values = mergeStructuredTtsValues(provider, JSON.parse(valuesText));
    if (!profileId) throw new Error('Profile id is required.');
    if (!label) throw new Error('Profile label is required.');
    if (!values || typeof values !== 'object' || Array.isArray(values)) throw new Error('Profile values must be a JSON object.');

    return {
      id: profileId,
      label: label,
      provider: provider,
      description: description,
      values: values
    };
  }

  function mergeStructuredTtsValues(provider, values) {
    var normalized = String(provider || '').toLowerCase();
    var next = Object.assign({}, values || {});

    function setString(key, elementId) {
      var element = document.getElementById(elementId);
      if (!element) return;
      var value = element.value.trim();
      if (value) next[key] = value;
      else delete next[key];
    }

    function setBool(key, elementId) {
      var element = document.getElementById(elementId);
      if (!element) return;
      next[key] = !!element.checked;
    }

    if (normalized === 'piper') {
      setString('piper_model_path', 'ttsEditorPiperModelPath');
      setString('piper_config_path', 'ttsEditorPiperConfigPath');
      setBool('rvc_enabled', 'ttsEditorRvcEnabled');
      if (next.rvc_enabled) {
        setString('rvc_model_name', 'ttsEditorRvcModelName');
      } else {
        delete next.rvc_model_name;
      }
    } else if (normalized === 'local' || normalized === 'gpt-sovits' || normalized === 'gpt_sovits') {
      setString('gpt_sovits_reference_audio', 'ttsEditorSovitsReferenceAudio');
      setString('gpt_sovits_prompt_text', 'ttsEditorSovitsPromptText');
    } else if (isQwenProvider(normalized)) {
      setString('qwen_tts_endpoint', 'ttsEditorQwenEndpoint');
      setString('qwen_tts_reference_audio', 'ttsEditorQwenReferenceAudio');
      setString('qwen_tts_reference_text', 'ttsEditorQwenReferenceText');
      setString('qwen_tts_speaker', 'ttsEditorQwenSpeaker');
      setString('qwen_tts_language', 'ttsEditorQwenLanguage');
      setString('qwen_tts_instruct', 'ttsEditorQwenInstruct');
      var qwenExtraEl = document.getElementById('ttsEditorQwenExtraJson');
      if (qwenExtraEl && qwenExtraEl.value.trim()) {
        try { next.qwen_tts_extra_json = JSON.parse(qwenExtraEl.value); }
        catch (e) { /* leave existing value if JSON is invalid */ }
      } else {
        delete next.qwen_tts_extra_json;
      }
    } else if (normalized === 'openai') {
      setString('tts_model', 'ttsEditorOpenAiModel');
      setString('tts_voice', 'ttsEditorOpenAiVoice');
      setString('tts_format', 'ttsEditorOpenAiFormat');
    }
    return next;
  }

  function readTtsEditorProvider() {
    var providerSelect = document.getElementById('ttsProviderSelect');
    return providerSelect ? providerSelect.value : '';
  }

  function syncStructuredTtsFields(provider, values) {
    var normalized = String(provider || '').toLowerCase();
    var piperFields = document.getElementById('ttsEditorPiperFields');
    var sovitsFields = document.getElementById('ttsEditorSovitsFields');
    var qwenFields = document.getElementById('ttsEditorQwenFields');
    var openaiFields = document.getElementById('ttsEditorOpenAiFields');
    var piperModel = document.getElementById('ttsEditorPiperModelPath');
    var piperConfig = document.getElementById('ttsEditorPiperConfigPath');
    var rvcEnabled = document.getElementById('ttsEditorRvcEnabled');
    var rvcModel = document.getElementById('ttsEditorRvcModelName');
    var sovitsRef = document.getElementById('ttsEditorSovitsReferenceAudio');
    var sovitsPrompt = document.getElementById('ttsEditorSovitsPromptText');
    var qwenEndpoint = document.getElementById('ttsEditorQwenEndpoint');
    var qwenRefAudio = document.getElementById('ttsEditorQwenReferenceAudio');
    var qwenRefText = document.getElementById('ttsEditorQwenReferenceText');
    var qwenSpeaker = document.getElementById('ttsEditorQwenSpeaker');
    var qwenLanguage = document.getElementById('ttsEditorQwenLanguage');
    var qwenInstruct = document.getElementById('ttsEditorQwenInstruct');
    var qwenExtraJson = document.getElementById('ttsEditorQwenExtraJson');
    var openaiModel = document.getElementById('ttsEditorOpenAiModel');
    var openaiVoice = document.getElementById('ttsEditorOpenAiVoice');
    var openaiFormat = document.getElementById('ttsEditorOpenAiFormat');

    if (piperFields) {
      piperFields.hidden = normalized !== 'piper';
      piperFields.style.display = normalized === 'piper' ? '' : 'none';
    }
    if (sovitsFields) {
      var showSovits = normalized === 'local' || normalized === 'gpt-sovits' || normalized === 'gpt_sovits';
      sovitsFields.hidden = !showSovits;
      sovitsFields.style.display = showSovits ? '' : 'none';
    }
    if (qwenFields) {
      var showQwen = isQwenProvider(normalized);
      qwenFields.hidden = !showQwen;
      qwenFields.style.display = showQwen ? '' : 'none';
    }
    if (openaiFields) {
      openaiFields.hidden = normalized !== 'openai';
      openaiFields.style.display = normalized === 'openai' ? '' : 'none';
    }

    if (piperModel) piperModel.value = values.piper_model_path || '';
    if (piperConfig) piperConfig.value = values.piper_config_path || '';
    if (rvcEnabled) rvcEnabled.checked = !!values.rvc_enabled;
    if (rvcModel) {
      rvcModel.value = values.rvc_model_name || '';
      rvcModel.hidden = !(normalized === 'piper' && !!values.rvc_enabled);
      rvcModel.style.display = normalized === 'piper' && !!values.rvc_enabled ? '' : 'none';
    }
    if (sovitsRef) sovitsRef.value = values.gpt_sovits_reference_audio || '';
    if (sovitsPrompt) sovitsPrompt.value = values.gpt_sovits_prompt_text || '';
    if (qwenEndpoint) qwenEndpoint.value = values.qwen_tts_endpoint || '';
    if (qwenRefAudio) qwenRefAudio.value = values.qwen_tts_reference_audio || '';
    if (qwenRefText) qwenRefText.value = values.qwen_tts_reference_text || '';
    if (qwenSpeaker) qwenSpeaker.value = values.qwen_tts_speaker || '';
    if (qwenLanguage) qwenLanguage.value = values.qwen_tts_language || '';
    if (qwenInstruct) qwenInstruct.value = values.qwen_tts_instruct || '';
    if (qwenExtraJson) qwenExtraJson.value = values.qwen_tts_extra_json ? JSON.stringify(values.qwen_tts_extra_json, null, 2) : '';
    if (openaiModel) openaiModel.value = values.tts_model || '';
    if (openaiVoice) openaiVoice.value = values.tts_voice || '';
    if (openaiFormat) openaiFormat.value = values.tts_format || '';
  }

  function syncStructuredTtsFields(editorProvider, structured) {
    var provider = editorProvider || readTtsEditorProvider();
    syncStructuredTtsFields(provider, structured);
  }

  function renderTtsProfileEditor(tts) {
    var editor = tts && tts.editor_profile ? tts.editor_profile : {
      id: '',
      label: '',
      provider: ((tts && (tts.selected_provider || tts.provider)) || '').toLowerCase(),
      description: '',
      values: ttsProfileExample((tts && (tts.selected_provider || tts.provider)) || '')
    };
    var profileId = document.getElementById('ttsEditorProfileId');
    var label = document.getElementById('ttsEditorLabel');
    var description = document.getElementById('ttsEditorDescription');
    var values = document.getElementById('ttsEditorValues');
    var help = document.getElementById('ttsEditorHelp');
    var shell = document.getElementById('ttsProfileEditorShell');
    var statusShell = document.getElementById('ttsProfileEditorStatusShell');
    var structured = editor.values || {};
    if (shell) shell.style.display = '';
    if (statusShell) statusShell.style.display = '';
    if (profileId && profileId.value !== (editor.id || '')) profileId.value = editor.id || '';
    if (label && label.value !== (editor.label || '')) label.value = editor.label || '';
    if (description && description.value !== (editor.description || '')) description.value = editor.description || '';
    var prettyValues = pretty(editor.values || {});
    if (values && values.value !== prettyValues) values.value = prettyValues;
    syncStructuredTtsFields(editor.provider || '', structured);
    if (values) values.placeholder = pretty(ttsProfileExample(editor.provider || ''));
    if (help) {
      renderBlockIfChanged(
        'ttsEditorHelp',
        { provider: editor.provider || '', example: ttsProfileExample(editor.provider || '') },
        humanTtsEditorHelp(editor.provider || ''),
        'ttsEditorHelp'
      );
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

  function renderBlock(elementId, rawValue, humanText) {
    var el = document.getElementById(elementId);
    if (!el) return;
    if (viewMode === 'json') {
      el.textContent = pretty(rawValue);
    } else {
      el.innerHTML = escapeHtml(humanText);
    }
  }

  var viewMode = localStorage.getItem('gammaDashboardViewMode') || 'human';
  var sectionHashes = {};
})();