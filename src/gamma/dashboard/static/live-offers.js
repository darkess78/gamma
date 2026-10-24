// Bounded correlation of core live audio offers with the owning live session.
class LiveOffers {
  constructor(deliver, {now = Date.now} = {}) {
    this.deliver = deliver; this.now = now; this.clear();
  }
  clear() { this.turn = null; this.pending = []; this.seen = new Set(); }
  own(turn) {
    if (typeof turn !== 'string' || !turn.length || turn.length > 128) return;
    if (this.turn !== turn) this.seen.clear();
    this.turn = turn;
    const pending = this.pending;
    this.pending = pending.filter(item => item.until > this.now() && item.event.turn_id !== turn);
    for (const item of pending) if (item.until > this.now() && item.event.turn_id === turn) this.receive(item.event);
  }
  receive(event) {
    const p = event && event.playback;
    const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 128;
    if (!event || event.source !== 'core_live' || event.type !== 'speech_chunk_ready' ||
        !validId(event.turn_id) || !p || p.schema_version !== 1 || !validId(p.lease_id) || !validId(p.event_id) ||
        !/^[a-f0-9]{32}$/.test(p.grant_id || '') || !/^[a-f0-9]{64}$/.test(p.audio_digest || '')) return;
    const key = JSON.stringify([p.lease_id, p.event_id]);
    if (this.seen.has(key)) return;
    this.pending = this.pending.filter(item => item.until > this.now());
    if (event.turn_id !== this.turn) {
      if (!this.pending.some(item => item.key === key)) this.pending.push({key, event, until: this.now() + 5000});
      this.pending = this.pending.slice(-16); return;
    }
    this.seen.add(key);
    if (this.seen.size > 128) this.seen.delete(this.seen.values().next().value);
    this.deliver(event);
  }
}
if (typeof module !== 'undefined') module.exports = LiveOffers;
if (typeof window !== 'undefined') window.LiveOffers = LiveOffers;
