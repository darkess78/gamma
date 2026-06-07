// nav.js - Navigation and UI helper functions for the Gamma dashboard
(function () {
  var viewMode = localStorage.getItem('gammaDashboardViewMode') || 'human';
  var dashboardPage = String(window.GAMMA_DASHBOARD_PAGE || '').trim().toLowerCase() || dashboardPageFromPath();
  var dashboardPageTabs = {
    dashboard: ['dashboard-overview'],
    live: ['voice'],
    status: ['status', 'providers', 'logs'],
    stream: ['stream'],
    memory: ['memory'],
    settings: ['providers', 'settings']
  };
  var dashboardActiveTab = dashboardPageTabs[dashboardPage]
    ? dashboardPageTabs[dashboardPage][0]
    : (localStorage.getItem('gammaDashboardActiveTab') || 'overview');
  var sectionHashes = {};
  var runtimePollMs = 10000;

  function dashboardPageFromPath() {
    var path = String(window.location.pathname || '').replace(/\/+$/, '') || '/';
    if (path === '/' || path === '/dashboard') return 'dashboard';
    if (path.indexOf('/dashboard/') === 0) {
      return path.slice('/dashboard/'.length).split('/')[0] || 'dashboard';
    }
    return 'dashboard';
  }

  function currentDashboardTabs() {
    return dashboardPageTabs[dashboardPage] || [dashboardActiveTab || 'overview'];
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function updateOutputViewLinks() {
    var apiBase = outputViewApiBase();
    var dashboardBase = dashboardPublicBase();
    var monitors = document.querySelectorAll('[data-output-view="monitor"]');
    var performers = document.querySelectorAll('[data-output-view="performer"]');
    var subtitles = document.querySelectorAll('[data-output-view="subtitles"]');
    var status = document.getElementById('outputViewApiStatus');
    var shanaHealth = latestData && latestData.shana && latestData.shana.api_health ? latestData.shana.api_health : null;
    var shanaRunning = latestData && latestData.shana && latestData.shana.process ? !!latestData.shana.process.running : false;
    var apiReachable = !!(shanaHealth && shanaHealth.ok);
    var monitorQuery = outputViewQuery(apiBase, {
      target_policy: 'dashboard_monitor',
      client_name: 'gaming_pc_monitor'
    });
    var performerQuery = outputViewQuery('', {
      target_policy: 'stream_public',
      client_name: 'stream_pc_performer'
    });
    var subtitleQuery = outputViewQuery(apiBase, {
      target_policy: 'stream_public',
      client_name: 'stream_pc_subtitle_overlay'
    });
    document.querySelectorAll('[data-dashboard-route]').forEach(function (link) {
      link.href = dashboardHref(link.getAttribute('data-dashboard-route'));
    });
    monitors.forEach(function (monitor) {
      monitor.href = (dashboardBase ? dashboardBase : '') + '/dashboard/monitor' + monitorQuery;
    });
    performers.forEach(function (performer) {
      performer.href = (apiBase ? apiBase + '/performer' : '/performer') + performerQuery;
    });
    document.querySelectorAll('[data-output-view="subtitles"]').forEach(function (subtitle) {
      subtitle.href = (dashboardBase ? dashboardBase : '') + '/overlay/subtitles' + subtitleQuery;
    });
    if (status) {
      status.textContent = apiBase ? 'Shana API: ' + apiBase : 'Shana API: unavailable';
      status.title = apiBase
        ? 'Output views connect to ' + apiBase
        : 'Output views will use the current browser origin until Shana status loads.';
      status.classList.toggle('good', apiReachable);
      status.classList.toggle('bad', !!latestData && (!shanaRunning || !apiReachable));
    }
  }

  function outputViewApiBase() {
    var configured = window.GAMMA_SHANA_BASE_URL || '';
    var statusUrl = latestData && latestData.shana && latestData.shana.url ? latestData.shana.url : '';
    return browserReachableApiBase(statusUrl || configured || '');
  }

  function dashboardPublicBase() {
    return browserReachableApiBase(window.GAMMA_DASHBOARD_BASE_URL || '');
  }

  function dashboardHref(path) {
    var route = String(path || '/dashboard');
    if (route.charAt(0) !== '/') {
      route = '/' + route;
    }
    var dashboardBase = dashboardPublicBase();
    return dashboardBase ? dashboardBase + route : route;
  }

  function browserReachableApiBase(rawBase) {
    var value = String(rawBase || '').replace(/\/$/, '');
    if (!value) {
      return '';
    }
    try {
      var apiUrl = new URL(value, window.location.origin);
      var browserHost = window.location.hostname;
      var apiHost = apiUrl.hostname;
      var apiIsLocal = apiHost === '127.0.0.1' || apiHost === 'localhost' || apiHost === '0.0.0.0' || apiHost === '::1';
      var browserIsLocal = browserHost === '127.0.0.1' || browserHost === 'localhost' || browserHost === '::1';
      if (apiIsLocal && browserHost && !browserIsLocal) {
        apiUrl.hostname = browserHost;
      }
      return apiUrl.toString().replace(/\/$/, '');
    } catch (error) {
      return value;
    }
  }

  function outputViewQuery(apiBase, values) {
    var query = new URLSearchParams();
    if (apiBase) {
      query.set('api_base', apiBase);
    }
    Object.keys(values || {}).forEach(function (key) {
      if (values[key]) {
        query.set(key, values[key]);
      }
    });
    var text = query.toString();
    return text ? '?' + text : '';
  }

  function updateNavbarDetails(data) {
    var process = data && data.shana && data.shana.process ? data.shana.process : {};
    var health = data && data.shana && data.shana.api_health ? data.shana.api_health : {};
    var twitchWorker = data && data.twitch && data.twitch.worker ? data.twitch.worker : {};
    var eventSub = data && data.twitch && data.twitch.eventsub ? data.twitch.eventsub : {};
    var twitchRunning = !!(twitchWorker.process && twitchWorker.process.running);
    var eventSubRunning = !!(eventSub.process && eventSub.process.running);
    var workerCount = (twitchRunning ? 1 : 0) + (eventSubRunning ? 1 : 0);
    updateStatusChip('stickyTwitchStatus', 'Workers ' + workerCount, workerCount ? 'good' : 'warn');
    setTextIfChanged('navbarDashboardStatus', data && data.dashboard && data.dashboard.url ? data.dashboard.url : 'running');
    setTextIfChanged('navbarApiStatus', health.ok ? 'healthy' : (health.detail || 'unavailable'));
    setTextIfChanged('navbarWorkerStatus', workerCount + ' running / Twitch ' + (twitchRunning ? 'on' : 'off') + ' / EventSub ' + (eventSubRunning ? 'on' : 'off'));
    setTextIfChanged('overviewWorkerStatus', workerCount + ' active');
  }

  function updateOverviewCards(data) {
    var process = data && data.shana && data.shana.process ? data.shana.process : {};
    var health = data && data.shana && data.shana.api_health ? data.shana.api_health : {};
    var providers = data && data.providers ? data.providers : {};
    var memoryStats = data && data.memory_db && data.memory_db.stats ? data.memory_db.stats : {};
    var performer = data && data.performer ? data.performer : {};
    var twitchWorker = data && data.twitch && data.twitch.worker ? data.twitch.worker : {};
    var eventSub = data && data.twitch && data.twitch.eventsub ? data.twitch.eventsub : {};
    var streamReady = data && data.twitch && data.twitch.stream_ready ? data.twitch.stream_ready : {};
    var recentByTarget = performer.recent_by_target || {};
    var currentOutput = recentByTarget.dashboard_monitor || recentByTarget.stream_public || performer.recent_event || {};
    var twitchRunning = !!(twitchWorker.process && twitchWorker.process.running);
    var eventSubRunning = !!(eventSub.process && eventSub.process.running);
    var warnings = [];
    if (!process.running) warnings.push('Shana stopped');
    if (!health.ok) warnings.push('API unavailable');
    if (streamReady.blocker_count) warnings.push(streamReady.blocker_count + ' stream blockers');
    setTextIfChanged('overviewLiveStatus', 'Voice test');
    setTextIfChanged('overviewOutputStatus', currentOutput.type || 'idle');
    setTextIfChanged('overviewShanaStatus', process.running ? 'Running' : 'Stopped');
    setTextIfChanged('overviewStreamStatus', streamReady.mode || (twitchRunning ? 'Twitch running' : 'Ready check'));
    setTextIfChanged('overviewMemoryStatus', (memoryStats.total_items || memoryStats.item_count || 0) + ' items');
    setTextIfChanged('overviewProviderStatus', providerSummary(providers));
    setTextIfChanged('overviewShanaMini', process.running ? 'ON' : 'OFF');
    setTextIfChanged('overviewApiMini', health.ok ? 'OK' : (health.detail || 'Down'));
    setTextIfChanged('overviewWorkerMini', ((twitchRunning ? 1 : 0) + (eventSubRunning ? 1 : 0)) + ' active');
    setTextIfChanged('overviewTurnMini', currentOutput.type ? currentOutput.type + ' #' + (currentOutput.sequence || '?') : 'Idle');
    setTextIfChanged('overviewLiveMini', liveSocket ? 'Connected' : 'Idle');
    setTextIfChanged('overviewStreamMini', streamReady.ok ? 'Ready' : (streamReady.mode || 'Check status'));
    setTextIfChanged('overviewTwitchMini', 'IRC ' + (twitchRunning ? 'on' : 'off') + ' / EventSub ' + (eventSubRunning ? 'on' : 'off'));
    setTextIfChanged('overviewMemoryMini', (memoryStats.known_people || memoryStats.people_count || 0) + ' people');
    setTextIfChanged('overviewWarningsMini', warnings.length ? warnings.join(' / ') : 'No current warnings');
  }

  function providerSummary(providers) {
    var names = ['llm', 'stt', 'tts'];
    var ready = 0;
    names.forEach(function (name) {
      var provider = providers && providers[name] ? providers[name] : {};
      if (provider.ok || provider.available || provider.provider || provider.enabled) ready += 1;
    });
    return ready + '/' + names.length + ' configured';
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

  function applyDashboardTabVisibility() {
    var panels = document.querySelectorAll('[data-dashboard-tab]');
    var activeTabs = currentDashboardTabs();
    if (!dashboardPageTabs[dashboardPage] && document.querySelector('[data-tab-target]') && !document.querySelector('[data-tab-target="' + dashboardActiveTab + '"]')) {
      dashboardActiveTab = 'overview';
      activeTabs = currentDashboardTabs();
    }
    document.body.setAttribute('data-dashboard-page', dashboardPage);
    document.body.setAttribute('data-active-tab', activeTabs.join(' '));
    if (activeTabs.indexOf('stream') !== -1) {
      setSectionOpen('browserVoicePanel', true);
    }
    panels.forEach(function (panel) {
      var tabs = String(panel.getAttribute('data-dashboard-tab') || '').split(/\s+/);
      var visible = tabs.some(function (tab) { return activeTabs.indexOf(tab) !== -1; });
      panel.classList.toggle('tab-hidden', !visible);
    });
    document.querySelectorAll('[data-tab-target]').forEach(function (button) {
      var isActive = activeTabs.indexOf(button.getAttribute('data-tab-target')) !== -1;
      button.classList.toggle('active', isActive);
      button.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    document.querySelectorAll('[data-dashboard-page-link]').forEach(function (link) {
      var isActive = link.getAttribute('data-dashboard-page-link') === dashboardPage;
      link.classList.toggle('active', isActive);
      link.setAttribute('aria-current', isActive ? 'page' : 'false');
    });
  }

  function updateStickyTabOffset() {
    var topbar = document.querySelector('.topbar');
    if (!topbar) return;
    var rect = topbar.getBoundingClientRect();
    document.documentElement.style.setProperty('--topbar-top', Math.ceil(rect.height) + 'px');
  }

  function toggleNavMenu() {
    var isOpen = !document.body.classList.contains('nav-menu-open');
    document.body.classList.toggle('nav-menu-open', isOpen);
    var toggle = document.querySelector('.nav-menu-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  }

  function updateStamp(text) {
    var stamp = document.getElementById('stamp');
    if (stamp) stamp.textContent = text;
  }

  function updateLiveStatus(text) {
    var status = document.getElementById('liveVoiceStatus');
    if (status) status.textContent = text;
  }

  function updateLiveControlLabels() {
    var speech = document.getElementById('liveSpeechThresholdValue');
    var interruptSpeech = document.getElementById('liveInterruptSpeechMsValue');
    var silence = document.getElementById('liveSilenceMsValue');
    if (speech) speech.textContent = currentSpeechThreshold().toFixed(3);
    if (interruptSpeech) interruptSpeech.textContent = String(currentInterruptSpeechMs());
    if (silence) silence.textContent = String(currentSilenceMs());
  }

  function currentSpeechThreshold() {
    var input = document.getElementById('liveSpeechThreshold');
    if (!input) return 0.018;
    return Number(input.value || 18) / 1000;
  }

  function currentSilenceMs() {
    var input = document.getElementById('liveSilenceMs');
    if (!input) return 900;
    return Number(input.value || 900);
  }

  function currentInterruptSpeechMs() {
    var input = document.getElementById('liveInterruptSpeechMs');
    if (!input) return 260;
    return Number(input.value || 260);
  }

  function bargeInEnabled() {
    var input = document.getElementById('liveBargeInEnabled');
    return !!(input && input.checked);
  }

  function persistLiveControlDefaults() {
    var responseMode = document.getElementById('liveResponseMode');
    var bargeInMode = document.getElementById('liveBargeInMode');
    var speech = document.getElementById('liveSpeechThreshold');
    var interruptSpeech = document.getElementById('liveInterruptSpeechMs');
    var silence = document.getElementById('liveSilenceMs');
    var bargeIn = document.getElementById('liveBargeInEnabled');
    if (responseMode) localStorage.setItem('gammaLiveResponseMode', responseMode.value);
    if (bargeInMode) localStorage.setItem('gammaLiveBargeInMode', bargeInMode.value);
    if (speech) localStorage.setItem('gammaLiveSpeechThreshold', speech.value);
    if (interruptSpeech) localStorage.setItem('gammaLiveInterruptSpeechMs', interruptSpeech.value);
    if (silence) localStorage.setItem('gammaLiveSilenceMs', silence.value);
    if (bargeIn) localStorage.setItem('gammaLiveBargeIn', bargeIn.checked ? 'true' : 'false');
    updateLiveControlLabels();
  }

  function loadLiveControlDefaults() {
    var savedResponseMode = localStorage.getItem('gammaLiveResponseMode');
    var savedBargeInMode = localStorage.getItem('gammaLiveBargeInMode');
    var savedSpeech = localStorage.getItem('gammaLiveSpeechThreshold');
    var savedInterruptSpeech = localStorage.getItem('gammaLiveInterruptSpeechMs');
    var savedSilence = localStorage.getItem('gammaLiveSilenceMs');
    var savedBargeIn = localStorage.getItem('gammaLiveBargeIn');
    var responseMode = document.getElementById('liveResponseMode');
    var bargeInMode = document.getElementById('liveBargeInMode');
    var speech = document.getElementById('liveSpeechThreshold');
    var interruptSpeech = document.getElementById('liveInterruptSpeechMs');
    var silence = document.getElementById('liveSilenceMs');
    var bargeIn = document.getElementById('liveBargeInEnabled');
    if (savedResponseMode && responseMode) responseMode.value = savedResponseMode;
    if (savedBargeInMode && bargeInMode) bargeInMode.value = savedBargeInMode;
    if (savedSpeech && speech) speech.value = savedSpeech;
    if (savedInterruptSpeech && interruptSpeech) interruptSpeech.value = savedInterruptSpeech;
    if (savedSilence && silence) silence.value = savedSilence;
    if (savedBargeIn !== null && bargeIn) bargeIn.checked = savedBargeIn === 'true';
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
    var key = cacheKey || elementId;
    if (sectionHashes[key] === value) {
      return;
    }
    sectionHashes[key] = value;
    el.textContent = value;
  }

  function renderBlockIfChanged(elementId, rawValue, humanText, cacheKey) {
    if (!document.getElementById(elementId)) return;
    var key = cacheKey || elementId;
    var nextKey = viewMode === 'json' ? pretty(rawValue) : humanText;
    if (sectionHashes[key] === nextKey) {
      return;
    }
    sectionHashes[key] = nextKey;
    renderBlock(elementId, rawValue, humanText);
  }

  function renderHtmlBlockIfChanged(elementId, rawValue, humanHtml, cacheKey) {
    var el = document.getElementById(elementId);
    if (!el) return;
    var key = cacheKey || elementId;
    var nextKey = viewMode === 'json' ? pretty(rawValue) : humanHtml;
    if (sectionHashes[key] === nextKey) {
      return;
    }
    sectionHashes[key] = nextKey;
    if (viewMode === 'json') {
      el.classList.add('json-render');
      el.textContent = pretty(rawValue);
    } else {
      el.classList.remove('json-render');
      el.innerHTML = humanHtml;
    }
  }

  function pretty(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
    }
  }

  function fmtArtifactTimestamp(filename) {
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

  function toggleSubtitleWindow() {
    if (subtitlePopup && !subtitlePopup.closed) {
      subtitlePopup.close();
      subtitlePopup = null;
      return;
    }
    subtitlePopup = window.open('', 'gammaSubtitleWindow', 'width=900,height=220');
    if (!subtitlePopup) {
      updateLiveStatus('Subtitle window blocked by the browser.');
      return;
    }
    subtitlePopup.document.open();
    subtitlePopup.document.write(
      '<!doctype html><html><head><title>Gamma Subtitles</title><style>' +
      'body{margin:0;background:#0f1318;color:#f6f7fb;font:700 32px/1.35 Georgia,serif;padding:20px;}' +
      '#subtitleText{white-space:pre-wrap;}' +
      '</style></head><body><div id="subtitleText">Subtitles idle.</div></body></html>'
    );
    subtitlePopup.document.close();
    var subtitleStatus = document.getElementById('liveSubtitleStatus');
    updateSubtitlePopup((subtitleStatus && subtitleStatus.textContent) || 'Subtitles idle.');
  }

  function enableProviderControls() {
    var llmButton = document.getElementById('llmTestButton');
    var sttButton = document.getElementById('sttTestButton');
    var voiceButton = document.getElementById('voiceTestButton');
    var ttsButton = document.getElementById('ttsTestButton');
    var ttsProvider = document.getElementById('ttsProviderSelect');
    var ttsProfile = document.getElementById('ttsProfileSelect');
    if (ttsProvider) ttsProvider.disabled = false;
    if (ttsProfile) ttsProfile.disabled = false;
    if (llmButton) llmButton.disabled = false;
    if (sttButton) sttButton.disabled = false;
    if (voiceButton) voiceButton.disabled = false;
    if (ttsButton) ttsButton.disabled = false;
  }

  function stableKey(value) {
    try {
      return JSON.stringify(value);
    } catch (error) {
      return String(value);
    }
  }

  // Initialize tab visibility after all modules are loaded
  if (typeof applyDashboardTabVisibility === 'function') {
    applyDashboardTabVisibility();
  }

})();