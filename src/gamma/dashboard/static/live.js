// live.js - Live voice functionality for Gamma dashboard
(function () {
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
  var livePlaybackQueue = [];
  var livePlaybackSeenChunks = {};
  var livePlaybackActive = false;
  var liveReplyCompleted = false;
  var liveCurrentChunk = null;
  var liveCurrentChunkStartedAt = 0;
  var liveCurrentChunkWatchdog = 0;
  var liveInterruptSpeechStartedAt = 0;
  var liveInterruptProbePending = false;
  var liveInterruptProbeChunks = [];
  var liveInterruptProbeBytes = 0;
  var selectedVisionFile = null;
  var selectedVisionPreviewUrl = null;
  var subtitlePopup = null;
  var subtitleState = { transcript: '', reply: '', partial: '' };
  var LIVE_TARGET_SAMPLE_RATE = 16000;
  var LIVE_SPEECH_THRESHOLD = 0.018;
  var LIVE_SILENCE_MS = 900;
  var LIVE_MIN_TURN_MS = 550;
  var liveSpeakerMuted = false;
  var liveMicMuted = false;
  var liveLastInterruptAt = 0;
  var liveLastLevel = 0;
  var liveLastEvent = 'Session has not started.';
  var recordMediaStream = null;
  var recordAudioContext = null;
  var recordSourceNode = null;
  var recordProcessorNode = null;
  var recordedSamples = [];
  var recordedBlob = null;
  var recordedMimeType = 'audio/wav';

  function syncLivePlaybackMute() {
    var playback = document.getElementById('voicePlayback');
    if (playback) playback.muted = liveSpeakerMuted;
    updateMuteButtons();
  }

  function updateMuteButtons() {
    var speakerButton = document.getElementById('liveSpeakerMuteButton');
    var micButton = document.getElementById('liveMicMuteButton');
    if (speakerButton) {
      speakerButton.textContent = liveSpeakerMuted ? 'Unmute Shana' : 'Mute Shana';
      speakerButton.setAttribute('data-muted', liveSpeakerMuted ? 'true' : 'false');
      speakerButton.classList.remove('secondary', 'danger', 'info', 'warn');
      speakerButton.classList.add(liveSpeakerMuted ? 'danger' : 'secondary');
    }
    if (micButton) {
      micButton.textContent = liveMicMuted ? 'Unmute Mic' : 'Mute Mic';
      micButton.setAttribute('data-muted', liveMicMuted ? 'true' : 'false');
      micButton.classList.remove('secondary', 'danger', 'info', 'warn');
      micButton.classList.add(liveMicMuted ? 'danger' : 'secondary');
    }
  }

  function toggleLiveSpeakerMuted() {
    liveSpeakerMuted = !liveSpeakerMuted;
    syncLivePlaybackMute();
    updateLiveStatus(liveSpeakerMuted ? 'Speaker muted.' : 'Speaker unmuted.');
  }

  function toggleLiveMicMuted() {
    liveMicMuted = !liveMicMuted;
    updateMuteButtons();
    updateLiveStatus(liveMicMuted ? 'Microphone muted.' : 'Microphone unmuted.');
  }

  function updateLiveStatus(text) {
    var status = document.getElementById('liveVoiceStatus');
    if (status) status.textContent = text;
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

  function currentInterruptSpeechMs() {
    var input = document.getElementById('liveInterruptSpeechMs');
    if (!input) return 260;
    return Number(input.value || 260);
  }

  function currentMinimumTurnMs() {
    var input = document.getElementById('liveMinimumTurnMs');
    return input ? Number(input.value || LIVE_MIN_TURN_MS) : LIVE_MIN_TURN_MS;
  }

  function currentBargeCooldownMs() {
    var input = document.getElementById('liveBargeCooldownMs');
    return input ? Number(input.value || 750) : 750;
  }

  function bargeInEnabled() {
    var input = document.getElementById('liveBargeInEnabled');
    return !!(input && input.checked);
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
      synthesize_speech: document.getElementById('voiceSynthesizeSpeech').checked,
      response_mode: currentLiveResponseMode()
    }));
    updateLiveStatus('Listening...');
  }

  function maybeCloseLiveTurn(nowMs) {
    if (!liveTurnOpen || liveAwaitingReply) {
      return;
    }
    if ((nowMs - liveTurnStartedAt) < currentMinimumTurnMs()) {
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
    resetLivePlayback(true);
    if (playback) {
      playback.pause();
      playback.removeAttribute('src');
      try { playback.load(); } catch (error) {}
    }
    if (liveSocket && liveSocket.readyState === WebSocket.OPEN) {
      liveSocket.send(JSON.stringify({ type: 'interrupt' }));
    }
    liveLastInterruptAt = Date.now();
    renderLiveMeta(liveJobMeta);
    updateLiveStatus('Interrupted. Listening for new speech.');
  }

  function resetLivePlayback(stopAudio) {
    livePlaybackQueue = [];
    livePlaybackSeenChunks = {};
    livePlaybackActive = false;
    liveReplyCompleted = false;
    liveCurrentChunk = null;
    liveCurrentChunkStartedAt = 0;
    clearLiveChunkWatchdog();
    liveInterruptSpeechStartedAt = 0;
    liveInterruptProbePending = false;
    liveInterruptProbeChunks = [];
    liveInterruptProbeBytes = 0;
    setSubtitleState({ transcript: '', reply: '', partial: '' });
    if (!stopAudio) {
      return;
    }
    var playback = document.getElementById('voicePlayback');
    if (!playback) {
      return;
    }
    playback.onended = null;
    playback.pause();
    playback.removeAttribute('src');
    try { playback.load(); } catch (error) {}
  }

  function clearLiveChunkWatchdog() {
    if (liveCurrentChunkWatchdog) {
      clearTimeout(liveCurrentChunkWatchdog);
      liveCurrentChunkWatchdog = 0;
    }
  }

  function finishCurrentChunkAndContinue(message) {
    clearLiveChunkWatchdog();
    livePlaybackActive = false;
    liveCurrentChunk = null;
    liveCurrentChunkStartedAt = 0;
    if (message) {
      updateLiveStatus(message);
    }
    playNextLiveReplyChunk();
  }

  function armLiveChunkWatchdog(playback, chunk) {
    clearLiveChunkWatchdog();
    var durationMs = 0;
    if (playback && isFinite(playback.duration) && playback.duration > 0) {
      durationMs = Math.ceil(playback.duration * 1000);
    }
    if (!durationMs) {
      durationMs = 15000;
    }
    liveCurrentChunkWatchdog = setTimeout(function () {
      if (!livePlaybackActive || !liveCurrentChunk || liveCurrentChunk !== chunk) {
        return;
      }
      finishCurrentChunkAndContinue('Chunk playback watchdog fired. Advancing to next chunk.');
    }, durationMs + 4000);
  }

  function queueLiveReplyChunk(chunk, turnId) {
    if (!chunk || !chunk.chunk_index || !chunk.audio_base64 || !chunk.audio_content_type) {
      return;
    }
    var chunkIndex = Number(chunk.chunk_index);
    var dedupeKey = String(turnId || '') + ':' + String(chunkIndex);
    if (!isFinite(chunkIndex) || chunkIndex <= 0 || livePlaybackSeenChunks[dedupeKey]) {
      return;
    }
    livePlaybackSeenChunks[dedupeKey] = true;
    livePlaybackQueue.push(chunk);
    livePlaybackQueue.sort(function (a, b) {
      return Number(a.chunk_index || 0) - Number(b.chunk_index || 0);
    });
    playNextLiveReplyChunk();
  }

  function finishLiveReplyIfIdle() {
    if (livePlaybackActive || livePlaybackQueue.length) {
      return;
    }
    liveAwaitingReply = false;
    updateLiveStatus('Live voice is armed. Start speaking.');
  }

  function playNextLiveReplyChunk() {
    if (livePlaybackActive || !livePlaybackQueue.length) {
      if (liveReplyCompleted) {
        finishLiveReplyIfIdle();
      }
      return;
    }
    var playback = document.getElementById('voicePlayback');
    if (!playback) {
      livePlaybackQueue = [];
      finishLiveReplyIfIdle();
      return;
    }
    var chunk = livePlaybackQueue.shift();
    livePlaybackActive = true;
    liveCurrentChunk = chunk;
    liveCurrentChunkStartedAt = Date.now();
    clearLiveChunkWatchdog();
    playback.onloadedmetadata = function () {
      armLiveChunkWatchdog(playback, chunk);
    };
    playback.oncanplay = function () {
      armLiveChunkWatchdog(playback, chunk);
    };
    playback.src = 'data:' + chunk.audio_content_type + ';base64,' + chunk.audio_base64;
    playback.muted = liveSpeakerMuted;
    updateLiveStatus('Speaking chunk ' + chunk.chunk_index + (chunk.interruptible === false ? ' (protected)...' : '...'));
    playback.onended = function () {
      finishCurrentChunkAndContinue('');
    };
    playback.onerror = function () {
      finishCurrentChunkAndContinue('Chunk playback error. Skipping to next chunk.');
    };
    playback.onstalled = function () {
      updateLiveStatus('Chunk playback stalled. Waiting for audio to resume...');
    };
    playback.onsuspend = function () {
      if (playback.ended) {
        finishCurrentChunkAndContinue('');
      }
    };
    playback.play().catch(function () {
      finishCurrentChunkAndContinue('Chunk playback rejected. Skipping to next chunk.');
    });
  }

  function liveCanInterruptCurrentReply() {
    if (!liveCurrentChunk) {
      return true;
    }
    if (liveCurrentChunk.interruptible !== false) {
      return true;
    }
    var protectMs = Number(liveCurrentChunk.protect_ms || 0);
    if (!isFinite(protectMs) || protectMs <= 0) {
      return false;
    }
    return (Date.now() - liveCurrentChunkStartedAt) >= protectMs;
  }

  function updateInterruptCandidate(nowMs, level) {
    if (!liveAwaitingReply || !bargeInEnabled()) {
      liveInterruptSpeechStartedAt = 0;
      clearInterruptProbe();
      return false;
    }
    if (level < currentSpeechThreshold()) {
      liveInterruptSpeechStartedAt = 0;
      clearInterruptProbe();
      return false;
    }
    if (!liveCanInterruptCurrentReply()) {
      liveInterruptSpeechStartedAt = 0;
      clearInterruptProbe();
      return false;
    }
    if ((nowMs - liveLastInterruptAt) < currentBargeCooldownMs()) {
      return false;
    }
    if (!liveInterruptSpeechStartedAt) {
      liveInterruptSpeechStartedAt = nowMs;
      return false;
    }
    return (nowMs - liveInterruptSpeechStartedAt) >= currentInterruptSpeechMs();
  }

  function clearInterruptProbe() {
    liveInterruptProbeChunks = [];
    liveInterruptProbeBytes = 0;
    liveInterruptProbePending = false;
  }

  function appendInterruptProbeChunk(buffer) {
    var chunk = new Uint8Array(buffer.slice(0));
    liveInterruptProbeChunks.push(chunk);
    liveInterruptProbeBytes += chunk.length;
    while (liveInterruptProbeBytes > 64000 && liveInterruptProbeChunks.length > 1) {
      var removed = liveInterruptProbeChunks.shift();
      liveInterruptProbeBytes -= removed.length;
    }
  }

  function interruptProbeBase64() {
    if (!liveInterruptProbeBytes) {
      return '';
    }
    var merged = new Uint8Array(liveInterruptProbeBytes);
    var offset = 0;
    for (var i = 0; i < liveInterruptProbeChunks.length; i += 1) {
      merged.set(liveInterruptProbeChunks[i], offset);
      offset += liveInterruptProbeChunks[i].length;
    }
    var binary = '';
    var chunkSize = 0x8000;
    for (var start = 0; start < merged.length; start += chunkSize) {
      var slice = merged.subarray(start, Math.min(start + chunkSize, merged.length));
      binary += String.fromCharCode.apply(null, slice);
    }
    return btoa(binary);
  }

  function rmsLevel(floatBuffer) {
    var sum = 0;
    for (var i = 0; i < floatBuffer.length; i += 1) {
      sum += floatBuffer[i] * floatBuffer[i];
    }
    return Math.sqrt(sum / Math.max(1, floatBuffer.length));
  }

  function maybeCloseLiveTurn(nowMs) {
    if (!liveTurnOpen || liveAwaitingReply) {
      return;
    }
    if ((nowMs - liveTurnStartedAt) < currentMinimumTurnMs()) {
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

  function handleLiveAudio(event) {
    var input = event.inputBuffer.getChannelData(0);
    var level = rmsLevel(input);
    var nowMs = Date.now();
    liveLastLevel = level;
    var downsampled = downsampleBuffer(input, liveAudioContext.sampleRate, LIVE_TARGET_SAMPLE_RATE);
    var pcm = floatTo16BitPCM(downsampled);
    drawLiveMeter(liveMicMuted ? 0 : level);
    renderLiveDebug();
    if (!liveSocket || liveSocket.readyState !== WebSocket.OPEN) {
      return;
    }
    if (liveMicMuted) {
      return;
    }
    if (liveAwaitingReply) {
      if (level >= currentSpeechThreshold()) {
        appendInterruptProbeChunk(pcm.buffer);
      }
      if (updateInterruptCandidate(nowMs, level) && !liveInterruptProbePending) {
        if (currentBargeInMode() === 'amplitude') {
          liveInterruptSpeechStartedAt = 0;
          clearInterruptProbe();
          interruptLiveReply();
        } else {
          var probeBase64 = interruptProbeBase64();
          if (probeBase64) {
            liveInterruptProbePending = true;
            liveSocket.send(JSON.stringify({ type: 'interrupt_probe', audio_base64: probeBase64 }));
          }
        }
      }
      return;
    }

    if (level >= currentSpeechThreshold()) {
      maybeOpenLiveTurn(nowMs);
      liveLastSpeechAt = nowMs;
    }

    if (!liveTurnOpen) {
      return;
    }

    liveSocket.send(pcm.buffer);
    maybeCloseLiveTurn(nowMs);
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

  function liveMeterLevelsPush(value) {
    liveMeterLevels.push(Math.min(1, Math.max(0, value)));
    if (liveMeterLevels.length > 72) {
      liveMeterLevels.shift();
    }
  }

  function currentBargeInMode() {
    var input = document.getElementById('liveBargeInMode');
    var value = input ? String(input.value || 'transcript') : 'transcript';
    return value === 'amplitude' ? 'amplitude' : 'transcript';
  }

  function currentLiveResponseMode() {
    var input = document.getElementById('liveResponseMode');
    var value = input ? String(input.value || 'simple_chunked') : 'simple_chunked';
    return value === 'incremental_experimental' ? 'incremental_experimental' : 'simple_chunked';
  }

  function updateLiveButton() {
    var button = document.getElementById('liveVoiceButton');
    if (!button) return;
    button.textContent = liveSocket ? 'Live Running' : 'Start Live';
    button.classList.toggle('secondary', !liveSocket);
  }

  function browserMicUnavailableReason() {
    var host = String(window.location.hostname || '').toLowerCase();
    var local = host === 'localhost' || host === '127.0.0.1' || host === '::1';
    if (!window.isSecureContext && !local) return 'Microphone access requires HTTPS or localhost.';
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return 'Microphone capture is unavailable in this browser.';
    return '';
  }

  function setLiveSubtitle(partial, transcript, reply) {
    var target = document.getElementById('liveSubtitleStatus');
    if (!target) return;
    var lines = [];
    if (partial) lines.push('Listening: ' + partial);
    if (transcript) lines.push('You: ' + transcript);
    if (reply) lines.push('Shana: ' + reply);
    target.textContent = lines.join('\n\n') || 'Subtitles idle.';
  }

  function handleLiveMessage(payload) {
    liveLastEvent = payload.type + (payload.state ? ': ' + payload.state : '');
    if (payload.type === 'state') {
      if (payload.detail) updateLiveStatus(payload.detail);
      renderLiveMeta(payload.job || null);
      if (['cancelled', 'interrupted', 'failed'].indexOf(payload.state) >= 0) {
        resetLivePlayback(true);
        liveAwaitingReply = false;
      }
    } else if (payload.type === 'partial_transcript') {
      setLiveSubtitle(payload.text || '', '', '');
      updateLiveStatus(payload.text ? 'Hearing: ' + payload.text : 'Speech detected; waiting for transcript text.');
    } else if (payload.type === 'transcript') {
      setLiveSubtitle('', payload.text || '', '');
      updateLiveStatus('Transcript ready.');
    } else if (payload.type === 'reply_chunk_ready') {
      setLiveSubtitle('', '', (payload.chunk || {}).text || '');
      queueLiveReplyChunk(payload.chunk || null, payload.turn_id || '');
    } else if (payload.type === 'interrupt_probe_result') {
      liveInterruptProbePending = false;
      if (payload.text) interruptLiveReply();
      else clearInterruptProbe();
    } else if (payload.type === 'turn_result') {
      setLiveSubtitle('', payload.transcript || '', payload.reply_text || '');
      renderLiveMeta(payload.job || null);
      liveHistory.push(payload);
      renderLiveHistory();
      liveReplyCompleted = true;
      (payload.reply_chunks || []).forEach(function (chunk) { queueLiveReplyChunk(chunk, payload.turn_id || ''); });
      finishLiveReplyIfIdle();
    } else if (payload.type === 'error') {
      liveAwaitingReply = false;
      liveTurnOpen = false;
      updateLiveStatus(payload.detail || 'Live voice returned an error.');
      renderLiveMeta(payload.job || null);
    }
    renderLiveDebug(payload);
  }

  async function startLiveVoice() {
    var reason = browserMicUnavailableReason();
    if (reason) {
      updateLiveStatus(reason);
      return;
    }
    updateLiveStatus('Connecting live voice socket...');
    liveSocket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/voice/live');
    liveSocket.onopen = async function () {
      try {
        liveMediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        liveAudioContext = new (window.AudioContext || window.webkitAudioContext)();
        liveSourceNode = liveAudioContext.createMediaStreamSource(liveMediaStream);
        liveProcessorNode = liveAudioContext.createScriptProcessor(4096, 1, 1);
        liveProcessorNode.onaudioprocess = handleLiveAudio;
        liveSourceNode.connect(liveProcessorNode);
        liveProcessorNode.connect(liveAudioContext.destination);
        updateLiveButton();
        updateMuteButtons();
        updateLiveStatus('Live voice is armed. Start speaking.');
        renderLiveDebug();
      } catch (error) {
        updateLiveStatus('Live microphone error: ' + String(error));
        stopLiveVoice();
      }
    };
    liveSocket.onmessage = function (event) {
      try {
        handleLiveMessage(JSON.parse(event.data));
      } catch (error) {
        updateLiveStatus('Live message could not be parsed: ' + String(error));
      }
    };
    liveSocket.onerror = function () { updateLiveStatus('Live voice socket error. Check dashboard and Shana logs.'); };
    liveSocket.onclose = function () {
      cleanupLiveAudio();
      resetLivePlayback(true);
      liveSocket = null;
      liveTurnOpen = false;
      liveAwaitingReply = false;
      updateLiveButton();
      updateLiveStatus('Live voice session stopped.');
      renderLiveDebug();
    };
  }

  function cleanupLiveAudio() {
    if (liveProcessorNode) { liveProcessorNode.disconnect(); liveProcessorNode.onaudioprocess = null; liveProcessorNode = null; }
    if (liveSourceNode) { liveSourceNode.disconnect(); liveSourceNode = null; }
    if (liveMediaStream) { liveMediaStream.getTracks().forEach(function (track) { track.stop(); }); liveMediaStream = null; }
    if (liveAudioContext) { liveAudioContext.close().catch(function () {}); liveAudioContext = null; }
  }

  function stopLiveVoice() {
    if (liveSocket && liveSocket.readyState === WebSocket.OPEN && liveTurnOpen) {
      liveSocket.send(JSON.stringify({ type: 'cancel_turn' }));
    }
    if (liveSocket) liveSocket.close();
    else {
      cleanupLiveAudio();
      resetLivePlayback(true);
      updateLiveButton();
      updateLiveStatus('Live voice session stopped.');
      renderLiveDebug();
    }
  }

  function toggleLiveVoice() {
    if (liveSocket) stopLiveVoice();
    else startLiveVoice();
  }

  function renderLiveDebug(lastPayload) {
    var target = document.getElementById('liveDebugPanel');
    if (!target) return;
    target.hidden = !liveSocket;
    if (!liveSocket) return;
    var socketStates = ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'];
    target.textContent = [
      'Socket: ' + (socketStates[liveSocket.readyState] || liveSocket.readyState),
      'Audio context: ' + (liveAudioContext ? liveAudioContext.state : 'not started'),
      'Mic level: ' + liveLastLevel.toFixed(4) + ' | threshold: ' + currentSpeechThreshold().toFixed(3),
      'Turn open: ' + liveTurnOpen + ' | awaiting reply: ' + liveAwaitingReply,
      'Playback queue: ' + livePlaybackQueue.length + ' | active: ' + livePlaybackActive,
      'Barge-in: ' + (bargeInEnabled() ? currentBargeInMode() : 'disabled') + ' | cooldown: ' + currentBargeCooldownMs() + ' ms',
      'Last event: ' + liveLastEvent,
      lastPayload ? 'Last payload: ' + JSON.stringify(lastPayload, null, 2) : ''
    ].filter(Boolean).join('\n');
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
    if (job.response_mode) lines.push('Response mode: ' + job.response_mode);
    if (job.cancel_reason) lines.push('Cancel reason: ' + job.cancel_reason);
    if (typeof job.cancel_latency_ms !== 'undefined' && job.cancel_latency_ms !== null) lines.push('Cancel latency: ' + fmtDurationMs(job.cancel_latency_ms));
    if (job.started_at) lines.push('Started: ' + fmtLocalDateTime(job.started_at));
    if (job.completed_at) lines.push('Completed: ' + fmtLocalDateTime(job.completed_at));
    target.textContent = lines.join('\n');
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
        if (turn.timing_ms.time_to_first_chunk_audio_ms) {
          lines.push('First audio: ' + (turn.timing_ms.time_to_first_chunk_audio_ms || 0) + ' ms');
        }
      }
      if (turn.job && turn.job.cancel_reason) {
        lines.push('Cancel reason: ' + turn.job.cancel_reason);
      }
      lines.push('');
    }
    target.textContent = lines.join('\n').trim();
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

  function sectionHashesValue(value) {
    var hash = JSON.stringify(value);
    try {
      return JSON.stringify(hash);
    } catch (e) {
      return String(hash);
    }
  }

  var liveMeterLevels = [];
  var viewMode = localStorage.getItem('gammaDashboardViewMode') || 'human';
  var sectionHashes = {};
  window.toggleLiveMicMuted = toggleLiveMicMuted;
  window.toggleLiveSpeakerMuted = toggleLiveSpeakerMuted;
  window.toggleLiveVoice = toggleLiveVoice;
  window.stopLiveVoice = stopLiveVoice;
})();
