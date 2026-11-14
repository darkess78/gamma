/* Thin private presentation. Decisions and delivery validity remain in Shana. */
(function () {
  'use strict';
  const base = '/api/coordination/interaction';
  const consumerId = `desktop-${crypto.randomUUID()}`;
  const queue = new window.ShanaPlaybackQueue(8);
  let privacyEpoch = 0, connected = false, polling = false, snapshot = null;
  let audio = null, audioUrl = null, playing = null, finishing = null, media = null, capture = null, processor = null;
  let captureEpoch = 0, audioSequence = 0;
  const audioRequests = new Map();
  let frames = [], preRoll = [], samples = 0, openedAt = 0, speechAt = 0, noise = .003;
  let textPending = false, textOperation = null, micStarting = false;
  let connectionFlight = Promise.resolve(), pendingDisconnect = Promise.resolve();
  const el = id => document.getElementById(id);
  const privateReady = () => el('privateConfirm').checked;
  const valid = epoch => epoch === privacyEpoch && privateReady() && connected;
  const id = () => crypto.randomUUID();
  const parameters = extra => new URLSearchParams({audience: 'owner_private', consumer_id: consumerId, ...extra});
  const statusText = text => { el('connectionStatus').textContent = text; };
  async function request(path, body, timeout = 15000, controller = null) {
    const abort = controller || new AbortController();
    const timer = setTimeout(() => abort.abort(), timeout);
    try {
      const response = await fetch(path, {method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
        signal: abort.signal, ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});
      const value = await response.json();
      if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : `HTTP ${response.status}`);
      return value;
    } finally { clearTimeout(timer); }
  }
  const post = (action, values = {}, timeout, controller = null) => request(`${base}/${action}`, {
    audience: 'owner_private', request_id: id(), consumer_id: consumerId, ...values
  }, timeout, controller);
  function stopAudio() {
    if (audio) { audio.onplaying = audio.onended = audio.onerror = null; audio.pause(); audio.removeAttribute('src'); audio.load(); }
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    audio = audioUrl = playing = null;
    document.body.dataset.speaking = 'false';
  }
  async function ack(item, state) {
    if (!privateReady()) return false;
    const epoch = privacyEpoch;
    const previous = item.ackChain || Promise.resolve();
    const operation = previous.catch(() => false).then(async () => {
      if (epoch !== privacyEpoch || !privateReady()) return false;
      try {
        await request(`${base}/ack`, {audience: 'owner_private', consumer_id: consumerId,
          output_id: item.id, generation: item.generation, output_epoch: item.epoch, status: state});
        return true;
      } catch (_) { return false; /* An uncertain acknowledgment never proves delivery. */ }
    });
    item.ackChain = operation;
    return operation;
  }
  function clearPlayback(acknowledge = false, skipId = null) {
    const current = playing || finishing;
    finishing = null;
    stopAudio();
    for (const item of queue.clear()) if (acknowledge && item.id !== skipId) void ack(current?.id === item.id ? current : item, 'cancelled');
  }
  function updateState(value, epoch = privacyEpoch) {
    if (!valid(epoch)) return;
    if (!Number.isSafeInteger(value.generation) || !Number.isSafeInteger(value.output_epoch)) throw new Error('Invalid output identity');
    const removed = queue.sync(value.generation, value.output_epoch);
    if (removed.length || (playing && (playing.generation !== value.generation || playing.epoch !== value.output_epoch))) {
      finishing = null; stopAudio(); el('caption').textContent = ''; el('deliveryStatus').textContent = 'Previous output is no longer current.';
    }
    const current = item => value.awake && value.consumer?.id === consumerId && value.consumer.connected
      && (value.outputs || []).some(output => output.id === item.id
        && output.generation === item.generation && output.output_epoch === item.epoch
        && ((output.status === 'played' && item.id === finishing?.id)
          || (['generated','offered','attempted','started'].includes(output.status)
            && (!item.audio_available || output.audio_available))));
    if ((queue.active && !current(queue.active)) || queue.pending.some(item => !current(item))) {
      clearPlayback();
      el('caption').textContent = ''; el('deliveryStatus').textContent = 'Previous output is no longer current.';
    }
    snapshot = value;
    document.body.dataset.awake = String(Boolean(value.awake));
    el('availability').textContent = `${value.awake ? 'Awake' : 'Asleep'}${value.focus ? ` · ${value.focus}` : ''}`;
    el('muteToggle').textContent = value.speech_enabled ? 'Mute speech' : 'Enable speech';
    el('voiceToggle').textContent = media ? 'Stop listening' : 'Start listening';
    el('sleepToggle').textContent = value.awake ? 'Sleep' : 'Wake';
    if (!value.speech_enabled) clearPlayback(true);
    if (!value.voice_enabled && media) stopMic();
    if (!value.consumer?.connected || value.consumer.id !== consumerId) {
      connected = false; clearPlayback(); stopMic();
      statusText('This presentation connection expired. Reconnect when ready.');
    }
  }
  async function playNext() {
    if (playing || finishing || !connected || !privateReady()) return;
    const item = queue.start();
    if (!item) return;
    const epoch = privacyEpoch;
    playing = item;
    el('caption').textContent = item.display_text || '';
    if (!item.audio_available) {
      const attempted = await ack(item, 'attempted');
      if (valid(epoch) && attempted) {
        const delivered = await ack(item, 'delivered');
        if (valid(epoch)) el('deliveryStatus').textContent = delivered ? 'Displayed as text.' : 'Text displayed; receipt unconfirmed.';
      } else if (valid(epoch)) el('deliveryStatus').textContent = 'Text displayed; receipt unconfirmed.';
      if (playing?.id === item.id) { playing = null; queue.finish(item.id); }
      return;
    }
    if (!snapshot?.speech_enabled) { clearPlayback(true); return; }
    try {
      const response = await fetch(`${base}/audio/${encodeURIComponent(item.id)}?${parameters({generation: item.generation, output_epoch: item.epoch})}`,
        {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error(`Audio unavailable: HTTP ${response.status}`);
      const blob = await response.blob();
      if (!valid(epoch) || !playing || playing.id !== item.id || queue.generation !== item.generation || queue.epoch !== item.epoch) return;
      if (!blob.type.startsWith('audio/') || blob.size > 8 * 1024 * 1024) throw new Error('Invalid audio response');
      audioUrl = URL.createObjectURL(blob);
      audio = new Audio(audioUrl);
      audio.onplaying = () => {
        if (!valid(epoch) || playing?.id !== item.id) { stopAudio(); return; }
        document.body.dataset.speaking = 'true';
        el('deliveryStatus').textContent = 'Playing. Device playback is not a human listening assessment.';
        void ack(item, 'started');
      };
      const finish = async state => {
        if (!valid(epoch) || playing?.id !== item.id) return;
        if (state === 'failed') {
          clearPlayback(true, item.id);
          el('deliveryStatus').textContent = 'Playback failed.';
          await ack(item, state);
          return;
        }
        stopAudio(); finishing = item;
        el('deliveryStatus').textContent = 'Playback ended; receipt pending.';
        const confirmed = await ack(item, state);
        if (!valid(epoch) || finishing?.id !== item.id) return;
        if (confirmed) { queue.finish(item.id); finishing = null; }
        else clearPlayback(true, item.id);
        el('deliveryStatus').textContent = confirmed ? 'Playback ended on this device.' : 'Playback ended; receipt unconfirmed. Later chunks were cancelled.';
        if (confirmed) void playNext();
      };
      audio.onended = () => finish('played');
      audio.onerror = () => finish('failed');
      await audio.play();
    } catch (error) {
      if (!valid(epoch) || playing?.id !== item.id) return;
      clearPlayback(true, item.id);
      el('deliveryStatus').textContent = `Speech did not play. ${String(error.message || error)}`;
      void ack(item, 'failed');
    }
  }
  async function poll() {
    if (polling || !connected || !privateReady()) return;
    polling = true;
    const epoch = privacyEpoch;
    try {
      const value = await request(`${base}/status?${parameters()}`, null, 5000);
      if (!valid(epoch)) return;
      updateState(value);
      if (!valid(epoch)) return;
      const next = await request(`${base}/poll?${parameters()}`, null, 5000);
      if (!valid(epoch)) return;
      if (next.generation !== queue.generation || next.output_epoch !== queue.epoch) {
        clearPlayback(); queue.sync(next.generation, next.output_epoch);
      }
      if (next.output) {
        const item = {...next.output, epoch: next.output.output_epoch};
        if (!queue.offer(item) && !queue.seen.has(item.id)) {
          // Missing or inconsistent chunks are cancelled; a later chunk never
          // becomes the beginning of speech after a gap or failed playback.
          clearPlayback(true);
          void ack(item, 'cancelled');
        }
      }
      await playNext();
    } catch (error) {
      if (!valid(epoch)) return;
      connected = false; clearPlayback(); stopMic();
      statusText('Connection lost. Playback and capture stopped. Reconnect when ready.');
    } finally { polling = false; }
  }
  async function connect() {
    if (!privateReady()) return;
    const epoch = ++privacyEpoch;
    connected = false; clearPlayback(); stopMic();
    statusText('Connecting to Shana…');
    try {
      await pendingDisconnect;
      if (epoch !== privacyEpoch || !privateReady()) return;
      connectionFlight = post('connect');
      const value = await connectionFlight;
      if (epoch !== privacyEpoch || !privateReady()) return;
      connected = true; updateState(value, epoch);
      statusText('Connected privately. Microphone capture starts only when you enable it.');
      await poll();
    } catch (error) { if (epoch === privacyEpoch && privateReady()) statusText(`Could not connect. ${String(error.message || error)}`); }
  }
  function stopMic() {
    captureEpoch += 1;
    audioSequence += 1;
    for (const entry of audioRequests.values()) entry.controller.abort();
    audioRequests.clear();
    micStarting = false; el('voiceToggle').disabled = false;
    if (processor) { processor.onaudioprocess = null; processor.disconnect(); }
    if (media) media.getTracks().forEach(track => track.stop());
    if (capture) void capture.close().catch(() => {});
    media = capture = processor = null;
    frames = []; preRoll = []; samples = 0; openedAt = speechAt = 0;
    el('micMeter').style.width = '0'; el('micStatus').textContent = 'Microphone off';
    el('voiceToggle').textContent = 'Start listening';
  }
  function wavBytes(chunks, count) {
    const data = new ArrayBuffer(44 + count * 2), view = new DataView(data);
    const string = (at, text) => { for (let i = 0; i < text.length; i++) view.setUint8(at + i, text.charCodeAt(i)); };
    string(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); string(8, 'WAVE'); string(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    string(36, 'data'); view.setUint32(40, count * 2, true);
    let at = 44;
    for (const chunk of chunks) for (const sample of chunk) { const n = Math.max(-1, Math.min(1, sample)); view.setInt16(at, n < 0 ? n * 32768 : n * 32767, true); at += 2; }
    const bytes = new Uint8Array(data); let binary = '';
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return btoa(binary);
  }
  function observation(channel) {
    return {channel, speaker: channel === 'desktop_text' || el('ownerSource').checked ? 'owner' : 'unknown', address_cue: channel === 'desktop_text' ? 'direct' : 'none', confidence: .5};
  }
  function receiptText(receipt) {
    if (receipt.status === 'partial') return 'Only part of the spoken reply was generated. The full reply remains available as text. It was not automatically replayed.';
    if (receipt.status === 'busy') return 'Shana is processing a previous turn. This input was not queued.';
    if (receipt.status === 'silent') return 'Shana chose silence.';
    if (receipt.status === 'ignored') return `Input ignored: ${receipt.reason || 'no accepted turn'}.`;
    if (receipt.status === 'cancelled') return 'This turn was cancelled.';
    if (receipt.status === 'failed' || receipt.ok === false) return 'The turn failed. It was not automatically replayed.';
    if (receipt.status === 'pending_or_uncertain_no_replay') return 'The earlier turn is pending or uncertain. It was not replayed.';
    if (receipt.status === 'deferred') return 'Shana deferred this turn.';
    return 'Turn processed.';
  }
  async function sendClip(clip) {
    if (!connected || !privateReady() || clip.captureEpoch !== captureEpoch) return;
    if (audioRequests.size >= 2) {
      el('micStatus').textContent = 'Two voice turns are already pending. This turn was not sent.';
      return;
    }
    const epoch = privacyEpoch, micEpoch = captureEpoch, sequence = ++audioSequence;
    const requestId = id(), entry = {controller: new AbortController()};
    audioRequests.set(requestId, entry);
    const current = () => valid(epoch) && micEpoch === captureEpoch && sequence === audioSequence;
    try {
      const receipt = await post('audio-input', {request_id: requestId, session_id: 'desktop-private', audio_base64: clip.base64,
        observation: clip.observation}, 180000, entry.controller);
      if (current()) {
        el('micStatus').textContent = `${receiptText(receipt)} Listening continues.`;
        void poll();
      }
    } catch (error) { if (current()) el('micStatus').textContent = `Speech input failed. ${String(error.message || error)}`; }
    finally { if (audioRequests.get(requestId) === entry) audioRequests.delete(requestId); }
  }
  function finishCapture() {
    if (samples >= 3520) void sendClip({base64: wavBytes(frames, samples), observation: observation('private_voice'), captureEpoch});
    frames = []; samples = 0; openedAt = speechAt = 0;
  }
  async function startMic() {
    if (!connected || !privateReady() || micStarting) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) { el('micStatus').textContent = 'Microphone capture needs localhost or HTTPS and browser permission.'; return; }
    const epoch = privacyEpoch, micEpoch = ++captureEpoch;
    micStarting = true; el('voiceToggle').disabled = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}, video: false});
      if (!valid(epoch) || micEpoch !== captureEpoch) { stream.getTracks().forEach(track => track.stop()); return; }
      await post('control', {action: 'voice_enabled', value: true});
      if (!valid(epoch) || micEpoch !== captureEpoch) { stream.getTracks().forEach(track => track.stop()); return; }
      media = stream; capture = new (window.AudioContext || window.webkitAudioContext)();
      const source = capture.createMediaStreamSource(media);
      processor = capture.createScriptProcessor(4096, 1, 1);
      const silent = capture.createGain(); silent.gain.value = 0;
      processor.onaudioprocess = event => {
        if (!valid(epoch) || micEpoch !== captureEpoch) return;
        const input = event.inputBuffer.getChannelData(0);
        let energy = 0; for (const n of input) energy += n * n;
        const level = Math.sqrt(energy / input.length), now = performance.now();
        el('micMeter').style.width = `${Math.min(100, level * 650)}%`;
        const ratio = capture.sampleRate / 16000;
        const chunk = new Float32Array(Math.floor(input.length / ratio));
        for (let i = 0; i < chunk.length; i++) { const start = Math.floor(i * ratio), end = Math.max(start + 1, Math.floor((i + 1) * ratio)); let total = 0; for (let j = start; j < end && j < input.length; j++) total += input[j]; chunk[i] = total / (end - start); }
        if (!openedAt) {
          preRoll.push(chunk); while (preRoll.length > 2) preRoll.shift();
          if (level < Math.max(.012, noise * 3)) { noise = noise * .98 + level * .02; return; }
          openedAt = speechAt = now; frames = preRoll; preRoll = []; samples = frames.reduce((n, c) => n + c.length, 0);
        } else {
          frames.push(chunk); samples += chunk.length;
          if (level >= Math.max(.008, noise * 2)) speechAt = now;
        }
        if (samples >= 188000 || now - speechAt >= 650) finishCapture();
      };
      source.connect(processor); processor.connect(silent); silent.connect(capture.destination);
      await capture.resume();
      el('micStatus').textContent = 'Listening. Capture continues during speech; Shana checks transcript echo before interruption.';
      el('voiceToggle').textContent = 'Stop listening';
      snapshot = {...snapshot, voice_enabled: true};
    } catch (error) {
      if (micEpoch === captureEpoch) {
        stopMic(); if (valid(epoch)) { el('micStatus').textContent = `Microphone unavailable. ${String(error.message || error)}`; void post('control', {action: 'voice_enabled', value: false}).catch(() => {}); }
      }
    } finally { if (micEpoch === captureEpoch) { micStarting = false; el('voiceToggle').disabled = false; } }
  }
  async function interrupt() {
    if (!privateReady()) return;
    const epoch = privacyEpoch;
    clearPlayback(true);
    try { const value = await post('interrupt', {reason: 'owner_stop'}); if (valid(epoch) && value.generation) updateState({...snapshot, ...value}, epoch); }
    catch (_) { if (valid(epoch)) statusText('Local playback stopped. The remote interruption is unconfirmed.'); }
  }
  function clearPrivate() {
    privacyEpoch += 1; connected = false; clearPlayback(); stopMic(); snapshot = null;
    el('caption').textContent = ''; el('availability').textContent = ''; el('deliveryStatus').textContent = '';
    el('privateText').value = ''; el('inputStatus').textContent = ''; el('ownerSource').checked = false; textOperation = null;
    el('presenceControls').hidden = true; statusText('Private audience not confirmed.');
    const previousConnect = connectionFlight;
    pendingDisconnect = Promise.allSettled([pendingDisconnect, previousConnect])
      .then(() => post('disconnect')).catch(() => {});
  }
  document.addEventListener('DOMContentLoaded', () => {
    el('privateConfirm').addEventListener('change', () => { if (privateReady()) { el('presenceControls').hidden = false; void connect(); } else clearPrivate(); });
    el('reconnect').addEventListener('click', () => { void connect(); });
    el('voiceToggle').addEventListener('click', () => { if (media) { stopMic(); void post('control', {action: 'voice_enabled', value: false}).catch(() => {}); } else void startMic(); });
    el('muteToggle').addEventListener('click', async () => { if (!connected) return; const epoch = privacyEpoch; const value = !snapshot?.speech_enabled; if (!value) clearPlayback(true); try { await post('control', {action: 'speech_enabled', value}); if (valid(epoch)) updateState(await request(`${base}/status?${parameters()}`), epoch); } catch (error) { if (valid(epoch)) statusText(String(error.message || error)); } });
    el('stopPlayback').addEventListener('click', () => { void interrupt(); });
    el('sleepToggle').addEventListener('click', async () => { if (!connected) return; const epoch = privacyEpoch; if (snapshot?.awake) clearPlayback(true); try { await request('/api/coordination/control', {audience: 'owner_private', request_id: id(), action: snapshot?.awake ? 'sleep' : 'wake'}); if (valid(epoch)) await poll(); } catch (error) { if (valid(epoch)) statusText(String(error.message || error)); } });
    el('avatarToggle').addEventListener('click', () => { const avatar = el('avatar'); avatar.toggleAttribute('hidden'); el('avatarToggle').textContent = avatar.hasAttribute('hidden') ? 'Show avatar' : 'Hide avatar'; });
    el('compactToggle').addEventListener('click', () => { document.body.classList.toggle('compact'); el('compactToggle').textContent = document.body.classList.contains('compact') ? 'Expand' : 'Compact'; });
    el('placement').addEventListener('change', () => { document.body.dataset.placement = el('placement').value; try { localStorage.setItem('shana-presence-placement', el('placement').value); } catch (_) {} });
    try { const placement = localStorage.getItem('shana-presence-placement'); if (['left','right','center'].includes(placement)) { el('placement').value = placement; document.body.dataset.placement = placement; } } catch (_) {}
    el('textForm').addEventListener('submit', async event => {
      event.preventDefault(); if (!connected || !privateReady() || textPending) return;
      const text = el('privateText').value.trim(); if (!text) return;
      if (textOperation?.text !== text) textOperation = {text, requestId: id()};
      const operation = textOperation;
      const epoch = privacyEpoch; textPending = true; el('sendText').disabled = true; el('inputStatus').textContent = 'Sent to Shana…';
      try { const receipt = await post('input', {request_id: operation.requestId, session_id: 'desktop-private', text, observation: observation('desktop_text')}, 180000); if (valid(epoch)) { if (!['busy','pending_or_uncertain_no_replay'].includes(receipt.status)) { if (textOperation === operation) textOperation = null; el('privateText').value = ''; } el('inputStatus').textContent = receiptText(receipt); void poll(); } }
      catch (error) { if (valid(epoch)) el('inputStatus').textContent = `Result unconfirmed. ${String(error.message || error)}`; }
      finally { textPending = false; el('sendText').disabled = false; }
    });
    setInterval(() => { void poll(); }, 400);
    window.addEventListener('pagehide', () => { clearPlayback(); stopMic(); if (privateReady()) { const body = JSON.stringify({audience: 'owner_private', request_id: id(), consumer_id: consumerId}); void fetch(`${base}/disconnect`, {method: 'POST', headers: {'Content-Type':'application/json'}, body, keepalive: true, credentials: 'same-origin'}).catch(() => {}); } });
  });
}());
