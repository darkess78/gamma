/* Authenticated responsive dashboard client. No background agent or content cache. */
(function () {
  'use strict';
  const base = '/api/coordination/phone';
  const el = name => document.getElementById(name);
  const uuid = () => crypto.randomUUID();
  const key = 'shana-phone-device-v1', pendingKey = 'shana-phone-pending-v1';
  let device = null, connected = false, epoch = 0, privacyEpoch = 0, connecting = false;
  let refreshing = false, sending = false, notices = false, lastState = null;
  let notificationRegistration = null;
  let historyRevision = 0, refreshAgain = null;
  const historyRequests = new Set();
  const requests = new Set(), alerts = new Set();
  try { device = JSON.parse(sessionStorage.getItem(key) || 'null'); } catch (_) { device = null; }
  const privateReady = () => el('phonePrivate').checked;
  const valid = version => privateReady() && connected && version === privacyEpoch;
  function say(message) { el('phoneConnection').textContent = message; }
  function updateControls() {
    el('phoneConnect').disabled = !privateReady() || connecting || connected;
    el('phoneDisconnect').disabled = !connected;
    el('phoneContent').hidden = !connected || !privateReady();
    el('phoneSend').disabled = !connected || sending;
    el('phoneState').textContent = connected ? (lastState?.awake === false ? 'Shana is asleep' : 'Connected') : 'Disconnected';
    el('phoneCheck').hidden = !sessionStorage.getItem(pendingKey);
    el('phoneReset').hidden = !device || connected;
    el('phoneReset').disabled = !privateReady();
    el('phoneDeviceManager').hidden = !privateReady();
  }
  async function request(action, values = {}, options = {}) {
    const controller = new AbortController();
    requests.add(controller);
    const timer = setTimeout(() => controller.abort(), options.timeout || 15000);
    try {
      const method = options.method || 'POST';
      const payload = {audience: 'owner_private', request_id: uuid(),
        ...(options.ownerOnly ? {} : {device_id: device?.device_id, device_secret: device?.secret,
          ...(options.unconnected ? {} : {epoch})}), ...values};
      const response = await fetch(`${base}/${action}`, {method, credentials: 'same-origin', cache: 'no-store',
        signal: controller.signal, ...(method === 'GET' ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)})});
      const value = await response.json();
      if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : `HTTP ${response.status}`);
      return value;
    } finally { clearTimeout(timer); requests.delete(controller); }
  }
  function newDevice() {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    const secret = btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    return {device_id: `phone-${uuid()}`, secret, registration_id: uuid()};
  }
  function clearPrivateView() {
    historyRevision += 1; historyRequests.clear();
    el('phoneHistory').replaceChildren(); el('phoneUpdates').replaceChildren();
    el('phoneDeviceList').replaceChildren(); el('phoneDeviceManager').open = false;
    el('phoneText').value = ''; lastState = null;
    for (const notice of alerts) notice.close();
    alerts.clear();
    notices = false;
    if (notificationRegistration) void closeRegisteredAlerts(notificationRegistration);
  }
  async function disconnect(remove = false) {
    const wasConnected = connected;
    privacyEpoch += 1; connected = false;
    for (const controller of requests) controller.abort();
    clearPrivateView(); updateControls();
    if (device && (remove || wasConnected)) {
      try {
        if (remove) await request('revoke-device', {device_id: device.device_id}, {ownerOnly: true});
        else await request('control', {action: 'disconnect'});
      } catch (_) { /* An unconfirmed revoke can be checked in Manage browser connections. */ }
    }
    if (remove) { device = null; sessionStorage.removeItem(key); sessionStorage.removeItem(pendingKey); }
    say(remove ? 'Browser connection reset. Use Manage browser connections to check removal, then connect again.' : 'Disconnected. Your conversation and work remain on Gamma.');
  }
  async function connect() {
    if (!privateReady() || connecting || connected) return;
    const version = ++privacyEpoch;
    connecting = true; updateControls(); say('Connecting privately…');
    try {
      if (!device?.device_id || !device?.secret || !device?.registration_id) {
        device = newDevice(); sessionStorage.setItem(key, JSON.stringify(device));
      }
      await request('register', {request_id: device.registration_id, label: 'My browser'}, {unconnected: true});
      if (!privateReady() || version !== privacyEpoch) return;
      const value = await request('connect', {}, {unconnected: true});
      if (!privateReady() || version !== privacyEpoch) {
        epoch = value.device.epoch;
        await request('control', {action: 'disconnect'}).catch(() => {});
        return;
      }
      epoch = value.device.epoch; connected = true;
      say('Connected to the same Shana. Text and reminders remain private to this session.');
      await refresh(version);
    } catch (error) {
      if (version === privacyEpoch && privateReady()) say(`Could not connect. ${error.message}`);
    } finally { connecting = false; updateControls(); }
  }
  function appendHistoryTurn(fragment, turn) {
    for (const [who, text] of [['You', turn.user], ['Shana', turn.assistant]]) {
      if (!text) continue;
      const item = document.createElement('div'); item.className = `message ${who === 'You' ? 'owner' : ''}`;
      const name = document.createElement('span'); name.className = 'speaker'; name.textContent = who;
      const body = document.createElement('p'); body.textContent = text; item.append(name, body); fragment.append(item);
    }
  }
  function renderHistory(value) {
    const fragment = document.createDocumentFragment();
    historyRequests.clear();
    for (const turn of value.turns || []) {
      appendHistoryTurn(fragment, turn);
      if (typeof turn.id === 'string') historyRequests.add(turn.id);
    }
    historyRevision += 1;
    el('phoneHistory').replaceChildren(fragment);
  }
  function renderCheckedReply(value, requestId, text, version) {
    if (!valid(version) || value.status !== 'completed' || value.request_id !== requestId
        || !value.response || typeof value.response.display_text !== 'string'
        || historyRequests.has(requestId)) return;
    const response = value.response;
    const communicates = ['reply', 'acknowledge'].includes(response.response_action)
      && !['silent', 'deferred'].includes(response.delivery_mode);
    const fragment = document.createDocumentFragment();
    appendHistoryTurn(fragment, {user: text, assistant: communicates ? response.display_text : ''});
    el('phoneHistory').append(fragment);
    historyRequests.add(requestId); historyRevision += 1;
  }
  function renderUpdates(value) {
    const fragment = document.createDocumentFragment();
    const rows = (value.messages || []).filter(row => ['received', 'acknowledged', 'uncertain'].includes(row.status));
    if (!rows.length) { const text = document.createElement('p'); text.className = 'subtle'; text.textContent = 'No updates yet.'; fragment.append(text); }
    for (const row of rows) {
      const card = document.createElement('div'); card.className = 'update';
      const text = document.createElement('p'); text.textContent = row.text || 'Delivery was not confirmed. Shana will not send this reminder again automatically.';
      card.append(text);
      if (row.status === 'received') {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Got it';
        button.addEventListener('click', async () => {
          const version = privacyEpoch; button.disabled = true;
          try { await request('receipt', {message_id: row.id, status: 'acknowledged'}); if (valid(version)) await refresh(version); }
          catch (_) { if (valid(version)) { text.textContent = 'Acknowledgment is unconfirmed. Check again; this reminder will not be resent.'; button.disabled = false; } }
        });
        card.append(button);
      } else { const state = document.createElement('p'); state.className = 'subtle'; state.textContent = row.status === 'acknowledged' ? 'Acknowledged. Commitment still open.' : 'No automatic resend.'; card.append(state); }
      fragment.append(card);
    }
    el('phoneUpdates').replaceChildren(fragment);
  }
  function boundedNotificationOperation(promise) {
    let timer;
    return Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Alert operation timed out.')), 10000);
    })]).finally(() => clearTimeout(timer));
  }
  async function closeRegisteredAlerts(registration, tag = null) {
    try {
      const active = await boundedNotificationOperation(tag ? registration.getNotifications({tag}) : registration.getNotifications());
      for (const notice of active) if (notice.tag?.startsWith('shana-private-')) notice.close();
    } catch (_) { /* Generic alert closure is unconfirmed; no content or resend is exposed. */ }
  }
  async function activeNotificationWorker(registration) {
    const worker = registration.active || registration.installing || registration.waiting;
    if (!worker) throw new Error('Alert worker unavailable.');
    if (worker.state === 'activated') return registration;
    let listener;
    try {
      await boundedNotificationOperation(new Promise((resolve, reject) => {
        listener = () => {
          if (worker.state === 'activated') resolve();
          else if (worker.state === 'redundant') reject(new Error('Alert worker did not activate.'));
        };
        worker.addEventListener('statechange', listener); listener();
      }));
      return registration;
    } finally { if (listener) worker.removeEventListener('statechange', listener); }
  }
  async function showDiscreetAlert(version) {
    if (!valid(version) || !notices || document.hidden || typeof Notification === 'undefined'
        || Notification.permission !== 'granted') return;
    const options = {body: 'You have a private update.', tag: `shana-private-${uuid()}`};
    try {
      if (notificationRegistration) {
        const registration = notificationRegistration;
        const operation = registration.showNotification('Shana', options);
        operation.then(() => {
          if (!valid(version) || !notices) void closeRegisteredAlerts(registration, options.tag);
        }).catch(() => {});
        await boundedNotificationOperation(operation);
      } else {
        // Some desktop browsers permit this fallback; mobile TypeError stays local to alerts.
        const notice = new Notification('Shana', options);
        alerts.add(notice); setTimeout(() => { notice.close(); alerts.delete(notice); }, 15000);
      }
    } catch (_) {
      if (version === privacyEpoch) notices = false;
      if (valid(version)) el('phoneNotificationStatus').textContent = 'Alerts are unavailable here. Updates still appear in this page.';
    }
  }
  async function refresh(version = privacyEpoch) {
    if (!valid(version)) return;
    if (refreshing) { refreshAgain = version; return; }
    refreshing = true;
    const historyAtStart = historyRevision;
    try {
      const value = await request('state');
      if (!valid(version)) return;
      lastState = value; updateControls();
      el('phoneReminderState').textContent = value.policy.enabled && Date.now() / 1000 < value.policy.authority_expires_at ? 'Considered reminders on' : 'Reminders off';
      if (!el('phoneSettings').open) {
        el('phoneAvailability').value = value.device.availability;
        el('phoneReminders').checked = value.policy.enabled && Date.now() / 1000 < value.policy.authority_expires_at;
      }
      renderUpdates(value);
      const history = await request('conversation');
      if (!valid(version)) return;
      // A history request begun before a newer checked reply cannot erase it.
      if (historyAtStart === historyRevision) renderHistory(history);
      if (document.hidden) return;
      const offered = await request('poll');
      if (!valid(version) || !offered.message) return;
      // Receipt means this browser obtained the bytes. It does not claim the owner read them.
      await request('receipt', {message_id: offered.message.id, status: 'received'});
      if (!valid(version)) return;
      const received = await request('state');
      if (!valid(version)) return;
      renderUpdates(received);
      await showDiscreetAlert(version);
    } catch (error) {
      if (valid(version)) {
        say(`Connection needs attention. ${error.message}. Reconnect when ready.`);
        connected = false; clearPrivateView(); updateControls();
      }
    } finally {
      refreshing = false;
      const next = refreshAgain; refreshAgain = null;
      if (next !== null && valid(next)) void refresh(next);
    }
  }
  async function checkPending() {
    const inputId = sessionStorage.getItem(pendingKey), version = privacyEpoch;
    if (!inputId || !valid(version)) return;
    try {
      const value = await request('result', {input_request_id: inputId});
      if (!valid(version)) return;
      el('phoneInputStatus').textContent = value.status === 'completed' ? 'Reply ready.' : `Message state: ${value.status}. It has not been resent.`;
      if (['completed', 'redacted', 'expired', 'interrupted'].includes(value.status)) sessionStorage.removeItem(pendingKey);
      await refresh(version); updateControls();
    } catch (_) { if (valid(version)) el('phoneInputStatus').textContent = 'Message status is unconfirmed. No automatic resend.'; }
  }
  async function loadDevices() {
    if (!privateReady()) return;
    const version = privacyEpoch;
    try {
      const value = await request('devices?audience=owner_private', {}, {method: 'GET', ownerOnly: true});
      if (!privateReady() || version !== privacyEpoch) return;
      const fragment = document.createDocumentFragment();
      for (const row of value.devices || []) {
        if (row.revoked) continue;
        const card = document.createElement('div'); card.className = 'update';
        const label = document.createElement('p'); label.textContent = `${row.label}${row.id === device?.device_id ? ' · this browser' : ''}`;
        const seen = document.createElement('p'); seen.className = 'subtle';
        seen.textContent = `${row.connected ? 'Connected' : 'Not connected'} · last seen ${new Date(row.last_seen_at * 1000).toLocaleString()}`;
        const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary'; button.textContent = 'Remove connection';
        button.addEventListener('click', async () => {
          if (!privateReady() || version !== privacyEpoch) return;
          button.disabled = true;
          try {
            await request('revoke-device', {device_id: row.id}, {ownerOnly: true});
            if (!privateReady() || version !== privacyEpoch) return;
            if (row.id === device?.device_id) await disconnect(true);
            else await loadDevices();
            el('phoneDevicesStatus').textContent = 'Connection removed. Conversation history is unchanged.';
          } catch (_) { if (privateReady() && version === privacyEpoch) { el('phoneDevicesStatus').textContent = 'Removal is unconfirmed. Show connections to check.'; button.disabled = false; } }
        });
        card.append(label, seen, button); fragment.append(card);
      }
      el('phoneDeviceList').replaceChildren(fragment);
      el('phoneDevicesStatus').textContent = (value.devices || []).filter(row => !row.revoked).length ? '' : 'No active browser connections.';
    } catch (_) { if (privateReady() && version === privacyEpoch) el('phoneDevicesStatus').textContent = 'Could not load connections. Check your dashboard sign-in.'; }
  }
  el('phonePrivate').addEventListener('change', () => {
    if (!privateReady()) void disconnect(); else say('Ready to connect privately.'); updateControls();
  });
  el('phoneConnect').addEventListener('click', connect);
  el('phoneReset').addEventListener('click', () => { if (privateReady()) void disconnect(true); });
  el('phoneLoadDevices').addEventListener('click', loadDevices);
  el('phoneDisconnect').addEventListener('click', () => disconnect());
  el('phoneForget').addEventListener('click', () => { if (privateReady()) void disconnect(true); });
  el('phoneCheck').addEventListener('click', checkPending);
  el('phoneForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (!connected || !privateReady() || sending || !el('phoneText').value.trim()) return;
    if (sessionStorage.getItem(pendingKey)) { el('phoneInputStatus').textContent = 'Check the previous message before sending another.'; return; }
    const version = privacyEpoch, requestId = uuid(), text = el('phoneText').value;
    sessionStorage.setItem(pendingKey, requestId); sending = true; updateControls();
    el('phoneInputStatus').textContent = 'Sending…';
    try {
      const value = await request('input', {request_id: requestId, text}, {timeout: 180000});
      if (!valid(version)) return;
      el('phoneText').value = '';
      el('phoneInputStatus').textContent = value.status === 'completed' ? 'Reply ready.' : `Message saved: ${value.status}. Check later; it will not be resent.`;
      if (value.status === 'completed') {
        sessionStorage.removeItem(pendingKey);
        renderCheckedReply(value, requestId, text, version);
      }
      void refresh(version);
    } catch (_) { if (valid(version)) el('phoneInputStatus').textContent = 'Delivery is unconfirmed. Use Check last message; no automatic resend.'; }
    finally { sending = false; updateControls(); }
  });
  el('phoneApply').addEventListener('click', async () => {
    const version = privacyEpoch;
    if (!valid(version)) return;
    try {
      await request('control', {action: 'availability', value: el('phoneAvailability').value});
      if (!valid(version)) return;
      const enabled = el('phoneReminders').checked, now = Date.now() / 1000;
      await request('control', {action: 'contact_policy', value: {enabled, authority_expires_at: enabled ? now + 8 * 3600 : 0,
        quiet_until: 0, minimum_interval: 900, maximum_per_day: 3}});
      if (valid(version)) { el('phoneSettingsStatus').textContent = 'Saved.'; el('phoneSettings').open = false; await refresh(version); }
    } catch (error) { if (valid(version)) el('phoneSettingsStatus').textContent = `Could not save. ${error.message}`; }
  });
  el('phoneNotifications').addEventListener('click', async () => {
    const version = privacyEpoch;
    if (!valid(version)) return;
    if (typeof Notification === 'undefined') { el('phoneNotificationStatus').textContent = 'This browser does not support alerts. Updates still appear here.'; return; }
    if (window.isSecureContext === false) { el('phoneNotificationStatus').textContent = 'Alerts need a secure HTTPS review address. Updates still appear here.'; return; }
    el('phoneNotifications').disabled = true;
    try {
      const permission = await Notification.requestPermission();
      if (!valid(version)) return;
      if (permission !== 'granted') { notices = false; el('phoneNotificationStatus').textContent = 'Alerts are off. Updates still appear here.'; return; }
      if (typeof navigator !== 'undefined' && navigator.serviceWorker) {
        const registration = await boundedNotificationOperation(navigator.serviceWorker.register('/dashboard/phone-worker.js',
          {scope: '/dashboard/phone', updateViaCache: 'none'}));
        if (!valid(version)) return;
        notificationRegistration = await activeNotificationWorker(registration);
        if (!valid(version)) return;
      }
      notices = true;
      el('phoneNotificationStatus').textContent = notices ? 'Discreet alerts enabled while this page is open.' : 'Alerts are off. Updates still appear here.';
    } catch (_) { if (version === privacyEpoch) notices = false; if (valid(version)) el('phoneNotificationStatus').textContent = 'Alerts unavailable. Updates still appear here.'; }
    finally { el('phoneNotifications').disabled = false; }
  });
  window.addEventListener('offline', () => { if (connected) void disconnect(); });
  window.addEventListener('pagehide', () => { if (connected) void disconnect(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden && connected) { el('phonePrivate').checked = false; void disconnect(); } });
  setInterval(() => { if (!document.hidden) void refresh(); }, 10000);
  updateControls();
}());
