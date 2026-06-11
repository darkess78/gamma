// memory.js - Memory-related functions and UI handlers for the Gamma dashboard
(function () {
  var subtitleState = { transcript: '', reply: '', partial: '' };
  var pendingMemoryDeleteItems = [];
  var visionHistory = [];
  var LIVE_SPEECH_THRESHOLD = 0.018;
  var LIVE_SILENCE_MS = 900;

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

  function memoryDeleteCandidates(minutes) {
    var safeMinutes = Math.max(1, Number(minutes) || 10);
    var items = (latestData && latestData.memory_db && Array.isArray(latestData.memory_db.recent_items))
      ? latestData.memory_db.recent_items.slice(0)
      : [];
    var cutoff = Date.now() - (safeMinutes * 60 * 1000);
    return items.filter(function (item) {
      var createdAt = item && item.created_at ? Date.parse(item.created_at) : NaN;
      return isFinite(createdAt) && createdAt >= cutoff;
    });
  }

  function openMemoryDeleteModal(minutes) {
    var safeMinutes = Math.max(1, Number(minutes) || 10);
    pendingMemoryDeleteItems = memoryDeleteCandidates(safeMinutes).map(function (item) {
      return {
        id: Number(item.id || 0),
        kind: String(item.kind || ''),
        summary: String(item.summary || ''),
        created_at: item.created_at || null,
        subject_name: item.subject_name || '',
        session_id: item.session_id || '',
        selected: true
      };
    });
    var modal = document.getElementById('memoryDeleteModal');
    var summary = document.getElementById('memoryDeleteSummary');
    var list = document.getElementById('memoryDeleteList');
    if (!modal || !summary || !list) return;
    summary.textContent = 'Select which memory entries from the last ' + safeMinutes + ' minutes to delete.';
    if (!pendingMemoryDeleteItems.length) {
      list.textContent = 'No recent memory entries available.';
    } else {
      var rows = [];
      for (var i = 0; i < pendingMemoryDeleteItems.length; i += 1) {
        var item = pendingMemoryDeleteItems[i];
        var meta = [];
        meta.push((item.kind === 'episodic' ? 'Episodic' : 'Fact') + '#' + item.id);
        if (item.subject_name) meta.push(item.subject_name);
        if (item.session_id) meta.push('session' + item.session_id);
        if (item.created_at) meta.push(fmtLocalTime(item.created_at, 'n/a'));
        rows.push(
          '<label class="memory-delete-item">' +
          '<input type="checkbox" data-memory-index="' + i + '" checked onchange="toggleMemoryDeleteSelection(' + i + ', this.checked)">' +
          '<div><div>' + escapeHtml(item.summary || 'n/a') + '</div><div class="memory-delete-meta">' + escapeHtml(meta.join('|')) + '</div></div>' +
          '</label>'
        );
      }
      list.innerHTML = rows.join('');
    }
    modal.hidden = false;
  }

  function closeMemoryDeleteModal() {
    var modal = document.getElementById('memoryDeleteModal');
    if (modal) modal.hidden = true;
    pendingMemoryDeleteItems = [];
  }

  function toggleMemoryDeleteSelection(index, checked) {
    if (!pendingMemoryDeleteItems[index]) return;
    pendingMemoryDeleteItems[index].selected = !!checked;
  }

  function setAllMemorySelections(selected) {
    for (var i = 0; i < pendingMemoryDeleteItems.length; i += 1) {
      pendingMemoryDeleteItems[i].selected = !!selected;
    }
    var inputs = document.querySelectorAll('#memoryDeleteList input[type="checkbox"]');
    for (var j = 0; j < inputs.length; j += 1) {
      inputs[j].checked = !!selected;
    }
  }

  async function submitMemoryDeletion() {
    var selected = pendingMemoryDeleteItems
      .filter(function (item) { return item.selected; })
      .map(function (item) { return { id: item.id, kind: item.kind }; });
    if (!selected.length) {
      alert('Select at least one memory entry to delete.');
      return;
    }
    await action('/api/memory/clear-selected', { body: { items: selected } });
    closeMemoryDeleteModal();
  }

  function clearRecentMemory() {
    openMemoryDeleteModal(10);
  }

  function memoryData() {
    return (window.gammaDashboardStatus && window.gammaDashboardStatus.memory_db) || {};
  }

  function renderMemoryRecords(memory) {
    memory = memory || {};
    var peopleTarget = document.getElementById('knownPeople');
    var memoryTarget = document.getElementById('recentMemories');
    var people = Array.isArray(memory.known_people) ? memory.known_people : [];
    var items = Array.isArray(memory.recent_items) ? memory.recent_items : [];
    if (peopleTarget) {
      peopleTarget.classList.add('record-list');
      peopleTarget.innerHTML = people.length ? people.map(function (person) {
        var accounts = Array.isArray(person.accounts) ? person.accounts : [];
        var accountText = accounts.length ? accounts.map(function (account) {
          return escapeHtml(account.platform + ': ' + account.platform_user_id);
        }).join('<br>') : 'No linked accounts';
        return '<article class="record-card"><div><strong>' + escapeHtml(person.name || 'Unnamed') + '</strong>'
          + '<div class="record-meta">' + escapeHtml(person.relationship_to_user || 'Relationship not set')
          + ' | trust: ' + escapeHtml(person.trust || 'guest') + '</div>'
          + '<div class="record-meta">' + accountText + '</div>'
          + (person.notes ? '<div class="record-notes">' + escapeHtml(person.notes) + '</div>' : '')
          + '</div><div class="record-actions"><button class="ghost" onclick="editKnownPerson(' + Number(person.id || 0)
          + ')">Edit</button><button class="ghost danger-outline" onclick="deleteKnownPerson(' + Number(person.id || 0)
          + ')">Delete</button></div></article>';
      }).join('') : '<div class="empty-state">No known people stored yet. Add one to link Twitch, Discord, game, or other account IDs.</div>';
    }
    if (memoryTarget) {
      memoryTarget.classList.add('record-list');
      memoryTarget.innerHTML = items.length ? items.map(function (item) {
        return '<article class="record-card"><div><strong>' + escapeHtml(item.kind === 'episodic' ? 'Episodic memory' : 'Profile fact')
          + '</strong><div class="record-meta">' + escapeHtml(item.subject_name || item.subject_type || 'unscoped')
          + (item.created_at ? ' | ' + escapeHtml(fmtLocalDateTime(item.created_at)) : '') + '</div>'
          + '<div class="record-notes">' + escapeHtml(item.summary || 'No text stored') + '</div></div>'
          + '<div class="record-actions"><button class="ghost" onclick="editMemoryItem(' + Number(item.id || 0)
          + ', \'' + escapeHtml(item.kind || '') + '\')">Edit</button><button class="ghost danger-outline" onclick="deleteMemoryItem('
          + Number(item.id || 0) + ', \'' + escapeHtml(item.kind || '') + '\')">Delete</button></div></article>';
      }).join('') : '<div class="empty-state">No stored memories yet.</div>';
    }
  }

  function openRecordModal(title) {
    var modal = document.getElementById('memoryRecordModal');
    var heading = document.getElementById('memoryRecordTitle');
    if (heading) heading.textContent = title;
    if (modal) modal.hidden = false;
  }

  function closeMemoryRecordModal() {
    var modal = document.getElementById('memoryRecordModal');
    if (modal) modal.hidden = true;
  }

  function editMemoryItem(id, kind) {
    var item = (memoryData().recent_items || []).find(function (entry) {
      return Number(entry.id) === Number(id) && entry.kind === kind;
    });
    if (!item) return;
    document.getElementById('memoryRecordMode').value = 'memory';
    document.getElementById('memoryRecordId').value = item.id;
    document.getElementById('memoryRecordKind').value = item.kind;
    document.getElementById('memoryRecordName').value = item.subject_name || '';
    document.getElementById('memoryRecordRelationship').value = item.relationship_to_user || '';
    document.getElementById('memoryRecordCategory').value = item.category || '';
    document.getElementById('memoryRecordConfidence').value = item.confidence == null ? 0.5 : item.confidence;
    document.getElementById('memoryRecordText').value = item.summary || '';
    document.getElementById('memoryPersonFields').hidden = true;
    document.getElementById('memoryItemFields').hidden = false;
    openRecordModal('Edit Memory');
  }

  function editKnownPerson(id) {
    var person = (memoryData().known_people || []).find(function (entry) { return Number(entry.id) === Number(id); });
    if (!person) return;
    document.getElementById('memoryRecordMode').value = 'person';
    document.getElementById('memoryRecordId').value = person.id;
    document.getElementById('memoryRecordName').value = person.name || '';
    document.getElementById('memoryRecordRelationship').value = person.relationship_to_user || '';
    document.getElementById('memoryPersonTrust').value = person.trust || 'guest';
    document.getElementById('memoryRecordText').value = person.notes || '';
    document.getElementById('memoryPersonAccounts').value = (person.accounts || []).map(function (account) {
      return [account.platform, account.platform_user_id, account.display_name || ''].join(' | ');
    }).join('\n');
    document.getElementById('memoryPersonFields').hidden = false;
    document.getElementById('memoryItemFields').hidden = true;
    openRecordModal('Edit Known Person');
  }

  function addKnownPerson() {
    document.getElementById('memoryRecordMode').value = 'person';
    document.getElementById('memoryRecordId').value = '';
    document.getElementById('memoryRecordName').value = 'Example Viewer';
    document.getElementById('memoryRecordRelationship').value = 'stream viewer';
    document.getElementById('memoryPersonTrust').value = 'guest';
    document.getElementById('memoryRecordText').value = 'Example record. Replace these values with the real person details.';
    document.getElementById('memoryPersonAccounts').value = 'twitch | example_twitch_id | ExampleViewer\ndiscord | example_discord_id | Example Viewer';
    document.getElementById('memoryPersonFields').hidden = false;
    document.getElementById('memoryItemFields').hidden = true;
    openRecordModal('Add Known Person');
  }

  async function saveMemoryRecord() {
    var mode = document.getElementById('memoryRecordMode').value;
    var payload = {
      id: Number(document.getElementById('memoryRecordId').value || 0),
      name: document.getElementById('memoryRecordName').value.trim(),
      relationship_to_user: document.getElementById('memoryRecordRelationship').value.trim(),
      notes: document.getElementById('memoryRecordText').value.trim()
    };
    var path = '/api/memory/people';
    if (mode === 'person') {
      payload.trust = document.getElementById('memoryPersonTrust').value;
      payload.accounts = document.getElementById('memoryPersonAccounts').value.split('\n').map(function (line) {
        var parts = line.split('|').map(function (value) { return value.trim(); });
        return { platform: parts[0] || '', platform_user_id: parts[1] || '', display_name: parts[2] || '' };
      }).filter(function (account) { return account.platform && account.platform_user_id; });
    } else {
      path = '/api/memory/item';
      payload.kind = document.getElementById('memoryRecordKind').value;
      payload.summary = payload.notes;
      payload.subject_name = payload.name;
      payload.category = document.getElementById('memoryRecordCategory').value.trim();
      payload.confidence = Number(document.getElementById('memoryRecordConfidence').value || 0.5);
    }
    await action(path, { body: payload });
    closeMemoryRecordModal();
  }

  async function deleteMemoryItem(id, kind) {
    if (!confirm('Delete this memory permanently?')) return;
    await action('/api/memory/clear-selected', { body: { items: [{ id: id, kind: kind }] } });
  }

  async function deleteKnownPerson(id) {
    if (!confirm('Delete this known person and their linked account IDs? Stored memories about them will remain.')) return;
    var response = await fetch('/api/memory/people/' + encodeURIComponent(id), { method: 'DELETE' });
    var payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || ('HTTP ' + response.status));
    await window.loadStatus();
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

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function humanRecentMemories(items) {
    if (!items || !items.length) {
      return 'No stored memories yet.';
    }
    var lines = ['Latest stored memory items by save order:'];
    for (var i = 0; i < items.length; i += 1) {
      var item = items[i] || {};
      var label = item.kind === 'episodic' ? 'Episodic' : 'Fact';
      var subject = item.subject_name ? ' [' + item.subject_name + ']' : '';
      var scope = item.session_id ? ' {session' + item.session_id + '}' : '';
      var when = item.created_at ? ' @ ' + fmtLocalTime(item.created_at, 'n/a') : '';
      lines.push('- ' + label + subject + scope + when + ': ' + (item.summary || 'n/a'));
    }
    return lines.join('\n');
  }

  function humanMemoryStats(stats) {
    var lines = [
      'Backend: ' + (stats.backend || 'n/a'),
      'Database: ' + (stats.database || 'n/a'),
      'Profile facts: ' + (typeof stats.profile_count === 'undefined' ? 'n/a' : stats.profile_count),
      'Episodic memories: ' + (typeof stats.episodic_count === 'undefined' ? 'n/a' : stats.episodic_count),
      'Session-scoped episodic memories: ' + (typeof stats.session_scoped_episodic_count === 'undefined' ? 'n/a' : stats.session_scoped_episodic_count),
      'Known people: ' + (typeof stats.known_people_count === 'undefined' ? 'n/a' : stats.known_people_count)
    ];
    return lines.join('\n');
  }

  function humanKnownPeople(people) {
    if (!people || !people.length) {
      return 'No known people stored yet.';
    }
    var lines = [];
    var i;
    for (i = 0; i < people.length; i += 1) {
      var person = people[i];
      var rel = person.relationship_to_user ? ' (' + person.relationship_to_user + ')' : '';
      lines.push('- ' + (person.name || 'Unnamed') + rel);
    }
    return lines.join('\n');
  }

  function humanVisionAnalysis(vision) {
    if (!vision) {
      return 'No vision analysis available.';
    }
    var lines = [
      'Image type: ' + (vision.image_type || 'unknown'),
      'Summary: ' + (vision.summary || 'n/a'),
      'Confidence: ' + (typeof vision.confidence === 'number' ? vision.confidence.toFixed(2) : 'n/a')
    ];
    if (vision.visible_text) {
      lines.push('');
      lines.push('Visible text:');
      lines.push(vision.visible_text);
    }
    if (vision.key_text_blocks && vision.key_text_blocks.length) {
      lines.push('');
      lines.push('Key text blocks:');
      for (var b = 0; b < vision.key_text_blocks.length; b += 1) {
        var block = vision.key_text_blocks[b];
        lines.push('- ' + (block.label || block.block_type || 'text') + ': ' + block.text);
      }
    }
    if (vision.interface_elements && vision.interface_elements.length) {
      lines.push('');
      lines.push('Interface elements:');
      for (var e = 0; e < vision.interface_elements.length; e += 1) {
        var element = vision.interface_elements[e];
        var details = [element.element_type || 'unknown'];
        if (element.role) details.push(element.role);
        if (element.state) details.push(element.state);
        lines.push('- ' + element.name + ' [' + details.join('|') + ']');
      }
    }
    if (vision.document_structure && vision.document_structure.length) {
      lines.push('');
      lines.push('Document structure:');
      for (var d = 0; d < vision.document_structure.length; d += 1) {
        lines.push('- ' + vision.document_structure[d]);
      }
    }
    if (vision.likely_actions && vision.likely_actions.length) {
      lines.push('');
      lines.push('Likely actions:');
      for (var a = 0; a < vision.likely_actions.length; a += 1) {
        lines.push('- ' + vision.likely_actions[a]);
      }
    }
    if (vision.objects && vision.objects.length) {
      lines.push('');
      lines.push('Objects:');
      for (var i = 0; i < vision.objects.length; i += 1) {
        var object = vision.objects[i];
        lines.push('- ' + object.name + (object.description ? ': ' + object.description : '') + ' [' + (typeof object.confidence === 'number' ? object.confidence.toFixed(2) : 'n/a') + ']');
      }
    }
    if (vision.spatial_notes && vision.spatial_notes.length) {
      lines.push('');
      lines.push('Spatial notes:');
      for (var j = 0; j < vision.spatial_notes.length; j += 1) {
        lines.push('- ' + vision.spatial_notes[j]);
      }
    }
    if (vision.suggested_follow_ups && vision.suggested_follow_ups.length) {
      lines.push('');
      lines.push('Suggested follow-ups:');
      for (var k = 0; k < vision.suggested_follow_ups.length; k += 1) {
        lines.push('- ' + vision.suggested_follow_ups[k]);
      }
    }
    return lines.join('\n');
  }

  function humanVisionReply(payload) {
    if (!payload) {
      return 'No Gamma vision reply available.';
    }
    var lines = [
      'Reply: ' + (payload.spoken_text || 'n/a'),
      'Emotion: ' + (payload.emotion || 'neutral')
    ];
    if (payload.vision) {
      lines.push('');
      lines.push(humanVisionAnalysis(payload.vision));
    }
    if (payload.audio_path) {
      lines.push('');
      lines.push('Audio artifact: ' + payload.audio_path);
    }
    return lines.join('\n');
  }

  function humanVisionHistory() {
    if (!visionHistory.length) {
      return 'No recent vision history.';
    }
    var lines = [];
    for (var i = 0; i < visionHistory.length; i += 1) {
      var item = visionHistory[i];
      lines.push('[' + fmtLocalDateTime(item.timestamp) + '] ' + (item.kind || 'vision'));
      lines.push('File: ' + (item.file_name || 'n/a'));
      lines.push('Mode: ' + (item.vision_mode || 'auto'));
      lines.push('Prompt: ' + (item.user_text || 'n/a'));
      if (item.summary) lines.push('Summary: ' + item.summary);
      if (item.reply_text) lines.push('Reply: ' + item.reply_text);
      lines.push('');
    }
    return lines.join('\n').trim();
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

  function pretty(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
    }
  }

  var sectionHashes = {};
  var viewMode = localStorage.getItem('gammaDashboardViewMode') || 'human';
  var latestData = null;

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

  function pushVisionHistory(entry) {
    visionHistory.unshift(entry);
    if (visionHistory.length > 8) {
      visionHistory = visionHistory.slice(0, 8);
    }
    saveVisionHistory();
    renderVisionHistory();
  }

  function renderVisionHistory() {
    renderBlock('visionHistory', visionHistory, humanVisionHistory());
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

  function renderBlock(elementId, rawValue, humanText) {
    var el = document.getElementById(elementId);
    if (!el) return;
    if (viewMode === 'json') {
      el.textContent = pretty(rawValue);
    } else {
      el.innerHTML = escapeHtml(humanText);
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  window.clearRecentMemory = clearRecentMemory;
  window.closeMemoryDeleteModal = closeMemoryDeleteModal;
  window.setAllMemorySelections = setAllMemorySelections;
  window.submitMemoryDeletion = submitMemoryDeletion;
  window.renderMemoryRecords = renderMemoryRecords;
  window.editMemoryItem = editMemoryItem;
  window.deleteMemoryItem = deleteMemoryItem;
  window.editKnownPerson = editKnownPerson;
  window.addKnownPerson = addKnownPerson;
  window.deleteKnownPerson = deleteKnownPerson;
  window.saveMemoryRecord = saveMemoryRecord;
  window.closeMemoryRecordModal = closeMemoryRecordModal;
})();
