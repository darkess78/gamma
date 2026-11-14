/* Private rehearsal client. A displayed reply is not proof of human hearing. */
(function (root) {
  'use strict';
  class RehearsalController {
    constructor(options) {
      this.request = options.request;
      this.present = options.present;
      this.audioPlayer = options.audioPlayer || null;
      this.update = options.update || function () {};
      this.uuid = options.uuid || (() => root.crypto.randomUUID());
      this.now = options.now || (() => Date.now() / 1000);
      this.schedule = options.schedule || ((fn) => root.setTimeout(fn, 1000));
      this.unschedule = options.unschedule || ((id) => root.clearTimeout(id));
      this.consumer = this.uuid();
      this.confirmed = false;
      this.connected = false;
      this.epoch = 0;
      this.session = null;
      this.busy = false;
      this.timer = null;
      this.lastHeartbeat = 0;
      this.seen = new Set();
      this.lifecycle = 0;
    }
    state(message) {
      this.update({ confirmed: this.confirmed, connected: this.connected,
        busy: this.busy, session: this.session, message: message || '' });
    }
    call(path, values, method) {
      return this.request(path, values, method || 'POST');
    }
    valid(epoch, sessionId, connected) {
      return this.confirmed && this.epoch === epoch && this.session &&
        this.session.id === sessionId && (!connected || this.connected);
    }
    pause() {
      if (this.timer !== null) this.unschedule(this.timer);
      this.timer = null;
    }
    audioContext() {
      const sessionId=this.session.id, revision=this.session.revision, generation=this.session.generation;
      return {consumerId:this.consumer,sessionId,revision,generation,
        valid:()=>this.confirmed&&this.connected&&this.session&&this.session.id===sessionId
          &&this.session.revision===revision&&this.session.generation===generation};
    }
    async listen(enabled) {
      if(!this.audioPlayer)return;
      if(!enabled){await this.audioPlayer.disable();return;}
      if(!this.confirmed||!this.connected||!this.session)return;
      await this.audioPlayer.enable(this.audioContext());
    }
    async clean(sessionId, stop) {
      if (!sessionId) return;
      let failure = false;
      try { await this.call('/disconnect', { session_id: sessionId, consumer_id: this.consumer }); }
      catch (_) { failure = true; }
      if (stop) {
        try { await this.call('/control', { request_id: this.uuid(), session_id: sessionId, action: 'stop' }); }
        catch (_) { failure = true; }
      }
      if (failure) this.state('The connection could not confirm stopping. Check the rehearsal before starting again.');
    }
    async confirm(value) {
      this.confirmed = value === true;
      this.epoch += 1;
      this.pause();
      const sessionId = this.session && this.session.id;
      this.connected = false;
      if(this.audioPlayer)void this.audioPlayer.disable();
      this.busy = this.lifecycle > 0;
      this.state(value ? 'Choose an idea or connect to an existing rehearsal.' : 'Private screen off. Rehearsal stopped and disconnected.');
      if (!value && sessionId) {
        this.lifecycle += 1; this.busy = true; this.state();
        try { await this.clean(sessionId, true); }
        finally { this.lifecycle -= 1; this.busy = this.lifecycle > 0; this.state(); }
      }
    }
    async start(plan) {
      if (!this.confirmed || this.busy || this.connected) return;
      const epoch = this.epoch;
      this.lifecycle += 1;
      this.busy = true; this.state('Starting the rehearsal…');
      try {
        const session = await this.call('/start', Object.assign({}, plan, { request_id: this.uuid() }));
        if (!this.confirmed || this.epoch !== epoch) { await this.clean(session.id, true); return; }
        this.session = session;
        this.seen.clear();
        await this.connect(epoch);
      } catch (_) {
        if (this.confirmed && this.epoch === epoch) this.state('Start was not confirmed. Connect to existing to check; do not resend the start.');
      } finally {
        this.lifecycle -= 1; this.busy = this.lifecycle > 0; this.state();
      }
    }
    async connectExisting() {
      if (!this.confirmed || this.busy || this.connected) return;
      const epoch = this.epoch;
      this.lifecycle += 1;
      this.busy = true;
      try {
        const value = await this.call('/status', {}, 'GET');
        if (!this.confirmed || this.epoch !== epoch) return;
        if (!value.active) { this.state('There is no active rehearsal. Choose an idea and start one.'); return; }
        this.session = value.active;
        this.seen.clear();
        await this.connect(epoch);
      } catch (_) {
        if (this.epoch === epoch) this.state('Could not connect. Check the connection, then try Connect to existing.');
      } finally {
        this.lifecycle -= 1; this.busy = this.lifecycle > 0; this.state();
      }
    }
    async connect(epoch) {
      const sessionId = this.session.id;
      const value = await this.call('/connect', { session_id: sessionId, consumer_id: this.consumer });
      if (!this.valid(epoch, sessionId, false)) { await this.clean(sessionId, true); return; }
      this.session.revision = value.revision;
      this.connected = true;
      this.lastHeartbeat = this.now();
      this.state('Connected privately. Replies will appear below.');
      this.loop(epoch, sessionId);
    }
    async loop(epoch, sessionId) {
      if (!this.valid(epoch, sessionId, true)) return;
      try {
        if (this.now() >= this.session.expires_at) {
          this.epoch += 1; this.connected = false; this.pause();
          this.state('The rehearsal reached its time limit. Start a new one when ready.');
          if(this.audioPlayer)void this.audioPlayer.disable();
          await this.clean(sessionId, false); return;
        }
        if (this.now() - this.lastHeartbeat >= 15) {
          await this.call('/heartbeat', { session_id: sessionId, consumer_id: this.consumer });
          if (!this.valid(epoch, sessionId, true)) return;
          this.lastHeartbeat = this.now();
        }
        const value = await this.call('/next', { session_id: sessionId, consumer_id: this.consumer }, 'GET');
        if (!this.valid(epoch, sessionId, true)) return;
        const output = value.output;
        if (output && !this.seen.has(output.id)) {
          if (output.session_id !== sessionId || output.consumer_id !== this.consumer ||
              output.generation !== this.session.generation || output.revision !== this.session.revision ||
              output.audience !== 'owner_private' || output.publication_authorized !== false ||
              output.expires_at <= this.now()) throw new Error('stale output');
          this.seen.add(output.id);
          if (!this.present(output) || !this.valid(epoch, sessionId, true)) {
            this.state('The reply was not displayed. It will not be replayed automatically.');
          } else {
            await this.call('/ack', { session_id: sessionId, consumer_id: this.consumer,
              output_id: output.id, generation: output.generation, revision: output.revision,
              status: 'delivered', receipt_id: this.uuid() });
            if (this.valid(epoch, sessionId, true)) {
              this.state('Reply displayed here.');
              if(this.audioPlayer)void this.audioPlayer.render(output.id);
            }
          }
        }
      } catch (_) {
        if (this.valid(epoch, sessionId, true)) {
          this.epoch += 1; this.connected = false; this.pause();
          if(this.audioPlayer)void this.audioPlayer.disable();
          this.state('Connection interrupted. Connect to existing to continue. Uncertain replies are not replayed.');
          await this.clean(sessionId, false);
        }
        return;
      }
      if (this.valid(epoch, sessionId, true)) {
        this.timer = this.schedule(() => { this.timer = null; this.loop(epoch, sessionId); });
      }
    }
    async send(kind, text, alias) {
      if (!this.confirmed || !this.connected || this.busy) return;
      const epoch = this.epoch, sessionId = this.session.id;
      this.busy = true; this.state('Considering the example…');
      try {
        const value = await this.call('/event', { session_id: sessionId, consumer_id: this.consumer,
          event_id: this.uuid(), kind: kind, text: text, actor_alias: alias,
          source: 'replay', observed_at: this.now() });
        if (!this.valid(epoch, sessionId, true)) return;
        const messages = { focused_quiet: 'Staying quiet while you focus.', minimum_gap: 'Leaving a little space before another contribution.',
          speech_budget: 'Taking a breather before more conversation.', decision_budget_exhausted: 'This rehearsal has reached its activity limit.',
          foreground_has_priority: 'Your current conversation comes first.', asleep: 'Shana is asleep. The example will not be replayed on wake.',
          output_pending: 'A contribution is already waiting to be displayed.' };
        this.state(value.status === 'ready' ? 'A contribution is ready.' : messages[value.reason] ||
          (value.status === 'quiet' ? 'Shana chose to leave this moment quiet.' : 'No new contribution was confirmed.'));
      } catch (_) {
        if (this.valid(epoch, sessionId, true)) this.state('The example outcome is uncertain. It has not been resent.');
      } finally {
        if (this.epoch === epoch) { this.busy = false; this.state(); }
      }
    }
    async control(action) {
      if (!this.confirmed || !this.connected || (this.busy && action !== 'stop')) return;
      const sessionId = this.session.id;
      this.epoch += 1; const epoch = this.epoch; this.pause();
      if(this.audioPlayer)this.audioPlayer.suspend();
      this.busy = true;
      try {
        const value = await this.call('/control', { request_id: this.uuid(), session_id: sessionId, action: action });
        if (!this.valid(epoch, sessionId, true)) return;
        this.session = value.session;
        if (action === 'stop') {
          if(this.audioPlayer)void this.audioPlayer.disable();
          this.connected = false; await this.clean(sessionId, false); this.state('Rehearsal stopped.');
        } else {
          if(this.audioPlayer&&this.audioPlayer.enabled)await this.audioPlayer.changeContext(this.audioContext());
          this.state(action === 'owner_away' ? 'Continuing within your idea while you step away.' :
            action === 'owner_return' ? 'Welcome back. Received contributions are in the feed.' :
            action === 'focus' ? 'Quiet while you focus.' : 'Back to relaxed conversation.');
          this.loop(epoch, sessionId);
        }
      } catch (_) {
        if (this.epoch === epoch) {
          this.connected = false; this.state('That change was not confirmed. Connect to existing to check.');
          if(this.audioPlayer)void this.audioPlayer.disable();
          await this.clean(sessionId, false);
        }
      } finally {
        if (this.epoch === epoch) { this.busy = false; this.state(); }
      }
    }
  }
  root.ShanaRehearsalController = RehearsalController;
  if (typeof module !== 'undefined' && module.exports) module.exports = { RehearsalController };
  if (!root.document) return;
  const document = root.document;
  function mount() {
    const byId = (id) => document.getElementById(id);
    if (!byId('rehearsalPrivate')) return;
    const feed = byId('rehearsalFeed');
    const player=root.ShanaChannelAudioPlayer?new root.ShanaChannelAudioPlayer({
      notice:text=>{byId('rehearsalSpeechStatus').textContent=text;},
      update:state=>{byId('rehearsalListen').checked=state.enabled;
        byId('rehearsalStopSpeech').disabled=!state.enabled&&!state.changing;}
    }):null;
    const controller = new RehearsalController({
      audioPlayer:player,
      request: async (path, values, method) => {
        const url = new URL('/api/coordination/stream' + path, root.location.origin);
        const options = { method: method, credentials: 'same-origin', cache: 'no-store', headers: {} };
        const payload = Object.assign({ audience: 'owner_private' }, values);
        if (method === 'GET') Object.keys(payload).forEach((key) => url.searchParams.set(key, payload[key]));
        else { options.headers['content-type'] = 'application/json'; options.body = JSON.stringify(payload); }
        const abort = new root.AbortController(); options.signal = abort.signal;
        options.keepalive = method === 'POST' && (path === '/disconnect' || path === '/control');
        const timeout = root.setTimeout(() => abort.abort(), path === '/event' ? 180000 : 20000);
        try {
          const response = await root.fetch(url, options);
          if (!response.ok) throw new Error('Private rehearsal request failed');
          return await response.json();
        } finally { root.clearTimeout(timeout); }
      },
      present: (output) => {
        if (!byId('rehearsalPrivate').checked || !feed.isConnected) return false;
        const item = document.createElement('li'), text = document.createElement('p'), label = document.createElement('small');
        text.textContent = output.text;
        label.textContent = 'Displayed in this private rehearsal'; label.className = 'shana-muted';
        item.append(text, label); feed.append(item);
        while (feed.children.length > 60) feed.firstElementChild.remove();
        byId('rehearsalEmpty').hidden = true;
        return item.isConnected && text.textContent === output.text;
      },
      update: (state) => {
        byId('rehearsalConnection').textContent = state.connected ? 'Connected privately' : 'Disconnected';
        if (state.message) byId('rehearsalNotice').textContent = state.message;
        if (!state.confirmed) {
          feed.replaceChildren(); byId('rehearsalEmpty').hidden = false;
          byId('rehearsalSummary').textContent = '';
        }
        ['rehearsalStart', 'rehearsalConnect'].forEach((id) => { byId(id).disabled = !state.confirmed || state.busy || state.connected; });
        ['rehearsalSend','rehearsalAway','rehearsalReturn','rehearsalFocus','rehearsalRelax'].forEach((id) => {
          byId(id).disabled = !state.confirmed || !state.connected || state.busy;
        });
        byId('rehearsalStop').disabled = !state.confirmed || !state.connected;
        byId('rehearsalListen').disabled=!player||!state.confirmed||!state.connected;
        if (state.session && state.confirmed) {
          const minutes = Math.max(0, Math.ceil((state.session.expires_at - Date.now() / 1000) / 60));
          byId('rehearsalSummary').textContent = (state.session.plan.format === 'gameplay' ? 'Gameplay and chat' : 'Building or coding and chat') +
            ' · ' + minutes + ' minutes remaining · ' + (state.session.owner_present ? 'You are here' : 'You stepped away') +
            (state.session.focused ? ' · Quiet focus' : '');
        }
      }
    });
    byId('rehearsalPrivate').addEventListener('change', (event) => { controller.confirm(event.target.checked); });
    byId('rehearsalPlanForm').addEventListener('submit', (event) => {
      event.preventDefault();
      const duration = Number(byId('rehearsalDuration').value);
      controller.start({ format: byId('rehearsalFormat').value, broad_plan: byId('rehearsalPlan').value.trim(),
        duration_seconds: duration, maximum_decisions: Math.min(480, Math.ceil(duration / 30)) });
    });
    byId('rehearsalConnect').addEventListener('click', () => { controller.connectExisting(); });
    byId('rehearsalListen').addEventListener('change',event=>{void controller.listen(event.target.checked);});
    byId('rehearsalStopSpeech').addEventListener('click',()=>{byId('rehearsalListen').checked=false;void controller.listen(false);});
    byId('rehearsalEventForm').addEventListener('submit', (event) => {
      event.preventDefault(); controller.send(byId('rehearsalKind').value, byId('rehearsalText').value, byId('rehearsalAlias').value);
    });
    [['rehearsalAway','owner_away'],['rehearsalReturn','owner_return'],['rehearsalFocus','focus'],
      ['rehearsalRelax','relax'],['rehearsalStop','stop']].forEach(([id,action]) => {
      byId(id).addEventListener('click', () => { controller.control(action); });
    });
    root.addEventListener('pagehide', () => { controller.confirm(false); });
    controller.state();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
}(typeof window !== 'undefined' ? window : globalThis));
