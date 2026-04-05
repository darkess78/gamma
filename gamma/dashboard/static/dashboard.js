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
  var LIVE_TARGET_SAMPLE_RATE = 16000;
  var LIVE_SPEECH_THRESHOLD = 0.018;
  var LIVE_SILENCE_MS = 900;
  var LIVE_MIN_TURN_MS = 550;
  var liveMeterLevels = [];

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

  function updateStamp(text) {
    document.getElementById('stamp').textContent = text;
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
    if (typeof job.cancel_latency_ms !== 'undefined' && job.cancel_latency_ms !== null) lines.push('Cancel latency: ' + job.cancel_latency_ms + ' ms');
    if (job.started_at) lines.push('Started: ' + job.started_at);
    if (job.completed_at) lines.push('Completed: ' + job.completed_at);
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
        lines.push('Timing: stt ' + (turn.timing_ms.stt_ms || 0) + ' ms | conversation ' + (turn.timing_ms.conversation_ms || 0) + ' ms | total ' + (turn.timing_ms.total_ms || 0) + ' ms');
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
    var lines = [
      'Shana URL: ' + data.shana.url,
      'API reachable: ' + (health.ok ? 'yes' : 'no')
    ];
    if (health.detail) lines.push('Detail: ' + health.detail);
    return lines.join('\n');
  }

  function humanProviders(providers) {
    var lines = [];
    var llm = providers.llm || {};
    var stt = providers.stt || {};
    var tts = providers.tts || {};
    lines.push('LLM: ' + (llm.provider || 'n/a') + ' (' + (llm.model || 'n/a') + ')');
    if (llm.endpoint) lines.push('LLM endpoint: ' + llm.endpoint);
    if (llm.health) lines.push('LLM health: ' + (llm.health.ok ? 'ok' : (llm.health.detail || 'down')));
    lines.push('');
    lines.push('STT: ' + (stt.provider || 'n/a') + ' (' + (stt.model || 'n/a') + ') on ' + (stt.device || 'n/a'));
    lines.push('');
    lines.push('TTS: ' + (tts.provider || 'n/a') + ' (' + (tts.model || 'n/a') + ')');
    if (tts.endpoint) lines.push('TTS endpoint: ' + tts.endpoint);
    if (tts.health) lines.push('TTS health: ' + (tts.health.ok ? 'ok' : (tts.health.detail || 'down')));
    return lines.join('\n');
  }

  function humanProviderAction(action) {
    if (!action || !action.status) {
      return 'No provider action has been run yet.';
    }
    var lines = [
      'Action: ' + (action.action || 'n/a'),
      'Status: ' + action.status,
      'Detail: ' + (action.detail || 'n/a'),
      'Ran at: ' + (action.ran_at || 'n/a')
    ];
    if (typeof action.returncode !== 'undefined') {
      lines.push('Return code: ' + action.returncode);
    }
    if (action.stdout) {
      lines.push('');
      lines.push('stdout:');
      lines.push(action.stdout);
    }
    if (action.stderr) {
      lines.push('');
      lines.push('stderr:');
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
    lines.push('Average total: ' + (summary.avg_total_ms || 'n/a') + ' ms');
    lines.push('Fastest: ' + (summary.min_total_ms || 'n/a') + ' ms');
    lines.push('Slowest: ' + (summary.max_total_ms || 'n/a') + ' ms');
    if (!entries.length) {
      lines.push('');
      lines.push('No timing entries recorded yet.');
      return lines.join('\n');
    }
    lines.push('');
    for (var i = Math.max(0, entries.length - 6); i < entries.length; i += 1) {
      var entry = entries[i];
      var phase = entry.timing_ms || {};
      lines.push('[' + (entry.timestamp || 'n/a') + '] ' + (phase.total_ms || 'n/a') + ' ms total');
      lines.push('  draft ' + (phase.draft_reply_ms || 0) + ' | metadata ' + (phase.metadata_ms || 0) + ' | tools ' + (phase.tool_exec_ms || 0) + ' | finalizer ' + (phase.finalizer_ms || 0) + ' | memory ' + (phase.memory_persist_ms || 0) + ' | tts ' + (phase.tts_ms || 0));
      lines.push('  user: ' + (entry.user_text_preview || ''));
    }
    return lines.join('\n');
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
    lines.push('Timing: stt ' + (timing.stt_ms || 0) + ' ms | conversation ' + (timing.conversation_ms || 0) + ' ms | total ' + (timing.total_ms || 0) + ' ms');
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

  function humanMachineMeta(machine) {
    var lines = [
      'Last sample: ' + (machine.sampled_at || 'n/a'),
      'Refresh interval: ' + (machine.refresh_interval_seconds || 'n/a') + 's',
      'GPU polling: ' + (machine.gpu_enabled ? 'enabled' : 'disabled')
    ];
    return lines.join('\n');
  }

  function renderPanels(data) {
    var process = data.shana && data.shana.process ? data.shana.process : {};
    var health = data.shana && data.shana.api_health ? data.shana.api_health : {};
    var systemStatus = data.shana && data.shana.system_status && data.shana.system_status.payload ? data.shana.system_status.payload : {};
    var machine = data.machine || {};
    var gpu = machine.gpu || {};

    document.getElementById('running').textContent = process.running ? 'Yes' : 'No';
    document.getElementById('pid').textContent = process.pid || 'n/a';
    document.getElementById('procCpu').textContent = process.running ? ((process.cpu_percent || 0).toFixed(1) + '%') : 'n/a';
    document.getElementById('procMem').textContent = process.running ? fmtBytes(process.rss_bytes) : 'n/a';
    document.getElementById('hostCpu').textContent = ((machine.cpu_percent || 0).toFixed(1) + '%');
    document.getElementById('hostRam').textContent = machine.memory ? (machine.memory.percent.toFixed(1) + '%') : 'n/a';
    document.getElementById('hostDisk').textContent = machine.disk ? (machine.disk.percent.toFixed(1) + '%') : 'n/a';
    if (gpu.ok && gpu.gpus && gpu.gpus.length) {
      var first = gpu.gpus[0];
      document.getElementById('hostGpu').textContent = first.utilization_percent + '% / ' + first.memory_used_mb + ' MB';
    } else {
      document.getElementById('hostGpu').textContent = gpu.detail || 'n/a';
    }

    renderBlock('machineMeta', {
      sampled_at: machine.sampled_at || null,
      refresh_interval_seconds: machine.refresh_interval_seconds || null,
      gpu_enabled: typeof machine.gpu_enabled === 'undefined' ? null : machine.gpu_enabled
    }, humanMachineMeta(machine));

    renderBlock('backendHealth', {
      shana_url: data.shana.url,
      api_health: health
    }, humanBackendHealth(data, health));

    renderBlock('providers', data.providers || systemStatus.providers || {}, humanProviders(data.providers || systemStatus.providers || {}));

    renderBlock(
      'providerActions',
      data.provider_actions || {},
      humanProviderAction(data.provider_actions || {})
    );

    renderBlock(
      'memoryStats',
      (systemStatus.memory && systemStatus.memory.stats) || data.memory_db.stats || {},
      humanMemoryStats((systemStatus.memory && systemStatus.memory.stats) || data.memory_db.stats || {})
    );

    renderBlock(
      'knownPeople',
      (systemStatus.memory && systemStatus.memory.known_people) || data.memory_db.known_people || [],
      humanKnownPeople((systemStatus.memory && systemStatus.memory.known_people) || data.memory_db.known_people || [])
    );

    renderBlock(
      'recentTimings',
      data.timings || {},
      humanRecentTimings(data.timings || {})
    );

    var logs = data.shana && data.shana.logs ? data.shana.logs : {};
    document.getElementById('stdoutLog').textContent = process.running ? (logs.stdout_tail || '') : 'Shana is not running. Log panel shows only the current supervised run.';
    document.getElementById('stderrLog').textContent = process.running ? (logs.stderr_tail || '') : 'Shana is not running. Log panel shows only the current supervised run.';
    updateStamp('Last refreshed: ' + new Date().toLocaleString());
  }

  async function action(path, options) {
    options = options || {};
    try {
      postClientLog('action_start', { path: path });
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
      await loadStatus();
    } catch (error) {
      postClientLog('action_exception', { path: path, error: String(error) });
      updateStamp('Action failed');
      document.getElementById('backendHealth').textContent = 'Dashboard action failed.\n' + String(error);
    }
  }

  async function loadStatus() {
    try {
      updateStamp('Loading...');
      postClientLog('load_start', { at: new Date().toISOString() });
      var response = await fetch('/api/status');
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
  window.toggleVoiceRecording = toggleVoiceRecording;
  window.sendRecordedVoice = sendRecordedVoice;
  window.toggleLiveVoice = toggleLiveVoice;
  window.stopLiveVoice = stopLiveVoice;

  postClientLog('script_boot', { viewMode: viewMode });
  setViewMode(viewMode);
  loadLiveControlDefaults();
  document.getElementById('liveSpeechThreshold').addEventListener('input', persistLiveControlDefaults);
  document.getElementById('liveSilenceMs').addEventListener('input', persistLiveControlDefaults);
  document.getElementById('liveBargeInEnabled').addEventListener('change', persistLiveControlDefaults);
  updateRecordButton();
  updateLiveButton();
  updateLiveControlLabels();
  renderLiveMeta(null);
  renderLiveHistory();
  drawLiveMeter(0);
  loadStatus();
  setInterval(loadStatus, 15000);
}());
