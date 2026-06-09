// monitor.js - Monitor-related functions for Gamma dashboard
(function () {
  let subscriberId = null;
  let ws = null;
  let muted = false;
  let audioEnabled = false;
  let playing = false;
  let audioQueue = [];
  let lastSequence = 0;

  const params = new URLSearchParams(location.search);
  const token = params.get('token') || '';
  const replayRecent = params.get('replay_recent') || '20';
  const targetPolicy = params.get('target_policy') || 'dashboard_monitor';
  const clientName = params.get('client_name') || 'gaming_pc_monitor';
  const apiBase = browserReachableApiBase(params.get('api_base') || window.GAMMA_SHANA_BASE_URL || location.origin);
  const dashboardBase = browserReachableApiBase(params.get('dashboard_base') || window.GAMMA_DASHBOARD_BASE_URL || '');
  const audio = document.getElementById('audioEngine');

  function setMonitorTheme(theme) {
    const selected = ['dashboard', 'compact', 'focus'].includes(theme) ? theme : 'dashboard';
    const themeSelect = document.getElementById('themeSelect');
    if (themeSelect && themeSelect.value !== selected) {
      themeSelect.value = selected;
    }
    document.body.classList.remove('theme-dashboard', 'theme-compact', 'theme-focus');
    document.body.classList.add(`theme-${selected}`);
    localStorage.setItem('gammaMonitorTheme', selected);
  }

  function enableAudio() {
    if (!audio || !audioEnabled) return;
    audioEnabled = true;
    if (!audio) return;
    const button = document.getElementById('audioEnableButton');
    const gate = document.getElementById('audioGate');
    const text = document.getElementById('audioGateText');
    if (button) {
      button.textContent = 'Audio Enabled';
      button.disabled = true;
    }
    if (gate) gate.classList.add('enabled');
    if (text) text.textContent = 'Monitor audio is enabled for future Shana speech.';
    playNextAudio();
  }

  function browserReachableApiBase(rawBase) {
    const value = String(rawBase || '').replace(/\/$/, '');
    if (!value) return '';
    try {
      const apiUrl = new URL(value, location.origin);
      const browserHost = location.hostname;
      const apiIsLocal = ['127.0.0.1', 'localhost', '0.0.0.0', '::1'].includes(apiUrl.hostname);
      const browserIsLocal = ['127.0.0.1', 'localhost', '::1'].includes(browserHost);
      if (apiIsLocal && browserHost && !browserIsLocal) {
        apiUrl.hostname = browserHost;
      }
      return apiUrl.toString().replace(/\/$/, '');
    } catch (error) {
      return value;
    }
  }

  function outputViewQuery(apiBase, values) {
    const query = new URLSearchParams();
    if (apiBase) {
      query.set('api_base', apiBase);
    }
    Object.keys(values || {}).forEach(key => {
      if (values[key]) {
        query.set(key, values[key]);
      }
    });
    const text = query.toString();
    return text ? '?' + text : '';
  }

  function updateOutputLinks() {
    const performerQuery = outputViewQuery('', {
      target_policy: 'stream_public',
      client_name: 'stream_pc_performer'
    });
    const subtitlesQuery = outputViewQuery(apiBase, {
      target_policy: 'stream_public',
      client_name: 'stream_pc_subtitle_overlay'
    });
    document.querySelectorAll('[data-output-link="performer"]').forEach(link => {
      link.href = `${apiBase || '/performer'}${performerQuery}`;
    });
    document.querySelectorAll('[data-output-link="subtitles"]').forEach(link => {
      link.href = `/overlay/subtitles${subtitlesQuery}`;
    });
    document.querySelectorAll('[data-dashboard-link]').forEach(link => {
      link.href = (dashboardBase || '/dashboard');
    });
  }

  function connect() {
    const apiUrl = new URL(apiBase);
    const protocol = apiUrl.protocol === 'https:' ? 'wss:' : 'ws:';
    const queryString = new URLSearchParams({
      replay_recent: replayRecent,
      target_policy: targetPolicy,
      client_name: clientName
    });
    if (lastSequence > 0) {
      queryString.set('after_sequence', String(lastSequence));
    }
    if (token) {
      queryString.set('token', token);
    }
    const url = `${protocol}//${apiUrl.host}/v1/performer/events?${queryString.toString()}`;
    
    ws = new WebSocket(url);
    
    ws.onopen = () => {
      if (document.getElementById('connectionDot')) {
        document.getElementById('connectionDot').classList.add('connected');
        document.getElementById('connectionDot').classList.remove('disconnected');
      }
      if (document.getElementById('connectionText')) {
        document.getElementById('connectionText').textContent = 'Connected';
      }
      console.log('Monitor connected to performer events');
    };
    
    ws.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      updateMonitor(payload);
    };
    
    ws.onclose = () => {
      if (document.getElementById('connectionDot')) {
        document.getElementById('connectionDot').classList.remove('connected');
        document.getElementById('connectionDot').classList.add('disconnected');
      }
      if (document.getElementById('connectionText')) {
        document.getElementById('connectionText').textContent = 'Disconnected';
      }
      setTimeout(connect, 2000);
    };
    
    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };
  }

  function updateMonitor(payload) {
    if (payload.type === 'ready') {
      subscriberId = payload.subscriber_id || subscriberId;
      if (document.getElementById('subscriberId')) {
        document.getElementById('subscriberId').textContent = subscriberId || '...';
      }
      if (payload.stats && document.getElementById('historyCount')) {
        document.getElementById('historyCount').textContent = payload.stats.history_count || 0;
      }
      if (payload.replay_recent) {
        document.getElementById('replayWindow').textContent = replayLabel(payload);
      }
      if (targetMutedFromStats(payload.stats)) {
        if (document.getElementById('turnState')) {
          document.getElementById('turnState').textContent = 'muted';
          document.getElementById('turnState').classList.remove('active');
        }
        clearAudio();
      }
      return;
    }
    
    if (isStaleEvent(payload)) {
      return;
    }
    
    if (payload.sequence && payload.sequence > lastSequence) {
      lastSequence = payload.sequence;
    }

    // Update turn info
    if (document.getElementById('turnId')) {
      document.getElementById('turnId').textContent = payload.turn_id || '...';
    }
    if (document.getElementById('turnSequence')) {
      document.getElementById('turnSequence').textContent = payload.sequence || '...';
    }
    if (document.getElementById('turnSource')) {
      document.getElementById('turnSource').textContent = payload.source || 'stream_output';
    }
    if (document.getElementById('turnPolicy')) {
      document.getElementById('turnPolicy').textContent = payload.target_policy || 'stream_public';
    }
    if (document.getElementById('turnInput')) {
      document.getElementById('turnInput').textContent = inputLabel(payload.payload?.input);
    }
    if (document.getElementById('turnActor')) {
      document.getElementById('turnActor').textContent = actorLabel(payload.payload?.actor);
    }
    
    // Update turn state
    if (payload.type === 'turn_started') {
      if (document.getElementById('turnState')) {
        document.getElementById('turnState').textContent = 'started';
        document.getElementById('turnState').classList.add('active');
      }
    } else if (payload.type === 'turn_state_changed') {
      if (document.getElementById('turnState')) {
        document.getElementById('turnState').textContent = payload.payload?.state || 'unknown';
        document.getElementById('turnState').classList.remove('active');
      }
    } else if (payload.type === 'speech_started' || payload.type === 'speech_chunk_ready') {
      if (document.getElementById('turnState')) {
        document.getElementById('turnState').textContent = 'speaking';
        document.getElementById('turnState').classList.add('active');
      }
    } else if (payload.type === 'speech_ended') {
      if (document.getElementById('turnState')) {
        document.getElementById('turnState').textContent = 'completed';
        document.getElementById('turnState').classList.remove('active');
      }
    } else if (payload.type === 'output_cleared') {
      if (document.getElementById('turnState')) {
        document.getElementById('turnState').textContent = 'cleared';
        document.getElementById('turnState').classList.remove('active');
      }
      clearAudio();
    } else if (payload.type === 'target_mute_changed') {
      if (document.getElementById('turnState')) {
        document.getElementById('turnState').textContent = payload.payload?.muted ? 'muted' : 'unmuted';
        document.getElementById('turnState').classList.remove('active');
      }
      if (payload.payload?.muted) {
        clearAudio();
      }
    } else {
      if (document.getElementById('turnState')) {
        document.getElementById('turnState').textContent = 'idle';
        document.getElementById('turnState').classList.remove('active');
      }
    }
    
    // Update subtitle
    if (payload.type === 'subtitle_update') {
      const text = payload.payload?.text || '';
      const clear = payload.payload?.clear || false;
      if (clear) {
        if (document.getElementById('subtitleDisplay')) {
          document.getElementById('subtitleDisplay').textContent = '';
        }
      } else {
        if (document.getElementById('subtitleDisplay')) {
          document.getElementById('subtitleDisplay').textContent = text;
        }
      }
    } else if (payload.type === 'subtitle_clear') {
      if (document.getElementById('subtitleDisplay')) {
        document.getElementById('subtitleDisplay').textContent = '';
      }
    }
    
    // Update expression
    if (payload.type === 'expression_set') {
      const expression = payload.payload?.expression || 'neutral';
      if (document.getElementById('expressionDisplay')) {
        const el = document.getElementById('expressionDisplay');
        el.textContent = expression;
        el.className = 'expression-display expression-' + expression;
      }
    }
    
    // Update queue
    if (payload.type === 'turn_state_changed' && payload.payload?.queue_size !== undefined) {
      const queueSize = payload.payload.queue_size || 0;
      if (document.getElementById('queueSize')) {
        document.getElementById('queueSize').textContent = queueSize;
      }
      const indicator = document.getElementById('queueIndicator');
      if (indicator) {
        if (queueSize > 0) {
          indicator.textContent = `${queueSize} item(s) in queue`;
          indicator.style.color = queueSize > 5 ? 'var(--warning)' : 'var(--text)';
        } else {
          indicator.textContent = 'No queue activity';
          indicator.style.color = 'var(--muted)';
        }
      }
    }
    
    // Update history
    if (payload.type === 'turn_state_changed' && payload.paint?.history_count !== undefined) {
      const history = payload.payload?.history_count || 0;
      if (document.getElementById('historyCount')) {
        document.getElementById('historyCount').textContent = history;
      }
    }

    if (payload.type === 'speech_started' || payload.type === 'speech_chunk_ready') {
      enqueueAudio(payload);
    }
    
    if (payload.type === 'speech_ended') {
      const indicator = document.getElementById('queueIndicator');
      if (indicator) {
        indicator.textContent = audioQueue.length ? `${audioQueue.length} audio item(s) queued` : 'No queue activity';
      }
    }
  }

  function targetMutedFromStats(stats) {
    const mutedTargets = stats && Array.isArray(stats.muted_targets) ? stats.muted_targets : [];
    return mutedTargets.includes(targetPolicy);
  }

  function isStaleEvent(payload) {
    return !!(payload && payload.sequence && lastSequence > 0 && payload.sequence <= lastSequence);
  }

  function replayLabel(payload) {
    const gap = payload.replay_gap ? ' / gap' : '';
    return `${payload.replay_recent ?? replayRecent} / after ${payload.after_sequence ?? 0}${gap}`;
  }

  function actorLabel(actor) {
    actor = actor || {};
    const source = actor.source || 'unknown';
    const name = actor.display_name || actor.platform_id || 'unknown';
    const roles = Array.isArray(actor.roles) && actor.roles.length ? ` [${actor.roles.join(', ')}]` : '';
    return `${source}:${name}${roles}`;
  }

  function inputLabel(input) {
    input = input || {};
    const kind = input.kind || 'unknown';
    return input.session_id ? `${kind} / ${input.session_id}` : kind;
  }

  function enqueueAudio(payload) {
    const url = audioSourceFromEvent(payload);
    if (!url) {
      return;
    }
    audioQueue.push(url);
    if (document.getElementById('queueSize')) {
      document.getElementById('queueSize').textContent = audioQueue.length + (playing ? 1 : 0);
    }
    const indicator = document.getElementById('queueIndicator');
    if (indicator) {
      indicator.textContent = `${audioQueue.length} audio item(s) queued`;
    }
    playNextAudio();
  }

  function audioSourceFromEvent(payload) {
    const eventPayload = payload.payload || {};
    if (eventPayload.audio_url) {
      return eventPayload.audio_url;
    }
    if (eventPayload.audio_base64 && eventPayload.audio_content_type) {
      return `data:${eventPayload.audio_content_type};base64,${eventPayload.audio_base64}`;
    }
    return '';
  }

  function playNextAudio() {
    if (!audioEnabled || muted || playing || audioQueue.length === 0) {
      return;
    }
    const url = audioQueue.shift();
    playing = true;
    if (audio) {
      audio.src = url;
      audio.play().catch(() => {
        playing = false;
        playNextAudio();
      });
    }
    if (document.getElementById('queueSize')) {
      document.getElementById('queueSize').textContent = audioQueue.length + 1;
    }
  }

  function clearAudio() {
    audioQueue = [];
    playing = false;
    if (!audio) return;
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    if (document.getElementById('queueSize')) {
      document.getElementById('queueSize').textContent = '0';
    }
    const indicator = document.getElementById('queueIndicator');
    if (indicator) {
      indicator.textContent = 'No queue activity';
    }
  }

  function toggleMute() {
    muted = !muted;
    const button = document.getElementById('muteButton');
    if (button) {
      button.textContent = muted ? 'Unmute Monitor' : 'Mute Monitor';
    }
    if (muted) {
      if (audio) audio.pause();
    } else {
      playing = false;
      playNextAudio();
    }
  }

  function clearOutput() {
    fetch(`${apiBase}/v1/performer/targets/${encodeURIComponent(targetPolicy)}/clear?reason=monitor_clear`, { method: 'POST' })
      .then(() => {
        clearAudio();
        if (document.getElementById('subtitleDisplay')) {
          document.getElementById('subtitleDisplay').textContent = '';
        }
        if (document.getElementById('turnState')) {
          document.getElementById('turnState').textContent = 'cleared';
        }
        const indicator = document.getElementById('queueIndicator');
        if (indicator) {
          indicator.textContent = 'Output cleared';
        }
      })
      .catch(err => {
        console.error('Failed to clear output:', err);
        alert('Failed to clear output');
      });
  }

  if (audio) {
    audio.addEventListener('ended', () => {
      playing = false;
      const queueSizeEl = document.getElementById('queueSize');
      if (queueSizeEl) {
        queueSizeEl.textContent = audioQueue.length;
      }
      playNextAudio();
    });

    audio.addEventListener('error', () => {
      playing = false;
      const queueSizeEl = document.getElementById('queueSize');
      if (queueSizeEl) {
        queueSizeEl.textContent = audioQueue.length;
      }
      playNextAudio();
    });
    }

  // Initialize
  setMonitorTheme(localStorage.getItem('gammaMonitorTheme') || 'dashboard');
  updateOutputLinks();
  connect();
})();