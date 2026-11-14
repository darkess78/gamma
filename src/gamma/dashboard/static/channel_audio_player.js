/* Explicit private listen consent. Media receipts describe this device only. */
(function (root) {
  'use strict';
  class ChannelAudioPlayer {
    constructor(options = {}) {
      const Queue = options.Queue || root.ShanaPlaybackQueue;
      this.queue = new Queue(8);
      this.channel = options.channel === 'shared_moment' ? 'shared_moment' : 'private_stream';
      this.request = options.request || this.http.bind(this);
      this.fetchAudio = options.fetchAudio || this.readAudio.bind(this);
      this.makeAudio = options.makeAudio || (url => new root.Audio(url));
      this.makeURL = options.makeURL || (blob => root.URL.createObjectURL(blob));
      this.revokeURL = options.revokeURL || (url => root.URL.revokeObjectURL(url));
      this.schedule = options.schedule || (fn => root.setTimeout(fn, 500));
      this.unschedule = options.unschedule || (id => root.clearTimeout(id));
      this.now = options.now || (() => Date.now() / 1000);
      this.notice = options.notice || (() => {});
      this.update = options.update || (() => {});
      this.enabled = false; this.connected = false; this.changing = false;
      this.context = null; this.epoch = 0; this.timer = null;
      this.audio = null; this.url = null; this.playing = null; this.finishing = null;
      this.snapshot = null; this.attempted = new Set(); this.aborts = new Set();
      this.operations = Promise.resolve();
    }
    state(text) { if (text) this.notice(text); this.update({enabled:this.enabled,connected:this.connected,changing:this.changing}); }
    valid(epoch) { return this.enabled && this.connected && epoch === this.epoch && this.context && this.context.valid(); }
    setupValid(epoch) { return this.enabled && epoch === this.epoch && this.context && this.context.valid(); }
    enqueue(fn) { const work = this.operations.catch(() => {}).then(fn); this.operations = work; return work; }
    async http(path, body, method, signal) {
      const options = {method,credentials:'same-origin',cache:'no-store',signal};
      if (body) {options.headers={'content-type':'application/json'}; options.body=JSON.stringify(body);}
      const response=await root.fetch(path,options);
      if (!response.ok) throw new Error('Private audio request did not finish');
      return response.json();
    }
    async call(path, values = {}, method = 'POST', timeout = 15000, consumerId = null) {
      const cid=consumerId || this.context.consumerId;
      const body={audience:'owner_private',consumer_id:cid,...values};
      let url=path;
      if (method==='GET') {url+='?'+new URLSearchParams(body);}
      const abort=new AbortController(), timer=root.setTimeout(() => abort.abort(),timeout);
      this.aborts.add(abort);
      try {return await this.request(url,method==='GET'?null:body,method,abort.signal);}
      finally {root.clearTimeout(timer);this.aborts.delete(abort);}
    }
    async readAudio(item, signal) {
      const params=new URLSearchParams({audience:'owner_private',consumer_id:this.context.consumerId,
        generation:item.generation,output_epoch:item.epoch});
      const response=await root.fetch('/api/coordination/interaction/audio/'+encodeURIComponent(item.id)+'?'+params,
        {credentials:'same-origin',cache:'no-store',signal});
      if (!response.ok) throw new Error('Private audio is no longer available');
      const blob=await response.blob();
      if (!blob.type.startsWith('audio/') || blob.size>4000000) throw new Error('Invalid private audio');
      return blob;
    }
    stopMedia() {
      if (this.audio) {this.audio.onplaying=this.audio.onended=this.audio.onerror=null;
        this.audio.pause();this.audio.removeAttribute('src');this.audio.load();}
      if (this.url) this.revokeURL(this.url);
      this.audio=this.url=this.playing=null;
    }
    fence() {
      this.epoch+=1;
      if (this.timer!==null) this.unschedule(this.timer);
      this.timer=null;
      for (const abort of this.aborts) abort.abort();
      this.stopMedia();this.finishing=null;this.queue.clear();this.snapshot=null;
      return this.epoch;
    }
    enable(context) {
      this.fence();this.context=context;this.enabled=true;this.connected=false;this.changing=true;
      const epoch=this.epoch,cid=context.consumerId;
      this.state('Connecting private speech…');
      return this.enqueue(async () => {
        try {
          if (!this.setupValid(epoch)) return;
          await this.call('/api/coordination/channel-audio/connect',{},'POST',15000,cid);
          if (!this.setupValid(epoch)) return;
          await this.call('/api/coordination/channel-audio/enable',{enabled:true},'POST',15000,cid);
          if (!this.setupValid(epoch)) return;
          this.connected=true;this.state('Listening to new contributions.');this.poll(epoch);
        } catch (_) {
          if (this.epoch===epoch) {this.enabled=false;this.connected=false;this.state('Speech could not connect. Turn listening on again when ready.');}
        } finally {if(this.epoch===epoch){if(!this.connected)this.enabled=false;this.changing=false;this.state();}}
      });
    }
    disable() {
      const cid=this.context && this.context.consumerId;
      this.enabled=false;this.connected=false;this.fence();this.changing=true;
      const epoch=this.epoch;this.state('Speech is off.');
      return this.enqueue(async () => {
        try {
          if (cid) {
            await this.call('/api/coordination/channel-audio/enable',{enabled:false},'POST',15000,cid);
            await this.call('/api/coordination/channel-audio/disconnect',{},'POST',15000,cid);
          }
        } catch (_) {if(this.epoch===epoch)this.state('Speech stopped here. The server connection could not confirm disconnecting.');}
        finally {if(this.epoch===epoch){this.changing=false;this.state();}}
      });
    }
    suspend() {const connecting=this.changing&&!this.connected;this.fence();if(connecting)void this.disable();}
    async changeContext(context) {
      this.fence();this.context=context;
      if (!this.enabled || !this.connected) return;
      const epoch=this.epoch,cid=context.consumerId;
      try {await this.enqueue(() => this.call('/api/coordination/channel-audio/interrupt',{},'POST',15000,cid));}
      catch (_) {if(this.epoch===epoch){this.enabled=false;this.connected=false;this.state('Speech stopped. Turn listening on again when ready.');}return;}
      if(this.valid(epoch))this.poll(epoch);
    }
    async render(outputId) {
      if (this.channel !== 'private_stream') return;
      return this.renderChecked(outputId, false);
    }
    async renderMoment(momentId) {
      if (this.channel !== 'shared_moment') return;
      return this.renderChecked(momentId, true);
    }
    async renderChecked(outputId, moment) {
      const epoch=this.epoch;
      if (!this.valid(epoch) || this.attempted.has(outputId) || this.attempted.size>=480) return;
      this.attempted.add(outputId);
      this.state('Preparing speech. The text is already displayed.');
      try {
        const result=await this.call('/api/coordination/channel-audio/' + (moment ? 'moment/render' : 'render'),
          moment ? {moment_id:outputId} : {stream_output_id:outputId},'POST',180000);
        if (!this.valid(epoch)) return;
        if (result.status==='busy') this.state('Speech was skipped while the voice was busy.');
        else if(result.status!=='generated')this.state('Speech was not completed. It will not be replayed automatically.');
      } catch (_) {if(this.valid(epoch))this.state('The speech outcome is uncertain. It has not been requested again.');}
    }
    current(item, value=this.snapshot) {
      return value && value.awake && value.consumer && value.consumer.connected
        && value.consumer.id===this.context.consumerId
        && (value.outputs || []).some(o => o.id===item.id && o.generation===item.generation
          && o.output_epoch===item.epoch && ((o.status==='played' && this.finishing && this.finishing.id===item.id)
            || (['generated','offered','attempted','started'].includes(o.status) && o.audio_available)));
    }
    sync(value) {
      const removed=this.queue.sync(value.generation,value.output_epoch);
      if(removed.length)this.stopMedia();
      this.snapshot=value;
      if ((this.queue.active && !this.current(this.queue.active)) || this.queue.pending.some(i=>!this.current(i))) {
        this.stopMedia();this.finishing=null;this.queue.clear();
      }
    }
    async ack(item,status,epoch) {
      const previous=item.ackChain || Promise.resolve();
      const operation=previous.catch(()=>false).then(async()=>{
        if(!this.valid(epoch))return false;
        try {await this.call('/api/coordination/interaction/ack',{output_id:item.id,
          generation:item.generation,output_epoch:item.epoch,status});return this.valid(epoch);}
        catch(_){return false;}
      });
      item.ackChain=operation;return operation;
    }
    async playNext(epoch) {
      if (!this.valid(epoch) || this.playing || this.finishing) return;
      const item=this.queue.start();if(!item)return;
      this.playing=item;
      const abort=new AbortController();this.aborts.add(abort);
      const fetchDeadline=root.setTimeout(()=>abort.abort(),15000);
      try {
        const blob=await this.fetchAudio(item,abort.signal);
        if(!this.valid(epoch)||this.playing?.id!==item.id)return;
        const latest=await this.call('/api/coordination/interaction/status',{},'GET');
        if(!this.valid(epoch)||this.playing?.id!==item.id)return;
        this.sync(latest);
        if(!this.current(item)||this.playing?.id!==item.id)return;
        this.url=this.makeURL(blob);this.audio=this.makeAudio(this.url);
        this.audio.onplaying=()=>{
          if(!this.valid(epoch)||this.playing?.id!==item.id){this.stopMedia();return;}
          this.state('Playing on this device.');
          void this.ack(item,'started',epoch).then(ok=>{if(!ok&&this.valid(epoch)){this.stopMedia();this.queue.clear();}});
        };
        const finish=async status=>{
          if(!this.valid(epoch)||this.playing?.id!==item.id)return;
          this.stopMedia();this.finishing=item;
          const confirmed=await this.ack(item,status,epoch);
          if(!this.valid(epoch)||this.finishing?.id!==item.id)return;
          this.finishing=null;
          if(confirmed&&status==='played'){this.queue.finish(item.id);this.state('Playback finished on this device.');}
          else {this.queue.clear();this.state('Playback ended without a confirmed receipt. Later chunks were stopped.');}
        };
        this.audio.onended=()=>{void finish('played');};
        this.audio.onerror=()=>{void finish('failed');};
        await this.audio.play();
      } catch (_) {
        if(this.valid(epoch)&&this.playing?.id===item.id){this.stopMedia();this.queue.clear();
          this.state('Speech did not play. Turn listening off and on when ready.');void this.ack(item,'failed',epoch);}
      } finally {root.clearTimeout(fetchDeadline);this.aborts.delete(abort);}
    }
    async poll(epoch) {
      if(!this.valid(epoch))return;
      try {
        const status=await this.call('/api/coordination/interaction/status',{},'GET');
        if(!this.valid(epoch))return;
        this.sync(status);
        if(!status.awake || !status.consumer?.connected){await this.disable();return;}
        const next=await this.call('/api/coordination/interaction/poll',{},'GET');
        if(!this.valid(epoch))return;
        if(next.generation!==this.queue.generation||next.output_epoch!==this.queue.epoch){this.stopMedia();this.queue.sync(next.generation,next.output_epoch);}
        if(next.output){
          const item={...next.output,epoch:next.output.output_epoch};
          if(item.channel!==this.channel||!item.audio_available)throw new Error('Unexpected audio origin');
          if(!this.queue.offer(item)&&!this.queue.seen.has(item.id)){this.stopMedia();this.queue.clear();void this.ack(item,'cancelled',epoch);}
        }
        await this.playNext(epoch);
      } catch (_) {
        if(this.valid(epoch)){await this.disable();this.state('Speech connection interrupted. Turn listening on again when ready.');}
        return;
      }
      if(this.valid(epoch))this.timer=this.schedule(()=>{this.timer=null;this.poll(epoch);});
    }
  }
  root.ShanaChannelAudioPlayer=ChannelAudioPlayer;
  if(typeof module!=='undefined'&&module.exports)module.exports={ChannelAudioPlayer};
}(typeof window!=='undefined'?window:globalThis));
