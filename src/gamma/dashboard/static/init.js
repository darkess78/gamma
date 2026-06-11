// init.js - Main initialization entry point for the refactored Gamma dashboard
(function () {
  'use strict';

  // Core state and initialization
  var latestData = null;
  var viewMode = localStorage.getItem('gammaDashboardViewMode') || 'human';
  var runtimeStatusSupported = true;
  var runtimePollMs = 10000;

  // State for live voice
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

  // State for TTS player
  var ttsPlayerCurrentFile = null;
  var ttsLastArtifactName = null;

  // State for vision
  var selectedVisionFile = null;
  var selectedVisionPreviewUrl = null;
  var visionHistory = [];

  // State for subtitles
  var subtitlePopup = null;
  var subtitleState = { transcript: '', reply: '', partial: '' };

  // Status chips
  var liveMeterLevels = [];
  var synced = false;

  // Utility functions
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

  function pretty(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
    }
  }

  function renderBlockIfChanged(elementId, rawValue, humanText, cacheKey) {
    if (!document.getElementById(elementId)) return;
    var key = cacheKey || elementId;
    if (!sectionHashes) sectionHashes = {};
    var nextKey = viewMode === 'json' ? pretty(rawValue) : humanText;
    if (sectionHashes[key] === nextKey) {
      return;
    }
    if (!sectionHashes) sectionHashes = {};
    sectionHashes[key] = nextKey;
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

  function setTextIfChanged(elementId, value, cacheKey) {
    var el = document.getElementById(elementId);
    if (!el) return;
    if (!sectionHashes) sectionHashes = {};
    var key = cacheKey || elementId;
    if (sectionHashes[key] === value) {
      return;
    }
    sectionHashes[key] = value;
    el.textContent = value;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function sectionHashesKey(value) {
    return JSON.stringify(value).length > 0 ? JSON.stringify(value) : String(value);
  }

  // Live voice initialization
  function initLiveVoice() {
    var micReason = browserMicUnavailableReason();
    if (micReason) {
      updateLiveStatus(micReason);
      return;
    }
    try {
      liveSocket = new WebSocket((window.location.protocol === 'https:' ? 'wss://' : 'ws://') + window.location.host + '/api/voice/live');
      liveSocket.onopen = async function () {
        try {
          resetLivePlayback(true);
          liveMediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
          liveAudioContext = new (window.AudioContext || window.webkitAudioContext)();
          liveSourceNode = liveAudioContext.createMediaStreamSource(liveMediaStream);
          liveProcessorNode = liveAudioContext.createScriptProcessor(4096, 1, 1);
          liveProcessorNode.onaudioprocess = handleLiveAudio;
          liveSourceNode.connect(liveProcessorNode);
          liveProcessorNode.connect(liveAudioContext.destination);
          updateLiveStatus('Live voice is armed. Start speaking.');
          syncLivePlaybackMute();
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
          handleLiveState(payload);
        }
        else if (payload.type === 'partial_transcript') {
          setSubtitleState({ partial: payload.text || '' });
          updateLiveStatus('Hearing: ' + (payload.text || '...'));
        }
        else if (payload.type === 'transcript') {
          setSubtitleState({ transcript: payload.text || '', partial: '' });
          updateLiveStatus('Transcript ready.');
        }
        else if (payload.type === 'reply_chunk_ready') {
          if (payload.chunk && payload.chunk.text) {
            setSubtitleState({ reply: payload.chunk.text, partial: '' });
          }
          queueLiveReplyChunk(payload.chunk || null, payload.turn_id || '');
        }
        else if (payload.type === 'interrupt_probe_result') {
          handleInterruptProbeResult(payload);
        }
        else if (payload.type === 'idle_decision') {
          liveHistory.push({
            kind: 'event',
            label: 'idle decision',
            detail: (payload.would_reply ? 'Would speak: ' : 'Stayed quiet: ') + (payload.reason || payload.decision || 'n/a'),
            job: payload.stream || null
          });
          renderLiveHistory();
          updateLiveStatus('Idle policy dry run: ' + (payload.reason || payload.decision || 'decision logged') + '.');
        }
        else if (payload.type === 'turn_result') {
          setSubtitleState({ transcript: payload.transcript || '', reply: payload.reply_text || '', partial: '' });
          renderLiveMeta(payload.job || null);
          liveHistory.push(payload);
          renderLiveHistory();
          renderBlock('voiceRoundtripStatus', payload, humanVoiceRoundtrip(payload));
          liveReplyCompleted = true;
          if (payload.reply_chunks && payload.reply_chunks.length) {
            for (var i = 0; i < payload.reply_chunks.length; i += 1) {
              queueLiveReplyChunk(payload.reply_chunks[i], payload.turn_id || '');
            }
            finishLiveReplyIfIdle();
          }
        }
        else if (payload.type === 'error') {
          resetLivePlayback(true);
          liveAwaitingReply = false;
          liveTurnOpen = false;
          renderLiveMeta(payload.job || null);
          updateLiveStatus(payload.detail || 'Live voice error.');
        }
      };
      liveSocket.onclose = function () {
        resetLivePlayback(true);
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

  function browserMicUnavailableReason() {
    var host = String(window.location.hostname || '').toLowerCase();
    var localhost = host === 'localhost' || host === '127.0.0.1' || host === '::1';
    if (!window.isSecureContext && !localhost) {
      return 'Browser microphone access requires HTTPS or localhost. Open the dashboard at http://127.0.0.1:8001 on this machine, or serve it over HTTPS.';
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return 'This browser does not expose microphone capture here. Use a current browser over HTTPS or localhost.';
    }
    return '';
  }

  function handleLiveState(payload) {
    if (payload.state === 'idle' && !liveAwaitingReply) {
      updateLiveStatus(payload.detail || 'Live voice is idle.');
    } else if (payload.state === 'interrupted') {
      updateLiveStatus(payload.detail || 'Interrupted.');
    } else if (payload.detail) {
      updateLiveStatus(payload.detail);
    }
    if (payload.state === 'cancelled' || payload.state === 'interrupted' || payload.state === 'failed') {
      resetLivePlayback(true);
      liveAwaitingReply = false;
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
  }

  function handleInterruptProbeResult(payload) {
    liveInterruptProbePending = false;
    if (payload.text) {
      updateLiveStatus('Speech detected. Interrupting...');
      interruptLiveReply();
    } else {
      liveInterruptSpeechStartedAt = 0;
      liveInterruptProbeChunks = [];
      liveInterruptProbeBytes = 0;
    }
  }

  function handleLiveAudio(event) {
    var input = event.inputBuffer.getChannelData(0);
    var level = rmsLevel(input);
    var nowMs = Date.now();
    var downsampled = downsampleBuffer(input, liveAudioContext.sampleRate, LIVE_TARGET_SAMPLE_RATE);
    var pcm = floatTo16BitPCM(downsampled);
    drawLiveMeter(liveMicMuted ? 0 : level);
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

  function rmsLevel(floatBuffer) {
    var sum = 0;
    for (var i = 0; i < floatBuffer.length; i += 1) {
      sum += floatBuffer[i] * floatBuffer[i];
    }
    return Math.sqrt(sum / Math.max(1, floatBuffer.length));
  }

  function currentSpeechThreshold() {
    var input = document.getElementById('liveSpeechThreshold');
    if (!input) return 0.018;
    return Number(input.value || 18) / 1000;
  }

  function currentBargeInMode() {
    var input = document.getElementById('liveBargeInMode');
    var value = input ? String(input.value || 'transcript') : 'transcript';
    return value === 'amplitude' ? 'amplitude' : 'transcript';
  }

  function updateInterruptCandidate(nowMs, level) {
    if (!liveAwaitingReply || !liveBargeInEnabled()) {
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
    if (!liveInterruptSpeechStartedAt) {
      liveInterruptSpeechStartedAt = nowMs;
      return false;
    }
    return (nowMs - liveInterruptSpeechStartedAt) >= currentInterruptSpeechMs();
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

  function updateInterruptSpeechMs() {
    var input = document.getElementById('liveInterruptSpeechMs');
    if (!input) return 260;
    return Number(input.value || 260);
  }

  function currentInterruptSpeechMs() {
    var input = document.getElementById('liveInterruptSpeechMs');
    if (!input) return 260;
    return Number(input.value || 260);
  }

  function liveBargeInEnabled() {
    var input = document.getElementById('liveBargeInEnabled');
    return !!(input && input.checked);
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
        lines.push('');
        continue;
      }
      lines.push('You: ' + (turn.transcript || ''));
      lines.push('Shana: ' + (turn.reply_text || ''));
      if (turn.timing_ms) {
        lines.push('Timing: stt ' + (turn.timing_ms.stt_ms || 0) + ' ms | llm+tts ' + (turn.timing_ms.conversation_ms || 0) + ' ms | tts ' + (turn.timing_ms.tts_ms || 0) + ' ms | total ' + (turn.timing_ms.total_ms || 0) + ' ms');
      }
      lines.push('');
    }
    target.textContent = lines.join('\n').trim();
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
    if (timing.time_to_first_chunk_audio_ms) {
      lines.push('First audio: ' + (timing.time_to_first_chunk_audio_ms || 0) + ' ms');
    }
    if (payload.reply_chunks && payload.reply_chunks.length) {
      lines.push('Chunks: ' + payload.reply_chunks.length);
    }
    lines.push('Audio returned: ' + (payload.audio_base64 ? 'yes' : 'no'));
    return lines.join('\n');
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

  function liveSpeakerMuted() {
    return liveSpeakerMutedGlobal;
  }

  var liveSpeakerMutedGlobal = false;

  function setSubtitleState(nextState) {
    subtitleState = {
      transcript: String(nextState && nextState.transcript || subtitleState.transcript || ''),
      reply: String(nextState && nextState.reply || subtitleState.reply || ''),
      partial: String(nextState && nextState.partial || subtitleState.partial || '')
    };
    var lines = [];
    if (subtitleState.partial) lines.push('Listening: ' + subtitleState.partial);
    if (subtitleState.transcript) lines.push('You: ' + subtitleState.transcript);
    if (subtitleState.reply) lines.push('Shana: ' + subtitleState.reply);
    var text = lines.length ? lines.join('\n\n') : 'Subtitles idle.';
    setTextIfChanged('liveSubtitleStatus', text, 'liveSubtitleStatus');
    updateSubtitlePopup(text);
  }

  function updateSubtitlePopup(text) {
    if (!subtitlePopup || subtitlePopup.closed) {
      subtitlePopup = null;
      return;
    }
    var target = subtitlePopup.document.getElementById('subtitleText');
    if (target) target.textContent = text;
  }

  // Initialize
  console.log('Gamma dashboard modules loaded: nav.js, memory.js, monitor.js, api.js, live.js, providers.js, stream.js, render.js');
})();