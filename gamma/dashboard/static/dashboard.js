(function () {
  var viewMode = localStorage.getItem('gammaDashboardViewMode') || 'human';
  var latestData = null;
  var mediaRecorder = null;
  var recordedChunks = [];
  var recordedBlob = null;
  var recordedMimeType = 'audio/webm';
  var liveSocket = null;
  var liveAudioContext = null;
  var liveMediaStream = null;
  var liveSourceNode = null;
  var liveProcessorNode = null;
  var liveAwaitingReply = false;
  var liveTurnOpen = false;
  var liveLastSpeechAt = 0;
  var liveTurnStartedAt = 0;
  var liveHistory = [];
  var liveJobMeta = null;
  var selectedVisionFile = null;
  var selectedVisionPreviewUrl = null;
  var visionHistory = [];
  var LIVE_TARGET_SAMPLE_RATE = 16000;
  var LIVE_SPEECH_THRESHOLD = 0.018;
  var LIVE_SILENCE_MS = 900;
  var LIVE_MIN_TURN_MS = 550;
  var liveMeterLevels = [];
  var sectionHashes = {};
  var runtimePollMs = 10000;
  var syncingTtsEditor = false;
  var runtimeStatusSupported = true;
  var ttsPlayerCurrentFile = null;
  var ttsPlayerSeeking = false;
  var ttsLastArtifactName = null;
  var ttsArtifactPollTimer = null;

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

  function fmtArtifactTimestamp(filename) {
    // Expects format: tts-20260413T170011Z.wav
    var m = filename.match(/(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/);
    if (!m) return '';
    var d = new Date(Date.UTC(+m[1], +m[2]-1, +m[3], +m[4], +m[5], +m[6]));
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function fmtTime(seconds) {
    if (!isFinite(seconds) || seconds < 0) return '0:00';
    var m = Math.floor(seconds / 60);
    var s = Math.floor(seconds % 60);
    return m + ':' + (s < 10 ? '0' : '') + s;
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

  function fmtDurationMs(value) {
    var ms = Number(value);
    if (!isFinite(ms) || ms < 0) return 'n/a';
    if (ms < 1000) return Math.round(ms) + ' ms';
    return (ms / 1000).toFixed(ms >= 10000 ? 1 : 2) + ' sec';
  }

  function fmtSeconds(value) {
    var seconds = Number(value);
    if (!isFinite(seconds) || seconds < 0) return 'n/a';
    if (seconds < 60) return seconds + ' sec';
    var minutes = Math.floor(seconds / 60);
    var remaining = seconds % 60;
    return minutes + ' min ' + remaining + ' sec';
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

  function providerLabel(value) {
    var normalized = String(value || '').toLowerCase();
    if (normalized === 'local' || normalized === 'gpt-sovits' || normalized === 'gpt_sovits') return 'GPT-SoVITS';
    if (normalized === 'qwen-tts' || normalized === 'qwen_tts' || normalized === 'qwen' || normalized === 'qwentts') return 'Qwen3-TTS';
    if (normalized === 'openai') return 'OpenAI';
    if (normalized === 'stt') return 'STT';
    if (normalized === 'llm') return 'LLM';
    if (!normalized) return 'n/a';
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
  }

  function fmtHealthStatus(health) {
    if (!health) return 'unknown';
    if (health.ok) return 'Healthy';
    return health.detail ? 'Unavailable (' + health.detail + ')' : 'Unavailable';
  }

  function updateStamp(text) {
    document.getElementById('stamp').textContent = text;
  }

  function setSectionOpen(sectionId, isOpen) {
    var body = document.getElementById(sectionId);
    var chevron = document.getElementById(sectionId + 'Chevron');
    if (!body) return;
    body.style.display = isOpen ? '' : 'none';
    if (chevron) chevron.textContent = isOpen ? 'v' : '>';
    try {
      localStorage.setItem('gammaSection.' + sectionId, isOpen ? 'open' : 'closed');
    } catch (error) {
    }
  }

  function toggleSection(sectionId) {
    var body = document.getElementById(sectionId);
    if (!body) return;
    setSectionOpen(sectionId, body.style.display === 'none');
  }

  function initSectionState(sectionId, defaultOpen) {
    var saved = null;
    try {
      saved = localStorage.getItem('gammaSection.' + sectionId);
    } catch (error) {
    }
    setSectionOpen(sectionId, saved ? saved === 'open' : defaultOpen);
  }

  function loadVisionHistory() {
    try {
      var raw = localStorage.getItem('gammaVisionHistory');
      visionHistory = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(visionHistory)) visionHistory = [];
    } catch (error) {
      visionHistory = [];
    }
  }

  function saveVisionHistory() {
    try {
      localStorage.setItem('gammaVisionHistory', JSON.stringify(visionHistory.slice(0, 8)));
    } catch (error) {
    }
  }

  function setViewMode(mode) {
    viewMode = mode;
    localStorage.setItem('gammaDashboardViewMode', mode);
    document.getElementById('viewModeSwitch').checked = mode === 'json';
    if (latestData) {
      renderPanels(latestData);
    }
    postClientLog('set_view_mode', { mode: mode });
  }

  function toggleViewMode() {
    var checked = document.getElementById('viewModeSwitch').checked;
    setViewMode(checked ? 'json' : 'human');
  }

  function renderBlock(elementId, rawValue, humanText) {
    var el = document.getElementById(elementId);
    if (viewMode === 'json') {
      el.textContent = pretty(rawValue);
    } else {
      el.innerHTML = escapeHtml(humanText);
    }
  }

  function stableKey(value) {
    try {
      return JSON.stringify(value);
    } catch (error) {
      return String(value);
    }
  }

  function setTextIfChanged(elementId, value, cacheKey) {
    var key = cacheKey || elementId;
    if (sectionHashes[key] === value) {
      return;
    }
    sectionHashes[key] = value;
    document.getElementById(elementId).textContent = value;
  }

  function renderBlockIfChanged(elementId, rawValue, humanText, cacheKey) {
    var key = cacheKey || elementId;
    var nextKey = viewMode === 'json' ? pretty(rawValue) : humanText;
    if (sectionHashes[key] === nextKey) {
      return;
    }
    sectionHashes[key] = nextKey;
    renderBlock(elementId, rawValue, humanText);
  }

  function updateLiveStatus(text) {
    document.getElementById('liveVoiceStatus').textContent = text;
  }

  function renderLiveMeta(job) {
    liveJobMeta = job || null;
    var target = document.getElementById('liveVoiceMeta');
    if (!target) return;
    if (!job) {
      target.textContent = 'No active live turn.';
      return;
    }
    var lines = [
      'Turn ID: ' + (job.turn_id || 'n/a'),
      'Status: ' + (job.status || 'n/a'),
      'Worker PID: ' + (job.worker_pid || 'n/a')
    ];
    if (job.cancel_reason) lines.push('Cancel reason: ' + job.cancel_reason);
    if (typeof job.cancel_latency_ms !== 'undefined' && job.cancel_latency_ms !== null) lines.push('Cancel latency: ' + fmtDurationMs(job.cancel_latency_ms));
    if (job.started_at) lines.push('Started: ' + fmtLocalDateTime(job.started_at));
    if (job.completed_at) lines.push('Completed: ' + fmtLocalDateTime(job.completed_at));
    target.textContent = lines.join('\n');
  }

  function currentSpeechThreshold() {
    var input = document.getElementById('liveSpeechThreshold');
    if (!input) return LIVE_SPEECH_THRESHOLD;
    return Number(input.value || 18) / 1000;
  }

  function currentSilenceMs() {
    var input = document.getElementById('liveSilenceMs');
    if (!input) return LIVE_SILENCE_MS;
    return Number(input.value || 900);
  }

  function bargeInEnabled() {
    var input = document.getElementById('liveBargeInEnabled');
    return !!(input && input.checked);
  }

  function updateLiveControlLabels() {
    var speech = document.getElementById('liveSpeechThresholdValue');
    var silence = document.getElementById('liveSilenceMsValue');
    if (speech) speech.textContent = currentSpeechThreshold().toFixed(3);
    if (silence) silence.textContent = String(currentSilenceMs());
  }

  function loadLiveControlDefaults() {
    var savedSpeech = localStorage.getItem('gammaLiveSpeechThreshold');
    var savedSilence = localStorage.getItem('gammaLiveSilenceMs');
    var savedBargeIn = localStorage.getItem('gammaLiveBargeIn');
    if (savedSpeech) document.getElementById('liveSpeechThreshold').value = savedSpeech;
    if (savedSilence) document.getElementById('liveSilenceMs').value = savedSilence;
    if (savedBargeIn !== null) document.getElementById('liveBargeInEnabled').checked = savedBargeIn === 'true';
  }

  function persistLiveControlDefaults() {
    localStorage.setItem('gammaLiveSpeechThreshold', document.getElementById('liveSpeechThreshold').value);
    localStorage.setItem('gammaLiveSilenceMs', document.getElementById('liveSilenceMs').value);
    localStorage.setItem('gammaLiveBargeIn', document.getElementById('liveBargeInEnabled').checked ? 'true' : 'false');
    updateLiveControlLabels();
  }

  function drawLiveMeter(level) {
    var canvas = document.getElementById('liveVoiceMeter');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    var width = canvas.width;
    var height = canvas.height;

    liveMeterLevels.push(Math.min(1, Math.max(0, level * 18)));
    if (liveMeterLevels.length > 72) {
      liveMeterLevels.shift();
    }

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#17212d';
    ctx.fillRect(0, 0, width, height);

    var bars = liveMeterLevels.length;
    var gap = 4;
    var barWidth = Math.max(4, Math.floor((width - ((bars + 1) * gap)) / Math.max(1, bars)));
    for (var i = 0; i < bars; i += 1) {
      var value = liveMeterLevels[i];
      var barHeight = Math.max(6, value * (height - 14));
      var x = gap + i * (barWidth + gap);
      var y = Math.round((height - barHeight) / 2);
      var active = value >= (currentSpeechThreshold() * 18);
      ctx.fillStyle = active ? '#33c3b3' : '#6d7f94';
      ctx.fillRect(x, y, barWidth, barHeight);
    }
  }

  function renderLiveHistory() {
    var target = document.getElementById('liveVoiceHistory');
    if (!liveHistory.length) {
      target.textContent = 'No live turns yet.';
      return;
    }
    var lines = [];
    for (var i = Math.max(0, liveHistory.length - 6); i < liveHistory.length; i += 1) {
      var turn = liveHistory[i];
      if (turn.kind === 'event') {
        lines.push('[' + (turn.label || 'event') + '] ' + (turn.detail || ''));
        if (turn.job && turn.job.turn_id) lines.push('Turn ID: ' + turn.job.turn_id);
        if (turn.job && turn.job.cancel_reason) lines.push('Cancel reason: ' + turn.job.cancel_reason);
        if (turn.job && turn.job.cancel_latency_ms !== null && typeof turn.job.cancel_latency_ms !== 'undefined') lines.push('Cancel latency: ' + turn.job.cancel_latency_ms + ' ms');
        lines.push('');
        continue;
      }
      lines.push('You: ' + (turn.transcript || ''));
      lines.push('Shana: ' + (turn.reply_text || ''));
      if (turn.timing_ms) {
        lines.push('Timing: stt ' + (turn.timing_ms.stt_ms || 0) + ' ms | llm+tts ' + (turn.timing_ms.conversation_ms || 0) + ' ms | tts ' + (turn.timing_ms.tts_ms || 0) + ' ms | total ' + (turn.timing_ms.total_ms || 0) + ' ms');
      }
      if (turn.job && turn.job.cancel_reason) {
        lines.push('Cancel reason: ' + turn.job.cancel_reason);
      }
      lines.push('');
    }
    target.textContent = lines.join('\n').trim();
  }

  function updateLiveButton() {
    var button = document.getElementById('liveVoiceButton');
    if (!button) return;
    button.textContent = liveSocket ? 'Live Running' : 'Start Live';
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

  function humanProviders(providers) {
    var lines = [];
    var llm = providers.llm || {};
    var stt = providers.stt || {};
    var tts = providers.tts || {};
    lines.push('LLM: ' + providerLabel(llm.provider) + (llm.model ? ' using ' + llm.model : ''));
    if (llm.endpoint) lines.push('LLM endpoint: ' + llm.endpoint);
    if (llm.health) lines.push('LLM health: ' + fmtHealthStatus(llm.health));
    lines.push('');
    lines.push('STT: ' + providerLabel(stt.provider) + (stt.model ? ' using ' + stt.model : ''));
    if (stt.device) lines.push('STT device: ' + stt.device);
    if (stt.health) lines.push('STT health: ' + fmtHealthStatus(stt.health));
    lines.push('');
    lines.push('TTS running: ' + providerLabel(tts.provider) + (tts.model ? ' using ' + tts.model : ''));
    if (tts.profile_label) lines.push('TTS voice profile: ' + tts.profile_label);
    if (tts.selected_provider) lines.push('TTS saved selection: ' + providerLabel(tts.selected_provider));
    if (tts.selected_profile_label) lines.push('TTS saved profile: ' + tts.selected_profile_label);
    lines.push('TTS restart required: ' + (tts.restart_required ? 'Yes' : 'No'));
    if (tts.endpoint) lines.push('TTS endpoint: ' + tts.endpoint);
    if (typeof tts.rvc_enabled !== 'undefined') lines.push('RVC post-process: ' + (tts.rvc_enabled ? 'Enabled' : 'Disabled'));
    if (tts.rvc_model_name) lines.push('RVC model: ' + tts.rvc_model_name);
    if (typeof tts.rvc_formant !== 'undefined' && tts.rvc_formant !== null) lines.push('RVC formant: ' + tts.rvc_formant);
    if (tts.health) lines.push('TTS health: ' + fmtHealthStatus(tts.health));
    return lines.join('\n');
  }

  function updateTtsControlState(tts) {
    var provider = ((tts && (tts.selected_provider || tts.provider)) || '').toLowerCase();
    var startButton = document.getElementById('ttsStartButton');
    var stopButton = document.getElementById('ttsStopButton');
    var testButton = document.getElementById('ttsTestButton');
    var note = document.getElementById('ttsControlNote');
    var select = document.getElementById('ttsProviderSelect');
    var profileSelect = document.getElementById('ttsProfileSelect');
    var editorShell = document.getElementById('ttsProfileEditorShell');
    var editorStatusShell = document.getElementById('ttsProfileEditorStatusShell');

    function restartNote(nextProvider) {
      var runningProvider = providerLabel(tts.provider);
      var selectedProviderLabel = providerLabel(tts.selected_provider || nextProvider || provider);
      if (selectedProviderLabel === 'GPT-SoVITS') {
        return 'Start GPT-SoVITS, then restart Shana to switch conversations from ' + runningProvider + ' to GPT-SoVITS.';
      }
      if (selectedProviderLabel === 'Qwen3-TTS') {
        return 'Start Qwen3-TTS, then restart Shana to switch conversations from ' + runningProvider + ' to Qwen3-TTS.';
      }
      return 'Restart Shana to switch conversations from ' + runningProvider + ' to ' + selectedProviderLabel + '.';
    }

    if (!startButton || !stopButton || !testButton || !note) return;
    if (editorShell) editorShell.style.display = 'none';
    if (editorStatusShell) editorStatusShell.style.display = 'none';
    if (select) {
      if (tts && Array.isArray(tts.available_providers) && tts.available_providers.length) {
        var providerOptions = [];
        for (var p = 0; p < tts.available_providers.length; p += 1) {
          var providerValue = String(tts.available_providers[p] || '');
          if (!providerValue) continue;
          var selectedProvider = providerValue.toLowerCase() === provider ? ' selected' : '';
          var providerOptionLabel = providerLabel(providerValue);
          providerOptions.push('<option value="' + providerValue + '"' + selectedProvider + '>' + escapeHtml(providerOptionLabel) + '</option>');
        }
        select.innerHTML = providerOptions.join('');
        select.disabled = false;
        if (editorShell) editorShell.style.display = '';
        if (editorStatusShell) editorStatusShell.style.display = '';
      } else if (!provider) {
        select.innerHTML = '<option value="">...</option>';
        select.disabled = true;
      }
    }
    if (profileSelect) {
      profileSelect.disabled = false;
    }
    if (select && provider) {
      select.value = provider;
    }
    if (profileSelect && tts && Array.isArray(tts.available_profiles)) {
      var selectedProfile = tts.selected_profile || '';
      var options = ['<option value="">Default</option>'];
      for (var i = 0; i < tts.available_profiles.length; i += 1) {
        var profile = tts.available_profiles[i];
        var profileProvider = (profile.provider || '').toLowerCase();
        var providerMatch = profileProvider === provider ||
          (isQwenProvider(provider) && isQwenProvider(profileProvider)) ||
          ((provider === 'local' || provider === 'gpt-sovits' || provider === 'gpt_sovits') && (profileProvider === 'local' || profileProvider === 'gpt-sovits' || profileProvider === 'gpt_sovits'));
        if (!providerMatch) {
          continue;
        }
        var selected = profile.id === selectedProfile ? ' selected' : '';
        options.push('<option value="' + profile.id + '"' + selected + '>' + escapeHtml(profile.label + ' (' + profile.provider + ')') + '</option>');
      }
      profileSelect.innerHTML = options.join('');
      profileSelect.disabled = false;
    } else if (profileSelect) {
      profileSelect.innerHTML = '<option value="">...</option>';
      profileSelect.disabled = true;
    }

    startButton.hidden = true;
    stopButton.hidden = true;
    startButton.disabled = false;
    stopButton.disabled = false;
    testButton.disabled = false;
    startButton.textContent = 'Start TTS';
    stopButton.textContent = 'Stop TTS';
    note.textContent = '';

    if (provider === 'local' || provider === 'gpt-sovits' || provider === 'gpt_sovits') {
      startButton.hidden = false;
      stopButton.hidden = false;
      startButton.textContent = 'Start GPT-SoVITS';
      stopButton.textContent = 'Stop GPT-SoVITS';
      if (tts.restart_required) {
        note.textContent = restartNote(provider);
      }
      return;
    }
    if (isQwenProvider(provider)) {
      startButton.hidden = false;
      stopButton.hidden = false;
      startButton.textContent = 'Start Qwen3-TTS';
      stopButton.textContent = 'Stop Qwen3-TTS';
      if (tts.restart_required) {
        note.textContent = restartNote(provider);
      }
      return;
    }

    startButton.disabled = true;
    stopButton.disabled = true;
    if (provider === 'piper') {
      if (tts.restart_required) {
        note.textContent = restartNote(provider);
      } else if (tts.rvc_enabled) {
        note.textContent = 'RVC post-process is enabled for the running Piper stack.';
      }
    } else if (provider === 'openai') {
      if (tts.restart_required) {
        note.textContent = restartNote(provider);
      }
    } else if (provider === 'stub') {
      if (tts.restart_required) {
        note.textContent = restartNote(provider);
      }
    }
  }

  function enableProviderControls() {
    var buttons = document.querySelectorAll('.provider-actions button');
    for (var i = 0; i < buttons.length; i += 1) {
      buttons[i].disabled = false;
    }
    var ttsProvider = document.getElementById('ttsProviderSelect');
    var ttsProfile = document.getElementById('ttsProfileSelect');
    if (ttsProvider) ttsProvider.disabled = false;
    if (ttsProfile) ttsProfile.disabled = false;
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

  function humanRecentTimings(timings) {
    var summary = timings && timings.summary ? timings.summary : {};
    var entries = timings && timings.entries ? timings.entries : [];
    var lines = [];
    lines.push('Entries: ' + (summary.count || 0));
    lines.push('Average total: ' + fmtDurationMs(summary.avg_total_ms));
    lines.push('Fastest: ' + fmtDurationMs(summary.min_total_ms));
    lines.push('Slowest: ' + fmtDurationMs(summary.max_total_ms));
    if (!entries.length) {
      lines.push('');
      lines.push('No timing entries recorded yet.');
      return lines.join('\n');
    }
    lines.push('');
    for (var i = Math.max(0, entries.length - 6); i < entries.length; i += 1) {
      var entry = entries[i];
      var phase = entry.timing_ms || {};
      lines.push(fmtLocalDateTime(entry.timestamp) + '  |  Total: ' + fmtDurationMs(phase.total_ms));
      lines.push('Draft: ' + fmtDurationMs(phase.draft_reply_ms) + ' | Metadata: ' + fmtDurationMs(phase.metadata_ms) + ' | Tools: ' + fmtDurationMs(phase.tool_exec_ms));
      lines.push('Finalizer: ' + fmtDurationMs(phase.finalizer_ms) + ' | Memory: ' + fmtDurationMs(phase.memory_persist_ms) + ' | TTS: ' + fmtDurationMs(phase.tts_ms));
      lines.push('User: ' + (entry.user_text_preview || ''));
      lines.push('');
    }
    return lines.join('\n').trim();
  }

  function humanVoiceRoundtrip(payload) {
    if (!payload) {
      return 'No browser voice roundtrip has run yet.';
    }
    var lines = [
      'Transcript: ' + (payload.transcript || 'n/a'),
      'Reply: ' + (payload.reply_text || 'n/a')
    ];
    var timing = payload.timing_ms || {};
    lines.push('Timing: stt ' + (timing.stt_ms || 0) + ' ms | llm+tts ' + (timing.conversation_ms || 0) + ' ms | tts ' + (timing.tts_ms || 0) + ' ms | total ' + (timing.total_ms || 0) + ' ms');
    lines.push('Audio returned: ' + (payload.audio_base64 ? 'yes' : 'no'));
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

  function humanVisionAnalysis(vision) {
    if (!vision) {
      return 'No vision analysis available.';
    }
    var lines = [
      'Image type: ' + (vision.image_type || 'unknown'),
      'Summary: ' + (vision.summary || 'n/a'),
      'Confidence: ' + (typeof vision.confidence === 'number' ? vision.confidence.toFixed(2) : 'n/a')
    ];
    if (vision.visible_text) {
      lines.push('');
      lines.push('Visible text:');
      lines.push(vision.visible_text);
    }
    if (vision.key_text_blocks && vision.key_text_blocks.length) {
      lines.push('');
      lines.push('Key text blocks:');
      for (var b = 0; b < vision.key_text_blocks.length; b += 1) {
        var block = vision.key_text_blocks[b];
        lines.push('- ' + (block.label || block.block_type || 'text') + ': ' + block.text);
      }
    }
    if (vision.interface_elements && vision.interface_elements.length) {
      lines.push('');
      lines.push('Interface elements:');
      for (var e = 0; e < vision.interface_elements.length; e += 1) {
        var element = vision.interface_elements[e];
        var details = [element.element_type || 'unknown'];
        if (element.role) details.push(element.role);
        if (element.state) details.push(element.state);
        lines.push('- ' + element.name + ' [' + details.join(' | ') + ']');
      }
    }
    if (vision.document_structure && vision.document_structure.length) {
      lines.push('');
      lines.push('Document structure:');
      for (var d = 0; d < vision.document_structure.length; d += 1) {
        lines.push('- ' + vision.document_structure[d]);
      }
    }
    if (vision.likely_actions && vision.likely_actions.length) {
      lines.push('');
      lines.push('Likely actions:');
      for (var a = 0; a < vision.likely_actions.length; a += 1) {
        lines.push('- ' + vision.likely_actions[a]);
      }
    }
    if (vision.objects && vision.objects.length) {
      lines.push('');
      lines.push('Objects:');
      for (var i = 0; i < vision.objects.length; i += 1) {
        var object = vision.objects[i];
        lines.push('- ' + object.name + (object.description ? ': ' + object.description : '') + ' [' + (typeof object.confidence === 'number' ? object.confidence.toFixed(2) : 'n/a') + ']');
      }
    }
    if (vision.spatial_notes && vision.spatial_notes.length) {
      lines.push('');
      lines.push('Spatial notes:');
      for (var j = 0; j < vision.spatial_notes.length; j += 1) {
        lines.push('- ' + vision.spatial_notes[j]);
      }
    }
    if (vision.suggested_follow_ups && vision.suggested_follow_ups.length) {
      lines.push('');
      lines.push('Suggested follow-ups:');
      for (var k = 0; k < vision.suggested_follow_ups.length; k += 1) {
        lines.push('- ' + vision.suggested_follow_ups[k]);
      }
    }
    return lines.join('\n');
  }

  function humanVisionReply(payload) {
    if (!payload) {
      return 'No Gamma vision reply available.';
    }
    var lines = [
      'Reply: ' + (payload.spoken_text || 'n/a'),
      'Emotion: ' + (payload.emotion || 'neutral')
    ];
    if (payload.vision) {
      lines.push('');
      lines.push(humanVisionAnalysis(payload.vision));
    }
    if (payload.audio_path) {
      lines.push('');
      lines.push('Audio artifact: ' + payload.audio_path);
    }
    return lines.join('\n');
  }

  function humanVisionHistory() {
    if (!visionHistory.length) {
      return 'No recent vision history.';
    }
    var lines = [];
    for (var i = 0; i < visionHistory.length; i += 1) {
      var item = visionHistory[i];
      lines.push('[' + fmtLocalDateTime(item.timestamp) + '] ' + (item.kind || 'vision'));
      lines.push('File: ' + (item.file_name || 'n/a'));
      lines.push('Mode: ' + (item.vision_mode || 'auto'));
      lines.push('Prompt: ' + (item.user_text || 'n/a'));
      if (item.summary) lines.push('Summary: ' + item.summary);
      if (item.reply_text) lines.push('Reply: ' + item.reply_text);
      lines.push('');
    }
    return lines.join('\n').trim();
  }

  function renderVisionHistory() {
    renderBlock('visionHistory', visionHistory, humanVisionHistory());
  }

  function pushVisionHistory(entry) {
    visionHistory.unshift(entry);
    if (visionHistory.length > 8) {
      visionHistory = visionHistory.slice(0, 8);
    }
    saveVisionHistory();
    renderVisionHistory();
  }

  function humanMachineMeta(machine) {
    var lines = [
      'Last sample: ' + fmtLocalDateTime(machine.sampled_at),
      'Refresh interval: ' + fmtSeconds(machine.refresh_interval_seconds),
      'GPU polling: ' + (machine.gpu_enabled ? 'Enabled' : 'Disabled')
    ];
    return lines.join('\n');
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

  function isQwenProvider(p) {
    var n = String(p || '').toLowerCase();
    return n === 'qwen-tts' || n === 'qwen_tts' || n === 'qwen' || n === 'qwentts';
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

  function syncJsonFromStructuredFields() {
    if (syncingTtsEditor) return;
    var valuesElement = document.getElementById('ttsEditorValues');
    if (!valuesElement) return;
    var provider = readTtsEditorProvider();
    var parsed = {};
    try {
      parsed = JSON.parse(valuesElement.value.trim() || '{}');
    } catch (error) {
      return;
    }
    var merged = mergeStructuredTtsValues(provider, parsed);
    var nextValue = pretty(merged);
    if (valuesElement.value === nextValue) return;
    syncingTtsEditor = true;
    valuesElement.value = nextValue;
    syncingTtsEditor = false;
  }

  function syncStructuredFieldsFromJson() {
    if (syncingTtsEditor) return;
    var valuesElement = document.getElementById('ttsEditorValues');
    if (!valuesElement) return;
    var parsed = {};
    try {
      parsed = JSON.parse(valuesElement.value.trim() || '{}');
    } catch (error) {
      return;
    }
    syncingTtsEditor = true;
    syncStructuredTtsFields(readTtsEditorProvider(), parsed);
    syncingTtsEditor = false;
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
        qwen_tts_reference_text: 'Exact words spoken in the reference clip.',
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
    lines.push(pretty(ttsProfileExample(provider)));
    return lines.join('\n');
  }

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
      var first = gpu.gpus[0];
      setTextIfChanged('hostGpu', first.utilization_percent + '% / ' + first.memory_used_mb + ' MB');
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

    if (machine && machine.refresh_interval_seconds) {
      runtimePollMs = Math.max(1000, Number(machine.refresh_interval_seconds) * 1000);
    }
  }

  function renderTtsAudioPanel(artifacts) {
    var list = document.getElementById('ttsArtifactList');
    if (!list) return;
    if (!artifacts || !artifacts.length) {
      var empty = '<span style="color:var(--muted);font-size:13px;">No recent audio files.</span>';
      if (list.innerHTML !== empty) list.innerHTML = empty;
      ttsLastArtifactName = null;
      return;
    }
    var newestName = artifacts[0].name;
    var prevName = ttsLastArtifactName;
    ttsLastArtifactName = newestName;
    if (prevName !== null && newestName !== prevName) {
      ttsPlayerLoad(newestName);
    }
    var html = '';
    for (var i = 0; i < artifacts.length; i++) {
      var a = artifacts[i];
      var isActive = ttsPlayerCurrentFile === a.name;
      html += '<div class="tts-artifact-row' + (isActive ? ' active' : '') + '">';
      html += '<div class="tts-artifact-play-area" data-load="' + escapeHtml(a.name) + '">';
      html += '<span class="tts-artifact-icon">' + (isActive ? '&#9646;&#9646;' : '&#9654;') + '</span>';
      html += '<span class="tts-artifact-name">' + escapeHtml(a.name) + '</span>';
      html += '</div>';
      html += '<span class="tts-artifact-meta">' + escapeHtml(fmtArtifactTimestamp(a.name)) + '</span>';
      html += '<span class="tts-artifact-meta">' + fmtBytes(a.size_bytes) + '</span>';
      html += '<button class="tts-artifact-delete" data-delete="' + escapeHtml(a.name) + '">Delete</button>';
      html += '</div>';
    }
    if (list.innerHTML !== html) list.innerHTML = html;
  }

  function ttsPlayerLoad(filename) {
    var audio = document.getElementById('ttsAudioEngine');
    if (!audio) return;
    ttsPlayerCurrentFile = filename;
    var url = '/api/audio/' + encodeURIComponent(filename);
    audio.src = url;
    audio.load();
    var track = document.getElementById('ttsPlayerTrack');
    var playBtn = document.getElementById('ttsPlayerPlayBtn');
    var seekBar = document.getElementById('ttsPlayerSeek');
    var timeEl = document.getElementById('ttsPlayerTime');
    var download = document.getElementById('ttsPlayerDownload');
    if (track) track.textContent = filename;
    if (playBtn) { playBtn.disabled = false; playBtn.textContent = 'Play'; }
    if (seekBar) { seekBar.disabled = false; seekBar.value = 0; }
    if (timeEl) timeEl.textContent = '0:00 / ...';
    if (download) { download.href = url; download.download = filename; download.style.display = ''; }
    if (latestData) renderTtsAudioPanel(latestData.recent_artifacts || []);
  }

  function ttsPlayerTogglePlay() {
    var audio = document.getElementById('ttsAudioEngine');
    if (!audio || !audio.src) return;
    if (audio.paused) {
      audio.play().catch(function () {});
    } else {
      audio.pause();
    }
  }

  function ttsPlayerSeekTo() {
    var audio = document.getElementById('ttsAudioEngine');
    var seekBar = document.getElementById('ttsPlayerSeek');
    if (!audio || !isFinite(audio.duration) || !seekBar) return;
    audio.currentTime = (parseFloat(seekBar.value) / 100) * audio.duration;
  }

  function ttsPlayerSetVolume() {
    var audio = document.getElementById('ttsAudioEngine');
    var volSlider = document.getElementById('ttsPlayerVol');
    var volValue = document.getElementById('ttsPlayerVolValue');
    if (!audio || !volSlider) return;
    var v = parseFloat(volSlider.value);
    audio.volume = v;
    if (volValue) volValue.textContent = Math.round(v * 100) + '%';
  }

  function ttsPlayerSetLoop() {
    var audio = document.getElementById('ttsAudioEngine');
    var loopCheck = document.getElementById('ttsPlayerLoop');
    if (!audio || !loopCheck) return;
    audio.loop = loopCheck.checked;
  }

  function ttsPlayerLoadLatest() {
    if (!latestData || !latestData.recent_artifacts || !latestData.recent_artifacts.length) return;
    ttsPlayerLoad(latestData.recent_artifacts[0].name);
  }

  function ttsPlayerClear() {
    var audio = document.getElementById('ttsAudioEngine');
    if (!audio) return;
    audio.pause();
    audio.src = '';
    ttsPlayerCurrentFile = null;
    var track = document.getElementById('ttsPlayerTrack');
    var playBtn = document.getElementById('ttsPlayerPlayBtn');
    var seekBar = document.getElementById('ttsPlayerSeek');
    var timeEl = document.getElementById('ttsPlayerTime');
    var download = document.getElementById('ttsPlayerDownload');
    if (track) track.textContent = 'No file loaded';
    if (playBtn) { playBtn.textContent = 'Play'; playBtn.disabled = true; }
    if (seekBar) { seekBar.value = 0; seekBar.disabled = true; }
    if (timeEl) timeEl.textContent = '0:00 / 0:00';
    if (download) download.style.display = 'none';
    renderTtsAudioPanel(latestData ? latestData.recent_artifacts || [] : []);
  }

  async function ttsArtifactDelete(filename) {
    try {
      var response = await fetch('/api/audio/' + encodeURIComponent(filename), { method: 'DELETE' });
      if (!response.ok) {
        var payload = await response.json().catch(function () { return {}; });
        postClientLog('tts_delete_error', { filename: filename, status: response.status, detail: payload.detail || '' });
        return;
      }
      if (ttsPlayerCurrentFile === filename) {
        ttsPlayerClear();
      }
      await loadStatus();
    } catch (error) {
      postClientLog('tts_delete_exception', { filename: filename, error: String(error) });
    }
  }

  function renderPanels(data) {
    var process = data.shana && data.shana.process ? data.shana.process : {};
    var systemStatus = data.shana && data.shana.system_status && data.shana.system_status.payload ? data.shana.system_status.payload : {};

    renderRuntimePanels(data);

    renderBlockIfChanged('providers', data.providers || systemStatus.providers || {}, humanProviders(data.providers || systemStatus.providers || {}), 'providers');
    updateTtsControlState((data.providers || systemStatus.providers || {}).tts || {});
    renderTtsProfileEditor((data.providers || systemStatus.providers || {}).tts || {});
    enableProviderControls();

    renderBlockIfChanged(
      'providerActions',
      data.provider_actions || {},
      humanProviderAction(data.provider_actions || {}),
      'providerActions'
    );

    if (!sectionHashes.ttsProfileEditorStatusSaved) {
      renderBlockIfChanged(
        'ttsProfileEditorStatus',
        data.providers && data.providers.tts ? data.providers.tts.editor_profile || {} : {},
        'Edit the active TTS profile, then save to update config/voices.local.toml.',
        'ttsProfileEditorStatusDefault'
      );
    }

    renderBlockIfChanged(
      'memoryStats',
      (systemStatus.memory && systemStatus.memory.stats) || data.memory_db.stats || {},
      humanMemoryStats((systemStatus.memory && systemStatus.memory.stats) || data.memory_db.stats || {}),
      'memoryStats'
    );

    renderBlockIfChanged(
      'knownPeople',
      (systemStatus.memory && systemStatus.memory.known_people) || data.memory_db.known_people || [],
      humanKnownPeople((systemStatus.memory && systemStatus.memory.known_people) || data.memory_db.known_people || []),
      'knownPeople'
    );

    renderBlockIfChanged(
      'recentTimings',
      data.timings || {},
      humanRecentTimings(data.timings || {}),
      'recentTimings'
    );

    renderTtsAudioPanel(data.recent_artifacts || []);

    var logs = data.shana && data.shana.logs ? data.shana.logs : {};
    setTextIfChanged('stdoutLog', process.running ? (logs.stdout_tail || '') : 'Shana is not running. Log panel shows only the current supervised run.', 'stdoutLog');
    setTextIfChanged('stderrLog', process.running ? (logs.stderr_tail || '') : 'Shana is not running. Log panel shows only the current supervised run.', 'stderrLog');
    updateStamp('Last refreshed: ' + new Date().toLocaleString());
  }

  function scheduleStatusRefreshes() {
    loadStatus();
    setTimeout(function () { loadStatus(); }, 350);
    setTimeout(function () { loadStatus(); }, 1000);
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
    } else if (path === '/api/shana/restart') {
      latestData.shana = latestData.shana || {};
      latestData.shana.process = latestData.shana.process || {};
      latestData.shana.process.running = true;
    }

    renderPanels(latestData);
  }

  async function action(path, options) {
    options = options || {};
    try {
      postClientLog('action_start', { path: path });
      applyOptimisticActionState(path);
      var response = await fetch(path, { method: 'POST' });
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
        document.getElementById('backendHealth').textContent = 'Shutdown requested.';
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
      document.getElementById('backendHealth').textContent = 'Dashboard action failed.\n' + String(error);
    }
  }

  function startArtifactPoll(beforeName) {
    if (ttsArtifactPollTimer) clearInterval(ttsArtifactPollTimer);
    var pollCount = 0;
    var maxPolls = 20;
    ttsArtifactPollTimer = setInterval(async function () {
      pollCount++;
      try {
        var response = await fetch('/api/status');
        var data = await response.json();
        var artifacts = data.recent_artifacts || [];
        if (artifacts.length && artifacts[0].name !== beforeName) {
          clearInterval(ttsArtifactPollTimer);
          ttsArtifactPollTimer = null;
          latestData = data;
          renderPanels(data);
          return;
        }
      } catch (e) {}
      if (pollCount >= maxPolls) {
        clearInterval(ttsArtifactPollTimer);
        ttsArtifactPollTimer = null;
      }
    }, 1500);
  }

  async function selectTtsProvider() {
    var select = document.getElementById('ttsProviderSelect');
    if (!select) return;
    try {
      applyOptimisticTtsSelection({
        selected_provider: select.value,
        selected_profile: '',
        selected_profile_label: null,
        restart_required: true,
        editor_profile: {
          id: '',
          label: '',
          provider: select.value,
          description: '',
          values: {}
        }
      });
      syncStructuredFieldsFromJson();
      var response = await fetch('/api/providers/tts/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: select.value })
      });
      var payload = await response.json();
      if (!response.ok) {
        alert(pretty(payload));
        return;
      }
      scheduleStatusRefreshes();
    } catch (error) {
      alert(String(error));
    }
  }

  async function selectTtsProfile() {
    var select = document.getElementById('ttsProfileSelect');
    if (!select) return;
    var selectedOption = select.options[select.selectedIndex];
    var profile = null;
    if (latestData && latestData.providers && latestData.providers.tts && Array.isArray(latestData.providers.tts.available_profiles)) {
      for (var i = 0; i < latestData.providers.tts.available_profiles.length; i += 1) {
        if (latestData.providers.tts.available_profiles[i].id === (select.value || '')) {
          profile = latestData.providers.tts.available_profiles[i];
          break;
        }
      }
    }
    try {
      applyOptimisticTtsSelection({
        selected_profile: select.value || '',
        selected_profile_label: select.value ? selectedOption.text.replace(/\s+\([^)]+\)$/, '') : null,
        restart_required: true,
        editor_profile: profile || {
          id: '',
          label: '',
          provider: document.getElementById('ttsProviderSelect').value || '',
          description: '',
          values: {}
        }
      });
      syncStructuredFieldsFromJson();
      var response = await fetch('/api/providers/tts/profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile: select.value })
      });
      var payload = await response.json();
      if (!response.ok) {
        alert(pretty(payload));
        return;
      }
      scheduleStatusRefreshes();
    } catch (error) {
      alert(String(error));
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
      renderPanels(data);
    } catch (error) {
      postClientLog('load_exception', { error: String(error) });
      updateStamp('Load failed');
      document.getElementById('backendHealth').textContent = 'Dashboard failed to render data.\n' + String(error);
    }
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

  async function saveTtsProfile() {
    try {
      var payload = collectTtsProfilePayload();
      var response = await fetch('/api/providers/tts/profile/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      var result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || ('HTTP ' + response.status));
      }
      sectionHashes.ttsProfileEditorStatusSaved = true;
      renderBlockIfChanged('ttsProfileEditorStatus', result, result.detail || 'TTS profile saved.', 'ttsProfileEditorStatus');
      await loadStatus();
    } catch (error) {
      sectionHashes.ttsProfileEditorStatusSaved = true;
      renderBlockIfChanged('ttsProfileEditorStatus', { error: String(error) }, String(error), 'ttsProfileEditorStatus');
    }
  }

  async function duplicateTtsProfile() {
    try {
      var sourceId = document.getElementById('ttsEditorProfileId').value.trim();
      var nextId = window.prompt('New profile id', sourceId ? (sourceId + '_copy') : '');
      if (!nextId) return;
      var nextLabel = window.prompt('New profile label', document.getElementById('ttsEditorLabel').value.trim() + ' Copy');
      if (!nextLabel) return;
      var payload = collectTtsProfilePayload(nextId.trim(), nextLabel.trim());
      var response = await fetch('/api/providers/tts/profile/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      var result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || ('HTTP ' + response.status));
      }
      sectionHashes.ttsProfileEditorStatusSaved = true;
      renderBlockIfChanged('ttsProfileEditorStatus', result, 'Duplicated as ' + nextId.trim() + '.', 'ttsProfileEditorStatus');
      await loadStatus();
    } catch (error) {
      sectionHashes.ttsProfileEditorStatusSaved = true;
      renderBlockIfChanged('ttsProfileEditorStatus', { error: String(error) }, String(error), 'ttsProfileEditorStatus');
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

  function updateRecordButton() {
    var button = document.getElementById('recordButton');
    if (!button) return;
    button.textContent = mediaRecorder && mediaRecorder.state === 'recording' ? 'Stop Recording' : 'Start Recording';
  }

  function downsampleBuffer(buffer, inputSampleRate, outputSampleRate) {
    if (outputSampleRate === inputSampleRate) {
      return buffer;
    }
    var ratio = inputSampleRate / outputSampleRate;
    var newLength = Math.round(buffer.length / ratio);
    var result = new Float32Array(newLength);
    var offsetResult = 0;
    var offsetBuffer = 0;
    while (offsetResult < result.length) {
      var nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
      var accum = 0;
      var count = 0;
      for (var i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i += 1) {
        accum += buffer[i];
        count += 1;
      }
      result[offsetResult] = count ? (accum / count) : 0;
      offsetResult += 1;
      offsetBuffer = nextOffsetBuffer;
    }
    return result;
  }

  function floatTo16BitPCM(floatBuffer) {
    var output = new Int16Array(floatBuffer.length);
    for (var i = 0; i < floatBuffer.length; i += 1) {
      var sample = Math.max(-1, Math.min(1, floatBuffer[i]));
      output[i] = sample < 0 ? sample * 32768 : sample * 32767;
    }
    return output;
  }

  function rmsLevel(floatBuffer) {
    var sum = 0;
    for (var i = 0; i < floatBuffer.length; i += 1) {
      sum += floatBuffer[i] * floatBuffer[i];
    }
    return Math.sqrt(sum / Math.max(1, floatBuffer.length));
  }

  function maybeOpenLiveTurn(nowMs) {
    if (!liveSocket || liveSocket.readyState !== WebSocket.OPEN || liveTurnOpen || liveAwaitingReply) {
      return;
    }
    liveTurnOpen = true;
    liveTurnStartedAt = nowMs;
    liveLastSpeechAt = nowMs;
    liveSocket.send(JSON.stringify({
      type: 'start_turn',
      session_id: document.getElementById('voiceSessionId').value.trim() || null,
      synthesize_speech: document.getElementById('voiceSynthesizeSpeech').checked
    }));
    updateLiveStatus('Listening...');
  }

  function maybeCloseLiveTurn(nowMs) {
    if (!liveTurnOpen || liveAwaitingReply) {
      return;
    }
    if ((nowMs - liveTurnStartedAt) < LIVE_MIN_TURN_MS) {
      return;
    }
    if ((nowMs - liveLastSpeechAt) < currentSilenceMs()) {
      return;
    }
    liveTurnOpen = false;
    liveAwaitingReply = true;
    liveSocket.send(JSON.stringify({ type: 'end_turn' }));
    updateLiveStatus('Thinking...');
  }

  function interruptLiveReply() {
    var playback = document.getElementById('voicePlayback');
    liveAwaitingReply = false;
    liveTurnOpen = false;
    if (playback) {
      playback.pause();
      playback.removeAttribute('src');
      try { playback.load(); } catch (error) {}
    }
    if (liveSocket && liveSocket.readyState === WebSocket.OPEN) {
      liveSocket.send(JSON.stringify({ type: 'interrupt' }));
    }
    renderLiveMeta(liveJobMeta);
    updateLiveStatus('Interrupted. Listening for new speech.');
  }

  function handleLiveAudio(event) {
    var input = event.inputBuffer.getChannelData(0);
    var level = rmsLevel(input);
    drawLiveMeter(level);
    if (!liveSocket || liveSocket.readyState !== WebSocket.OPEN) {
      return;
    }
    if (liveAwaitingReply && bargeInEnabled() && level >= currentSpeechThreshold()) {
      interruptLiveReply();
    }
    if (liveAwaitingReply) {
      return;
    }
    var nowMs = Date.now();

    if (level >= currentSpeechThreshold()) {
      maybeOpenLiveTurn(nowMs);
      liveLastSpeechAt = nowMs;
    }

    if (!liveTurnOpen) {
      return;
    }

    var downsampled = downsampleBuffer(input, liveAudioContext.sampleRate, LIVE_TARGET_SAMPLE_RATE);
    var pcm = floatTo16BitPCM(downsampled);
    liveSocket.send(pcm.buffer);
    maybeCloseLiveTurn(nowMs);
  }

  async function startLiveVoice() {
    if (liveSocket) {
      return;
    }
    try {
      liveSocket = new WebSocket((window.location.protocol === 'https:' ? 'wss://' : 'ws://') + window.location.host + '/api/voice/live');
      liveSocket.onopen = async function () {
        try {
          liveMediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
          liveAudioContext = new (window.AudioContext || window.webkitAudioContext)();
          liveSourceNode = liveAudioContext.createMediaStreamSource(liveMediaStream);
          liveProcessorNode = liveAudioContext.createScriptProcessor(4096, 1, 1);
          liveProcessorNode.onaudioprocess = handleLiveAudio;
          liveSourceNode.connect(liveProcessorNode);
          liveProcessorNode.connect(liveAudioContext.destination);
          updateLiveStatus('Live voice is armed. Start speaking.');
          updateLiveButton();
        } catch (error) {
          updateLiveStatus('Live microphone error: ' + String(error));
          stopLiveVoice();
        }
      };
      liveSocket.onmessage = function (event) {
        var payload = {};
        try {
          payload = JSON.parse(event.data);
        } catch (error) {
          updateLiveStatus('Live voice message parse failed.');
          return;
        }
        if (payload.type === 'state') {
          if (payload.state === 'idle' && !liveAwaitingReply) {
            updateLiveStatus(payload.detail || 'Live voice is idle.');
          } else if (payload.state === 'interrupted') {
            updateLiveStatus(payload.detail || 'Interrupted.');
          } else if (payload.detail) {
            updateLiveStatus(payload.detail);
          }
          renderLiveMeta(payload.job || null);
          if (payload.state === 'cancelled' || payload.state === 'interrupted' || payload.state === 'failed') {
            liveHistory.push({
              kind: 'event',
              label: payload.state,
              detail: payload.detail || payload.state,
              job: payload.job || null
            });
            renderLiveHistory();
          }
          return;
        }
        if (payload.type === 'partial_transcript') {
          updateLiveStatus('Hearing: ' + (payload.text || '...'));
          return;
        }
        if (payload.type === 'transcript') {
          updateLiveStatus('Transcript ready.');
          return;
        }
        if (payload.type === 'turn_result') {
          renderLiveMeta(payload.job || null);
          liveHistory.push(payload);
          renderLiveHistory();
          renderBlock('voiceRoundtripStatus', payload, humanVoiceRoundtrip(payload));
          if (payload.audio_base64 && payload.audio_content_type) {
            var playback = document.getElementById('voicePlayback');
            playback.src = 'data:' + payload.audio_content_type + ';base64,' + payload.audio_base64;
            updateLiveStatus('Speaking...');
            playback.onended = function () {
              liveAwaitingReply = false;
              updateLiveStatus('Live voice is armed. Start speaking.');
            };
            playback.play().catch(function () {
              liveAwaitingReply = false;
              updateLiveStatus('Reply ready. Start speaking.');
            });
          } else {
            liveAwaitingReply = false;
            updateLiveStatus('Reply ready. Start speaking.');
          }
          return;
        }
        if (payload.type === 'error') {
          liveAwaitingReply = false;
          liveTurnOpen = false;
          renderLiveMeta(payload.job || null);
          updateLiveStatus(payload.detail || 'Live voice error.');
          return;
        }
      };
      liveSocket.onclose = function () {
        cleanupLiveAudio();
        liveSocket = null;
        liveAwaitingReply = false;
        liveTurnOpen = false;
        updateLiveButton();
        renderLiveMeta(null);
        updateLiveStatus('Live voice session stopped.');
        drawLiveMeter(0);
      };
      liveSocket.onerror = function () {
        updateLiveStatus('Live voice socket error.');
      };
    } catch (error) {
      updateLiveStatus('Failed to start live voice: ' + String(error));
      stopLiveVoice();
    }
  }

  function cleanupLiveAudio() {
    if (liveProcessorNode) {
      liveProcessorNode.disconnect();
      liveProcessorNode.onaudioprocess = null;
      liveProcessorNode = null;
    }
    if (liveSourceNode) {
      liveSourceNode.disconnect();
      liveSourceNode = null;
    }
    if (liveMediaStream) {
      liveMediaStream.getTracks().forEach(function (track) { track.stop(); });
      liveMediaStream = null;
    }
    if (liveAudioContext) {
      liveAudioContext.close().catch(function () {});
      liveAudioContext = null;
    }
  }

  function stopLiveVoice() {
    if (liveSocket && liveSocket.readyState === WebSocket.OPEN) {
      if (liveTurnOpen) {
        liveSocket.send(JSON.stringify({ type: 'cancel_turn' }));
      }
      liveSocket.close();
    } else {
      cleanupLiveAudio();
      liveSocket = null;
      liveAwaitingReply = false;
      liveTurnOpen = false;
      updateLiveButton();
      renderLiveMeta(null);
      updateLiveStatus('Live voice session stopped.');
      drawLiveMeter(0);
    }
  }

  function toggleLiveVoice() {
    if (liveSocket) {
      stopLiveVoice();
      return;
    }
    startLiveVoice();
  }

  async function toggleVoiceRecording() {
    try {
      if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        return;
      }
      var stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordedChunks = [];
      var mimeTypes = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
      recordedMimeType = '';
      for (var i = 0; i < mimeTypes.length; i += 1) {
        if (window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(mimeTypes[i])) {
          recordedMimeType = mimeTypes[i];
          break;
        }
      }
      mediaRecorder = recordedMimeType ? new MediaRecorder(stream, { mimeType: recordedMimeType }) : new MediaRecorder(stream);
      mediaRecorder.ondataavailable = function (event) {
        if (event.data && event.data.size > 0) {
          recordedChunks.push(event.data);
        }
      };
      mediaRecorder.onstop = function () {
        recordedBlob = new Blob(recordedChunks, { type: recordedMimeType || 'audio/webm' });
        document.getElementById('voiceRoundtripStatus').textContent = 'Recorded clip ready to send.';
        if (mediaRecorder.stream) {
          mediaRecorder.stream.getTracks().forEach(function (track) { track.stop(); });
        }
        updateRecordButton();
      };
      mediaRecorder.start();
      document.getElementById('voiceRoundtripStatus').textContent = 'Recording...';
      updateRecordButton();
    } catch (error) {
      document.getElementById('voiceRoundtripStatus').textContent = 'Microphone error: ' + String(error);
    }
  }

  async function sendRecordedVoice() {
    if (!recordedBlob) {
      document.getElementById('voiceRoundtripStatus').textContent = 'Record something first.';
      return;
    }
    var formData = new FormData();
    var extension = 'webm';
    if ((recordedMimeType || '').indexOf('mp4') !== -1) extension = 'm4a';
    formData.append('audio_file', recordedBlob, 'browser-recording.' + extension);
    var sessionId = document.getElementById('voiceSessionId').value.trim();
    if (sessionId) formData.append('session_id', sessionId);
    formData.append('synthesize_speech', document.getElementById('voiceSynthesizeSpeech').checked ? 'true' : 'false');
    document.getElementById('voiceRoundtripStatus').textContent = 'Sending audio...';
    try {
      var response = await fetch('/api/voice/roundtrip', { method: 'POST', body: formData });
      if (!response.ok) {
        throw new Error('HTTP ' + response.status);
      }
      var payload = await response.json();
      renderBlock('voiceRoundtripStatus', payload, humanVoiceRoundtrip(payload));
      if (payload.audio_base64 && payload.audio_content_type) {
        var playback = document.getElementById('voicePlayback');
        playback.src = 'data:' + payload.audio_content_type + ';base64,' + payload.audio_base64;
        playback.play().catch(function () {});
      }
    } catch (error) {
      document.getElementById('voiceRoundtripStatus').textContent = 'Voice roundtrip failed.\n' + String(error);
    }
  }

  function updateVisionImageMeta() {
    var target = document.getElementById('visionImageMeta');
    var preview = document.getElementById('visionImagePreview');
    if (!selectedVisionFile) {
      target.textContent = 'No image selected.';
      preview.hidden = true;
      preview.removeAttribute('src');
      return;
    }
    target.textContent = 'Selected: ' + selectedVisionFile.name + '\nType: ' + (selectedVisionFile.type || 'unknown') + '\nSize: ' + fmtBytes(selectedVisionFile.size);
    preview.hidden = !selectedVisionPreviewUrl;
    if (selectedVisionPreviewUrl) {
      preview.src = selectedVisionPreviewUrl;
    }
  }

  function buildVisionFormData() {
    if (!selectedVisionFile) {
      throw new Error('Choose an image first.');
    }
    var formData = new FormData();
    formData.append('image_file', selectedVisionFile, selectedVisionFile.name);
    formData.append('user_text', document.getElementById('visionPrompt').value.trim() || 'Tell me what is important in this image.');
    formData.append('vision_mode', document.getElementById('visionMode').value || 'auto');
    return formData;
  }

  async function analyzeVisionImage() {
    try {
      var formData = buildVisionFormData();
      var prompt = document.getElementById('visionPrompt').value.trim() || 'Tell me what is important in this image.';
      var mode = document.getElementById('visionMode').value || 'auto';
      document.getElementById('visionStatus').textContent = 'Analyzing image...';
      var response = await fetch('/api/vision/analyze', { method: 'POST', body: formData });
      var payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || ('HTTP ' + response.status));
      }
      renderBlock('visionResult', payload, humanVisionAnalysis(payload));
      document.getElementById('visionStatus').textContent = 'Vision analysis complete.';
      pushVisionHistory({
        timestamp: new Date().toLocaleString(),
        kind: 'analyze',
        file_name: selectedVisionFile ? selectedVisionFile.name : 'n/a',
        vision_mode: mode,
        user_text: prompt,
        summary: payload.summary || null
      });
    } catch (error) {
      document.getElementById('visionStatus').textContent = 'Vision analysis failed.\n' + String(error);
    }
  }

  async function askGammaAboutImage() {
    try {
      var formData = buildVisionFormData();
      var prompt = document.getElementById('visionPrompt').value.trim() || 'Tell me what is important in this image.';
      var mode = document.getElementById('visionMode').value || 'auto';
      var sessionId = document.getElementById('visionSessionId').value.trim();
      if (sessionId) formData.append('session_id', sessionId);
      formData.append('synthesize_speech', document.getElementById('visionSynthesizeSpeech').checked ? 'true' : 'false');
      document.getElementById('visionStatus').textContent = 'Sending image to Gamma...';
      var response = await fetch('/api/vision/respond', { method: 'POST', body: formData });
      var payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || ('HTTP ' + response.status));
      }
      renderBlock('visionResult', payload, humanVisionReply(payload));
      document.getElementById('visionStatus').textContent = 'Gamma vision reply ready.';
      pushVisionHistory({
        timestamp: new Date().toLocaleString(),
        kind: 'ask',
        file_name: selectedVisionFile ? selectedVisionFile.name : 'n/a',
        vision_mode: mode,
        user_text: prompt,
        summary: payload.vision && payload.vision.summary ? payload.vision.summary : null,
        reply_text: payload.spoken_text || null
      });
    } catch (error) {
      document.getElementById('visionStatus').textContent = 'Gamma vision request failed.\n' + String(error);
    }
  }

  window.onerror = function (message, source, lineno, colno, error) {
    postClientLog('window_error', {
      message: String(message),
      source: source || null,
      line: lineno || null,
      column: colno || null,
      error: error ? String(error) : null
    });
  };

  window.onunhandledrejection = function (event) {
    postClientLog('unhandled_rejection', {
      reason: event && event.reason ? String(event.reason) : 'unknown'
    });
  };

  window.toggleViewMode = toggleViewMode;
  window.action = action;
  window.loadStatus = loadStatus;
  window.toggleSection = toggleSection;
  window.selectTtsProvider = selectTtsProvider;
  window.selectTtsProfile = selectTtsProfile;
  window.saveTtsProfile = saveTtsProfile;
  window.duplicateTtsProfile = duplicateTtsProfile;
  window.toggleVoiceRecording = toggleVoiceRecording;
  window.sendRecordedVoice = sendRecordedVoice;
  window.toggleLiveVoice = toggleLiveVoice;
  window.stopLiveVoice = stopLiveVoice;
  window.analyzeVisionImage = analyzeVisionImage;
  window.askGammaAboutImage = askGammaAboutImage;
  window.ttsPlayerLoad = ttsPlayerLoad;
  window.ttsPlayerTogglePlay = ttsPlayerTogglePlay;
  window.ttsPlayerSeekTo = ttsPlayerSeekTo;
  window.ttsPlayerSetVolume = ttsPlayerSetVolume;
  window.ttsPlayerSetLoop = ttsPlayerSetLoop;
  window.ttsPlayerLoadLatest = ttsPlayerLoadLatest;
  window.ttsPlayerClear = ttsPlayerClear;
  window.ttsArtifactDelete = ttsArtifactDelete;

  postClientLog('script_boot', { viewMode: viewMode });
  setViewMode(viewMode);
  loadLiveControlDefaults();
  initSectionState('ttsProfileEditorPanel', false);
  initSectionState('ttsAudioPanel', false);
  initSectionState('browserVoicePanel', false);

  var _ttsArtifactList = document.getElementById('ttsArtifactList');
  if (_ttsArtifactList) {
    _ttsArtifactList.addEventListener('click', function (e) {
      var playEl = e.target.closest('[data-load]');
      if (playEl) { ttsPlayerLoad(playEl.getAttribute('data-load')); return; }
      var delEl = e.target.closest('[data-delete]');
      if (delEl) { ttsArtifactDelete(delEl.getAttribute('data-delete')); }
    });
  }

  var _ttsAudio = document.getElementById('ttsAudioEngine');
  if (_ttsAudio) {
    _ttsAudio.addEventListener('error', function () {
      var errEl = document.getElementById('ttsPlayerError');
      var codes = { 1: 'aborted', 2: 'network error', 3: 'decode error', 4: 'source not supported' };
      var code = _ttsAudio.error ? (_ttsAudio.error.code || 0) : 0;
      var msg = 'Audio failed to load' + (codes[code] ? ': ' + codes[code] : '') + '. Dashboard may need a restart to serve audio files.';
      if (errEl) { errEl.textContent = msg; errEl.style.display = ''; }
      var playBtn = document.getElementById('ttsPlayerPlayBtn');
      if (playBtn) { playBtn.textContent = 'Play'; playBtn.disabled = true; }
    });
    _ttsAudio.addEventListener('loadedmetadata', function () {
      var seekBar = document.getElementById('ttsPlayerSeek');
      var timeEl = document.getElementById('ttsPlayerTime');
      var errEl = document.getElementById('ttsPlayerError');
      if (seekBar) { seekBar.value = 0; seekBar.disabled = false; }
      if (timeEl) timeEl.textContent = '0:00 / ' + fmtTime(_ttsAudio.duration);
      if (errEl) { errEl.textContent = ''; errEl.style.display = 'none'; }
    });
    _ttsAudio.addEventListener('timeupdate', function () {
      if (ttsPlayerSeeking) return;
      var seekBar = document.getElementById('ttsPlayerSeek');
      var timeEl = document.getElementById('ttsPlayerTime');
      if (seekBar && isFinite(_ttsAudio.duration) && _ttsAudio.duration > 0) {
        seekBar.value = (_ttsAudio.currentTime / _ttsAudio.duration) * 100;
      }
      if (timeEl) timeEl.textContent = fmtTime(_ttsAudio.currentTime) + ' / ' + fmtTime(_ttsAudio.duration);
    });
    _ttsAudio.addEventListener('play', function () {
      var btn = document.getElementById('ttsPlayerPlayBtn');
      if (btn) btn.textContent = 'Pause';
    });
    _ttsAudio.addEventListener('pause', function () {
      var btn = document.getElementById('ttsPlayerPlayBtn');
      if (btn) btn.textContent = 'Play';
    });
    _ttsAudio.addEventListener('ended', function () {
      var btn = document.getElementById('ttsPlayerPlayBtn');
      if (btn && !_ttsAudio.loop) btn.textContent = 'Play';
    });
    var _ttsSeek = document.getElementById('ttsPlayerSeek');
    if (_ttsSeek) {
      _ttsSeek.addEventListener('mousedown', function () { ttsPlayerSeeking = true; });
      _ttsSeek.addEventListener('touchstart', function () { ttsPlayerSeeking = true; }, { passive: true });
      _ttsSeek.addEventListener('mouseup', function () { ttsPlayerSeeking = false; });
      _ttsSeek.addEventListener('touchend', function () { ttsPlayerSeeking = false; });
    }
  }
  initSectionState('visionPanel', false);
  initSectionState('stdoutPanel', false);
  initSectionState('stderrPanel', false);
  document.getElementById('liveSpeechThreshold').addEventListener('input', persistLiveControlDefaults);
  document.getElementById('liveSilenceMs').addEventListener('input', persistLiveControlDefaults);
  document.getElementById('liveBargeInEnabled').addEventListener('change', persistLiveControlDefaults);
  updateRecordButton();
  updateLiveButton();
  updateLiveControlLabels();
  renderLiveMeta(null);
  renderLiveHistory();
  drawLiveMeter(0);
  document.getElementById('visionImageFile').addEventListener('change', function (event) {
    if (selectedVisionPreviewUrl) {
      URL.revokeObjectURL(selectedVisionPreviewUrl);
      selectedVisionPreviewUrl = null;
    }
    selectedVisionFile = event.target.files && event.target.files[0] ? event.target.files[0] : null;
    if (selectedVisionFile) {
      selectedVisionPreviewUrl = URL.createObjectURL(selectedVisionFile);
    }
    updateVisionImageMeta();
  });
  loadVisionHistory();
  updateVisionImageMeta();
  renderVisionHistory();
  [
    'ttsEditorPiperModelPath',
    'ttsEditorPiperConfigPath',
    'ttsEditorRvcEnabled',
    'ttsEditorRvcModelName',
    'ttsEditorSovitsReferenceAudio',
    'ttsEditorSovitsPromptText',
    'ttsEditorOpenAiModel',
    'ttsEditorOpenAiVoice',
    'ttsEditorOpenAiFormat'
  ].forEach(function (id) {
    var element = document.getElementById(id);
    if (!element) return;
    var eventName = element.tagName === 'INPUT' && element.type === 'checkbox' ? 'change' : 'input';
    element.addEventListener(eventName, syncJsonFromStructuredFields);
  });
  document.getElementById('ttsEditorValues').addEventListener('input', syncStructuredFieldsFromJson);
  loadStatus();
  scheduleRuntimePoll();
}());
