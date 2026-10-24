class PlaybackRequests {
  constructor(send, { timeoutMs = 3000 } = {}) {
    this._send = send;
    this._timeoutMs = timeoutMs;
    this._pending = new Map();
  }

  _key(lease_id, event_id) {
    return JSON.stringify([lease_id, event_id]);
  }

  async request(receipt) {
    if (!receipt || receipt.schema_version !== 1) throw new Error('schema_version must be 1');
    if (typeof receipt.lease_id !== 'string' || !receipt.lease_id.length || receipt.lease_id.length > 128)
      throw new Error('invalid lease_id');
    if (typeof receipt.event_id !== 'string' || !receipt.event_id.length || receipt.event_id.length > 128)
      throw new Error('invalid event_id');

    const key = this._key(receipt.lease_id, receipt.event_id);
    if (this._pending.has(key)) return Promise.reject(new Error('duplicate pending'));
    if (this._pending.size >= 32) return Promise.reject(new Error('too many pending'));

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pending.delete(key);
        reject(new Error('timeout'));
      }, this._timeoutMs);

      this._pending.set(key, { resolve, reject, timer });

      try {
        this._send({ type: 'playback_prepare', schema_version: 1, lease_id: receipt.lease_id, event_id: receipt.event_id });
      } catch (e) {
        clearTimeout(timer);
        this._pending.delete(key);
        reject(e);
      }
    });
  }

  receive(message) {
    if (!message || (message.type !== 'playback_permit' && message.type !== 'playback_rejected')) return false;
    const key = this._key(message.lease_id, message.event_id);
    const entry = this._pending.get(key);
    if (!entry) return false;

    clearTimeout(entry.timer);
    this._pending.delete(key);

    if (message.type === 'playback_permit') {
      const now = Date.now() / 1000;
      if (typeof message.expires_at === 'number' && Number.isFinite(message.expires_at) &&
          message.expires_at > now && message.expires_at <= now + 10) {
        entry.resolve(message);
      } else {
        entry.reject(new Error('invalid expires_at'));
      }
    } else {
      entry.reject(new Error('rejected'));
    }
    return true;
  }

  cancelAll() {
    for (const entry of this._pending.values()) {
      clearTimeout(entry.timer);
      entry.reject(new Error('cancelled'));
    }
    this._pending.clear();
  }

  cancelReceipt(lease_id, event_id) {
    const key = this._key(lease_id, event_id);
    const entry = this._pending.get(key);
    if (!entry) return false;
    clearTimeout(entry.timer);
    this._pending.delete(key);
    entry.reject(new Error('revoked'));
    return true;
  }
}

function _validateRefs(receipt) {
  if (!/^[a-f0-9]{32}$/.test(receipt.grant_id) || !/^[a-f0-9]{64}$/.test(receipt.audio_digest || '')) {
    throw new Error('Invalid approved audio reference');
  }
}

async function _downloadAndVerify(response, receipt) {
  if (!response.ok) throw new Error('Approved audio unavailable');
  const reader = response.body.getReader();
  const parts = [];
  let size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > 33554432) { await reader.cancel(); throw new Error('Approved audio exceeds size limit'); }
      parts.push(chunk.value);
    }
  } finally { reader.releaseLock(); }
  if (!size) throw new Error('Empty approved audio');
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) { bytes.set(part, offset); offset += part.byteLength; }
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const hex = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
  if (hex !== receipt.audio_digest) throw new Error('Approved audio integrity failed');
  return URL.createObjectURL(new Blob([bytes], {type: response.headers.get('Content-Type') || 'audio/wav'}));
}

PlaybackRequests.loadAudio = async function (receipt, apiBase, token, signal) {
  if (!receipt.grant_id) return null;
  _validateRefs(receipt);
  const url = new URL(`${apiBase.replace(/\/$/, '')}/v1/delivery/${receipt.grant_id}/audio`, location.origin);
  const response = await fetch(url, {signal, cache: 'no-store', redirect: 'error',
    headers: token ? {Authorization: `Bearer ${token}`} : {}});
  return _downloadAndVerify(response, receipt);
};

PlaybackRequests.loadDashboardAudio = async function (receipt, signal) {
  if (!receipt || !receipt.grant_id) throw new Error('Invalid approved audio reference');
  _validateRefs(receipt);
  const url = new URL(`/api/playback/audio/${receipt.grant_id}`, location.origin);
  const response = await fetch(url, {signal, cache: 'no-store', redirect: 'error', credentials: 'same-origin', headers: {}});
  return _downloadAndVerify(response, receipt);
};

if (typeof module !== 'undefined') module.exports = PlaybackRequests;
if (typeof window !== 'undefined') window.PlaybackRequests = PlaybackRequests;
