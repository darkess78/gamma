(() => {
  'use strict';
  const $ = id => document.getElementById(id), prefix = '/api/coordination/shared-activity';
  let stream = null, source = null, sourceKind = null, epoch = 0, sequence = 0, busy = false, ready = false, localEpoch = 0, starting = false, selecting = false, pending = null, moment = null, watchRun = null, watchStarting = false, watchReady = false;
  const ui = () => window.ShanaUI;
  let player = null, audioConsumer = null;
  const listenNode = () => $('sharingListen');
  function audioValid(turn, selectedStream, selectedSource) {
    return turn === localEpoch && stream === selectedStream && source === selectedSource &&
      $('sharingPrivate').checked && listenNode().checked && stream?.getVideoTracks()[0]?.readyState !== 'ended';
  }
  function stopSpeech() {
    listenNode().checked = false;
    $('sharingSpeechStop').disabled = true;
    if (player) void player.disable();
  }
  async function listen() {
    if (!listenNode().checked) {stopSpeech(); return;}
    if (!stream || !source || !$('sharingPrivate').checked || !ready) {stopSpeech(); return;}
    if (!window.ShanaChannelAudioPlayer) {stopSpeech(); $('sharingSpeechStatus').textContent = 'Speech playback is unavailable.'; return;}
    if (!player) player = new window.ShanaChannelAudioPlayer({channel: 'shared_moment',
      notice: text => {$('sharingSpeechStatus').textContent = text;},
      update: state => {listenNode().checked = state.enabled; $('sharingSpeechStop').disabled = !state.enabled;}});
    audioConsumer = 'moments-' + ui().id();
    const turn = localEpoch, selectedStream = stream, selectedSource = source;
    await player.enable({consumerId: audioConsumer, valid: () => audioValid(turn, selectedStream, selectedSource)});
  }
  function finishWatch(run) {
    run.ended = true; clearTimeout(run.timer); clearTimeout(run.heartbeat); run.controller.abort();
    if (watchRun === run) watchRun = null;
    // Natural exhaustion keeps only an already accepted finite audio grant.
    // Explicit Stop watching, Stop speech or Stop sharing still revokes it.
    enabled();
  }
  const signal = () => pending ? {signal: pending.signal} : {};
  function enabled() {const active = Boolean(stream && source && $('sharingPrivate').checked); listenNode().disabled = !active || !ready; $('sharingStart').disabled = !$('sharingPrivate').checked || active || starting || watchStarting; $('sharingStop').disabled = !active && !starting && !watchStarting; $('sharingFrame').disabled = $('sharingLiveFrame').disabled = !active || !ready || busy || Boolean(watchRun) || watchStarting; $('sharingKind').disabled = active || starting || watchStarting; $('sharingGoalSave').disabled = !$('sharingPrivate').checked; $('sharingWatchStart').disabled = !active || !watchReady || busy || Boolean(watchRun) || watchStarting; $('sharingWatchStop').disabled = !watchRun && !watchStarting; $('sharingWatchMinutes').disabled = Boolean(watchRun) || watchStarting;}
  function current(run) {return moment === run && !run.ended && run.turn === localEpoch && stream === run.stream && source === run.source && $('sharingPrivate').checked;}
  function endMoment(run) {
    if (!run || run.ended) return;
    run.ended = true; clearTimeout(run.timer); clearTimeout(run.expiry);
    run.primary.abort(); run.refresh?.abort(); run.resolveReady?.(null);
    if (run.ownerSignal) run.ownerSignal.removeEventListener('abort', run.ownerAbort);
    if (moment === run) {moment = null; busy = false; enabled();}
  }
  async function revoke(keepalive = false) {await ui().request(prefix + '/control', {action: 'stop', values: {}}, {keepalive});}
  async function stop({keepalive = false} = {}) {
    stopSpeech();
    stopWatch({keepalive});
    endMoment(moment);
    localEpoch += 1; const active = Boolean(source || selecting); source = null;
    sourceKind = null;
    if (pending) pending.abort(); pending = null;
    const previous = stream; stream = null; if (previous) previous.getTracks().forEach(track => track.stop());
    ready = false;
    $('sharingPreview').srcObject = null; $('sharingPreview').hidden = true;
    $('sharingGoal').value = ''; $('sharingGoalStatus').textContent = '';
    $('sharingState').textContent = 'Not sharing'; $('sharingObservation').textContent = 'Sharing stopped.'; $('sharingReactions').replaceChildren(); enabled();
    if (active) {try {await revoke(keepalive);} catch {ui().notice($('sharingNotice'), 'The window is closed locally. Server access will also expire; check the connection before sharing again.', true);}}
  }
  async function start() {
    if (!$('sharingPrivate').checked || stream || starting) return;
    starting = true; enabled();
    if (!pending || pending.signal.aborted) pending = new AbortController();
    const turn = ++localEpoch; let candidate;
    try {
      if (!navigator.mediaDevices?.getDisplayMedia) throw new Error('Window sharing needs Brave on a secure connection or localhost.');
      candidate = await navigator.mediaDevices.getDisplayMedia({video: {frameRate: 1}, audio: false});
      if (turn !== localEpoch || !$('sharingPrivate').checked) {candidate.getTracks().forEach(t => t.stop()); return;}
      const track = candidate.getVideoTracks()[0], settings = track.getSettings();
      if (!['window','browser'].includes(settings.displaySurface)) throw new Error('Choose a single window or browser tab instead of your whole screen.');
      const latest = await ui().request(prefix + '/status?audience=owner_private', undefined, signal());
      if (turn !== localEpoch || !$('sharingPrivate').checked) {candidate.getTracks().forEach(t => t.stop()); return;}
      epoch = latest.state.source_epoch; ready = latest.image_analysis_available; watchReady = latest.watch_available;
      const nextSource = 'window-' + ui().id();
      selecting = true;
      const kind = $('sharingKind').value;
      const selected = await ui().request(prefix + '/control', {action: 'select', values: {source_id: nextSource, label: (track.label || 'Selected window').slice(0,80), kind, seconds: 900, maximum_reviews: 30, review_interval_seconds: 15, maximum_frames: 120, expected_epoch: epoch}}, signal());
      if (turn !== localEpoch || !$('sharingPrivate').checked) {candidate.getTracks().forEach(t => t.stop()); await revoke(); return;}
      stream = candidate; source = nextSource; sourceKind = kind; epoch = selected.source_epoch; sequence = 0;
      track.addEventListener('ended', stop, {once: true});
      $('sharingPreview').srcObject = stream; $('sharingPreview').hidden = false; $('sharingState').textContent = 'Sharing one window'; enabled();
      ui().notice($('sharingNotice'), 'Window selected. Share this moment, or explicitly start a finite watch.');
    } catch (error) {if (candidate) candidate.getTracks().forEach(t => t.stop()); if (selecting) {try {await revoke();} catch {}} if (turn === localEpoch) ui().notice($('sharingNotice'), error.name === 'NotAllowedError' ? 'No window was shared.' : error.message, true);} finally {starting = false; selecting = false; enabled();}
  }
  async function liveFrame() {
    if (busy || !stream || !ready || !$('sharingPrivate').checked) return;
    let resolveReady;
    const firstView = new Promise(resolve => {resolveReady = resolve;});
    const run = {turn: localEpoch, stream, source, kind: sourceKind, serverEpoch: epoch,
      primary: new AbortController(), refresh: new AbortController(), resolveReady,
      refreshBusy: false, ended: false, ownerSignal: pending?.signal};
    run.ownerAbort = () => endMoment(run);
    if (run.ownerSignal) run.ownerSignal.addEventListener('abort', run.ownerAbort, {once: true});
    moment = run; busy = true; enabled();
    function capture() {
      if (!current(run)) throw new DOMException('Sharing ended.', 'AbortError');
      const video = $('sharingPreview');
      if (!video.videoWidth || !video.videoHeight) throw new Error('The selected window is not ready yet.');
      const scale = Math.min(1, 960 / video.videoWidth, 540 / video.videoHeight);
      const canvas = document.createElement('canvas'); canvas.width = Math.max(1, Math.round(video.videoWidth * scale)); canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
      const capturedAt = Date.now()/1000;
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      return {metadata: {observation_id: 'view-' + ui().id(), source_id: run.source, source_epoch: run.serverEpoch,
        sequence: sequence++, captured_at: capturedAt, kind: run.kind},
        image_base64: canvas.toDataURL('image/jpeg', .7).split(',')[1], media_type: 'image/jpeg'};
    }
    function accepted(result) {
      if (!current(run)) return;
      if (result.status === 'unavailable') throw new Error('Screen understanding is not connected yet.');
      if (['interpreted','refreshed'].includes(result.status)) run.resolveReady(result);
    }
    function schedule() {if (current(run)) run.timer = setTimeout(refresh, 2000);}
    async function refresh() {
      if (!current(run) || run.refreshBusy) return;
      run.refreshBusy = true;
      try {
        const result = await ui().request(prefix + '/image', capture(), {signal: run.refresh.signal});
        accepted(result);
      } catch (error) {
        if (current(run)) {ui().notice($('sharingNotice'), error.message, true); endMoment(run);}
      } finally {run.refreshBusy = false; schedule();}
    }
    run.expiry = setTimeout(async () => {
      if (!current(run)) return;
      endMoment(run); const stoppedTurn = localEpoch + 1;
      await stop();
      if (localEpoch === stoppedTurn && $('sharingPrivate').checked) ui().notice($('sharingNotice'), 'The 45-second moment ended and sharing stopped. Choose a window to try another moment.');
    }, 45000);
    schedule();
    try {
      ui().notice($('sharingNotice'), 'Sharing fresh frames while Shana looks and responds, for up to 45 seconds.');
      accepted(await ui().request(prefix + '/image', capture(), {signal: run.primary.signal}));
      const interpreted = await firstView;
      if (!interpreted || !current(run)) return;
      const state = await ui().request(prefix + '/status?audience=owner_private', undefined, {signal: run.primary.signal});
      if (!current(run)) return;
      if (!state.state.enabled || state.state.source_epoch !== run.serverEpoch || !state.context.observation) throw new Error('The view changed before it could be interpreted. Share another moment when it settles.');
      $('sharingObservation').textContent = state.context.observation?.summary || 'This view is no longer current.';
      const reaction = await ui().request(prefix + '/review', {}, {signal: run.primary.signal});
      if (!current(run)) return;
      if (reaction.text) ui().feed($('sharingReactions'), reaction.text);
      ui().notice($('sharingNotice'), reaction.status === 'cancelled' ? 'The view changed before the response was ready. No more frames are being sent.' : reaction.status === 'silent' ? 'Shana chose quiet company. No more frames are being sent.' : 'Moment shared. No more frames are being sent.');
    } catch (error) {if (current(run) && error.name !== 'AbortError') ui().notice($('sharingNotice'), error.message, true);} finally {endMoment(run);}
  }
  function captureMoment(run) {
    if (!current(run)) throw new DOMException('Sharing ended.', 'AbortError');
    const video = $('sharingPreview');
    if (!video.videoWidth || !video.videoHeight) throw new Error('The selected window is not ready yet.');
    const scale = Math.min(1, 960 / video.videoWidth, 540 / video.videoHeight), canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale)); canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    const capturedAt = Date.now()/1000;
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    return {metadata: {observation_id: 'view-' + ui().id(), source_id: run.source, source_epoch: run.serverEpoch,
      sequence: sequence++, captured_at: capturedAt, kind: run.kind},
      image_base64: canvas.toDataURL('image/jpeg', .7).split(',')[1], media_type: 'image/jpeg'};
  }
  async function textReceived(run, result) {
    if (!current(run) || !result.text || !result.text_sha256) return;
    const feed = $('sharingReactions');
    if (!Array.from(feed.children).some(node => node.textContent === result.text)) return;
    const receipt = {request_id: 'receipt-' + ui().id(), moment_id: result.id, text_sha256: result.text_sha256};
    const cid = player?.enabled && player?.connected && listenNode().checked ? audioConsumer : null;
    if (cid) receipt.consumer_id = cid;
    for (let attempt = 0; attempt < 2 && current(run); attempt++) {
      try {const accepted = await ui().request(prefix + '/moment/received', receipt, {signal: run.primary.signal});
        if (current(run) && cid === audioConsumer && accepted.audio_consumer_bound && listenNode().checked) void player.renderMoment(result.id);
        return;} catch (error) {if (error.name === 'AbortError') return;}
    }
  }
  async function frame(watch = null) {
    if (busy || !stream || !ready || !$('sharingPrivate').checked || watchStarting || (!watch && watchRun)) return null;
    const run = {turn: localEpoch, stream, source, kind: sourceKind, serverEpoch: epoch,
      primary: new AbortController(), ended: false, watch};
    moment = run; busy = true; enabled();
    run.expiry = setTimeout(() => {if (current(run)) stop();}, 45000);
    try {
      if (!watch) {
        const status = await ui().request(prefix + '/status?audience=owner_private', undefined, {signal: run.primary.signal});
        if (!current(run)) return null;
        if (!status.state.enabled || status.state.source_id !== source || status.state.source_epoch !== epoch) throw new Error('Window permission changed. Choose the window again.');
        if (status.state.remaining_reviews <= 0 || status.state.remaining_frames <= 0) {
          // This explicit owner click grants one new moment, never refills the old watch.
          stopSpeech();
          const selected = await ui().request(prefix + '/control', {action: 'select', values: {source_id: source,
            label: (stream.getVideoTracks()[0].label || 'Selected window').slice(0,80), kind: sourceKind,
            seconds: 180, maximum_reviews: 1, review_interval_seconds: 10, maximum_frames: 1, expected_epoch: epoch}}, {signal: run.primary.signal});
          if (!current(run)) return null;
          epoch = run.serverEpoch = selected.source_epoch; sequence = 0;
        }
      }
      const payload = captureMoment(run);
      $('sharingObservation').textContent = 'Captured at ' + new Date(payload.metadata.captured_at * 1000).toLocaleTimeString() + '. Interpreting this past moment; current state is unknown.';
      ui().notice($('sharingNotice'), 'One captured image is being interpreted. No additional frames are sent for this moment.');
      const response = await ui().request(prefix + (watch ? '/watch/moment' : '/moment'),
        watch ? {...payload, watch_id: watch.row.id, lease: watch.row.lease} : payload, {signal: run.primary.signal});
      if (!current(run) || (watch && !watchCurrent(watch))) return null;
      const result = watch ? response.moment : response;
      if (result.captured_at !== undefined) {
        $('sharingObservation').textContent = 'Captured at ' + new Date(result.captured_at * 1000).toLocaleTimeString() +
          ' (' + Math.round(result.age_seconds) + ' seconds before this result). ' + (result.summary || '') + ' Current state is unknown.';
      }
      if (result.status === 'generated' && result.text) {ui().feed($('sharingReactions'), result.text); await textReceived(run, result);}
      if (!current(run)) return null;
      ui().notice($('sharingNotice'), result.status === 'silent' ? 'Shana chose quiet company for that captured moment.' :
        result.status === 'generated' ? 'Reflection on the captured moment. It does not describe the current view.' :
        result.status === 'busy' || result.status === 'deferred' ? 'Shana is occupied or giving this moment space.' : 'This moment was withheld. No old output will be replayed.');
      return response;
    } catch (error) {
      if (current(run) && error.name !== 'AbortError') {ui().notice($('sharingNotice'), error.message + ' No automatic retry.', true); if (watch) stopWatch();}
      return null;
    } finally {endMoment(run);}
  }
  function watchCurrent(run) {return watchRun === run && !run.ended && run.turn === localEpoch && run.stream === stream && run.source === source && $('sharingPrivate').checked;}
  function showWatch(row) {$('sharingWatchStatus').textContent = row.status === 'active' ? 'Watching for up to ' + row.minutes + ' minutes: ' + row.remaining_moments + ' moments left. Quiet is a valid choice.' : 'Watch ended: ' + row.reason.replaceAll('_', ' ') + '.';}
  async function stopWatch({keepalive = false} = {}) {
    stopSpeech();
    const run = watchRun; watchRun = null;
    if (watchStarting && !run) {watchStarting = false; await stop({keepalive}); return;}
    if (run) {
      run.ended = true; clearTimeout(run.timer); clearTimeout(run.heartbeat); run.controller.abort();
      if (moment?.watch === run) endMoment(moment);
      $('sharingWatchStatus').textContent = 'Watch stopped. It will not resume automatically.';
      try {await ui().request(prefix + '/watch/stop', {watch_id: run.row.id, lease: run.row.lease}, {keepalive});} catch {}
    }
    enabled();
  }
  function scheduleWatch(run) {
    if (!watchCurrent(run)) return;
    if (run.row.status !== 'active') {showWatch(run.row); if (run.row.status === 'exhausted') finishWatch(run); else stopWatch(); return;}
    run.timer = setTimeout(async () => {
      if (!watchCurrent(run)) return;
      const result = await frame(run);
      if (!watchCurrent(run)) return;
      if (!result) {stopWatch(); return;}
      run.row = result.watch; showWatch(run.row); scheduleWatch(run);
    }, Math.max(1000, Math.min(120000, run.row.next_sample_at * 1000 - Date.now())));
  }
  function heartbeatWatch(run) {
    if (!watchCurrent(run)) return;
    run.heartbeat = setTimeout(async () => {
      if (!watchCurrent(run)) return;
      try {
        const row = await ui().request(prefix + '/watch/heartbeat', {watch_id: run.row.id, lease: run.row.lease}, {signal: run.controller.signal});
        if (!watchCurrent(run)) return;
        if (row.status !== 'active' || row.consumed_moments >= run.row.consumed_moments) run.row = row;
        showWatch(run.row);
        if (row.status !== 'active') {
          // The last generated result is allowed its original receipt window.
          // Do not revoke it while that same request is arriving at the DOM.
          if (row.status === 'exhausted' && moment?.watch === run) return;
          if (row.status === 'exhausted') finishWatch(run); else stopWatch(); return;
        }
        heartbeatWatch(run);
      } catch {if (watchCurrent(run)) stopWatch();}
    }, 10000);
  }
  async function startWatch() {
    if (watchRun || watchStarting || busy || !stream || !source || !watchReady || !$('sharingPrivate').checked) return;
    const turn = localEpoch, selectedStream = stream, selectedSource = source;
    const minutes = Number($('sharingWatchMinutes').value), quota = ({5:10,15:20,30:30})[minutes];
    if (!quota) return;
    stopSpeech();
    watchStarting = true; selecting = true; enabled();
    try {
      const status = await ui().request(prefix + '/status?audience=owner_private', undefined, signal());
      if (turn !== localEpoch || selectedStream !== stream || !$('sharingPrivate').checked) return;
      if (!status.state.enabled || status.state.source_id !== selectedSource || status.state.source_epoch !== epoch) throw new Error('Window permission changed. Choose the window again.');
      const selected = await ui().request(prefix + '/control', {action: 'select', values: {source_id: source,
        label: (stream.getVideoTracks()[0].label || 'Selected window').slice(0,80), kind: sourceKind,
        seconds: minutes * 60, maximum_reviews: quota, review_interval_seconds: 10, maximum_frames: quota, expected_epoch: epoch}}, signal());
      if (turn !== localEpoch || selectedStream !== stream || !$('sharingPrivate').checked) {await revoke(); return;}
      epoch = selected.source_epoch; sequence = 0;
      const row = await ui().request(prefix + '/watch/start', {source_id: source, source_epoch: epoch, minutes}, signal());
      if (turn !== localEpoch || selectedStream !== stream || !$('sharingPrivate').checked) {try {await ui().request(prefix + '/watch/stop', {watch_id: row.id, lease: row.lease});} catch {} return;}
      const run = {turn, stream, source, row, ended: false, controller: new AbortController()};
      watchRun = run; showWatch(row); heartbeatWatch(run); scheduleWatch(run);
    } catch (error) {if (turn === localEpoch && $('sharingPrivate').checked && error.name !== 'AbortError') {await stop(); ui().notice($('sharingWatchStatus'), error.message + ' Sharing stopped; choose the window before another attempt.', true);}}
    finally {watchStarting = false; selecting = false; enabled();}
  }
  async function goal(event) {
    event.preventDefault();
    if (!$('sharingPrivate').checked) return;
    const turn = localEpoch; stopSpeech(); endMoment(moment);
    try {await ui().request(prefix + '/control', {action: 'goal', values: {text: $('sharingGoal').value}}, signal()); if (turn !== localEpoch || !$('sharingPrivate').checked) return; await ui().request(prefix + '/control', {action: 'mode', values: {mode: $('sharingMode').value}}, signal()); if (turn === localEpoch && $('sharingPrivate').checked) ui().notice($('sharingGoalStatus'), 'Shared context saved.');} catch (error) {if (turn === localEpoch && $('sharingPrivate').checked && error.name !== 'AbortError') ui().notice($('sharingGoalStatus'), error.message, true);}
  }
  async function game(action, name) {
    try {await ui().request(prefix + '/minecraft/' + action, name ? {name, arguments: {}} : {}); ui().notice($('sharingNotice'), action === 'revoke' ? 'Emergency stop requested.' : 'Test request sent. Check the observed result.');} catch (error) {ui().notice($('sharingNotice'), error.message, true);}
  }
  async function readiness() {
    if (!$('sharingPrivate').checked) return;
    const turn = localEpoch;
    try {const status = await ui().request(prefix + '/status?audience=owner_private', undefined, signal()); if (turn !== localEpoch || !$('sharingPrivate').checked) return; ready = status.image_analysis_available; watchReady = status.watch_available; epoch = status.state.source_epoch; $('sharingReadiness').textContent = ready ? 'Ready to share a captured moment. The first image checks the configured model.' : 'Screen understanding is not connected yet. No frame will be sent.'; $('sharingGoal').value = status.context.shared_goal || ''; $('sharingMode').value = status.context.mode || 'relaxed'; enabled();} catch (error) {if (turn === localEpoch) ui().notice($('sharingNotice'), error.message, true);}
    if (turn !== localEpoch || !$('sharingPrivate').checked) return;
    try {await ui().request(prefix + '/minecraft/status?audience=owner_private', undefined, signal()); if (turn !== localEpoch || !$('sharingPrivate').checked) return; $('gameConnection').textContent = 'Private test-world controls are connected.'; $('gamePermit').disabled = $('gameStop').disabled = false; document.querySelectorAll('[data-game]').forEach(b => b.disabled = false);} catch {if (turn === localEpoch) $('gameConnection').textContent = 'No disposable test world is connected.';}
  }
  document.addEventListener('DOMContentLoaded', () => {listenNode().addEventListener('change', listen); $('sharingSpeechStop').addEventListener('click', stopSpeech); $('sharingPrivate').addEventListener('change', () => {if ($('sharingPrivate').checked) {pending = typeof AbortController === 'function' ? new AbortController() : null; enabled(); readiness();} else stop();}); $('sharingStart').addEventListener('click', start); $('sharingStop').addEventListener('click', stop); $('sharingFrame').addEventListener('click', () => frame()); $('sharingLiveFrame').addEventListener('click', liveFrame); $('sharingWatchStart').addEventListener('click', startWatch); $('sharingWatchStop').addEventListener('click', () => stopWatch()); $('sharingGoalForm').addEventListener('submit', goal); $('gamePermit').addEventListener('click', () => game('permit')); $('gameStop').addEventListener('click', () => game('revoke')); document.querySelectorAll('[data-game]').forEach(b => b.addEventListener('click', () => game('action', b.dataset.game))); enabled();});
  window.addEventListener('pagehide', () => stop({keepalive: true}));
})();
