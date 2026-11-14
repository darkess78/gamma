/* Bounded local playback queue. Server generation and output epoch own validity. */
(function (root) {
  'use strict';
  class PlaybackQueue {
    constructor(maximum = 8) {
      if (!Number.isSafeInteger(maximum) || maximum < 1 || maximum > 64) throw new TypeError('Invalid queue limit');
      this.maximum = maximum;
      this.generation = null;
      this.epoch = null;
      this.pending = [];
      this.active = null;
      this.seen = new Set();
      this.utterances = new Map();
    }
    sync(generation, epoch) {
      if (![generation, epoch].every(n => Number.isSafeInteger(n) && n > 0)) throw new TypeError('Invalid output identity');
      if (generation === this.generation && epoch === this.epoch) return [];
      const removed = this.clear();
      this.generation = generation;
      this.epoch = epoch;
      this.seen.clear();
      this.utterances.clear();
      return removed;
    }
    offer(item) {
      if (!item || typeof item.id !== 'string' || !item.id || item.id.length > 128
          || item.generation !== this.generation || item.epoch !== this.epoch
          || this.seen.has(item.id) || this.seen.size >= 1024
          || this.pending.length + (this.active ? 1 : 0) >= this.maximum) return false;
      const chunked = ['utterance_id','chunk_index','chunk_count','final_chunk'].some(key => key in item);
      if (chunked) {
        if (typeof item.utterance_id !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(item.utterance_id)
            || !Number.isSafeInteger(item.chunk_count) || item.chunk_count < 1 || item.chunk_count > 4
            || !Number.isSafeInteger(item.chunk_index) || item.chunk_index < 0 || item.chunk_index >= item.chunk_count
            || item.final_chunk !== (item.chunk_index === item.chunk_count - 1)) return false;
        let utterance = this.utterances.get(item.utterance_id);
        if (!utterance) {
          if (this.utterances.size >= 1024) return false;
          if (item.chunk_index !== 0) {
            this.utterances.set(item.utterance_id, {count: item.chunk_count, next: 0, closed: true});
            return false;
          }
          utterance = {count: item.chunk_count, next: 0, closed: false};
        }
        if (utterance.closed || utterance.count !== item.chunk_count || utterance.next !== item.chunk_index) {
          utterance.closed = true;
          return false;
        }
        utterance.next += 1;
        this.utterances.set(item.utterance_id, utterance);
      }
      this.seen.add(item.id);
      this.pending.push({...item});
      return true;
    }
    start() {
      if (this.active || !this.pending.length) return null;
      this.active = this.pending.shift();
      return {...this.active};
    }
    finish(id) {
      if (!this.active || this.active.id !== id) return false;
      this.active = null;
      return true;
    }
    clear() {
      const removed = this.active ? [this.active, ...this.pending] : [...this.pending];
      for (const item of removed) {
        const utterance = this.utterances.get(item.utterance_id);
        if (utterance) utterance.closed = true;
      }
      this.active = null;
      this.pending = [];
      return removed.map(item => ({...item}));
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {PlaybackQueue};
  else root.ShanaPlaybackQueue = PlaybackQueue;
}(typeof globalThis !== 'undefined' ? globalThis : this));
