// memory.js - People & Memory page: known people, linked accounts, memories and forgetting.
(function () {
  'use strict';

  var pendingMemoryDeleteItems = [];

  function memoryDeleteCandidates(minutes) {
    var safeMinutes = Math.max(1, Number(minutes) || 10);
    var memory = memoryData();
    var items = Array.isArray(memory.recent_items) ? memory.recent_items.slice(0) : [];
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
        meta.push((item.kind === 'episodic' ? 'Episodic' : 'Fact') + ' #' + item.id);
        if (item.subject_name) meta.push(item.subject_name);
        if (item.session_id) meta.push('session ' + item.session_id);
        if (item.created_at) meta.push(fmtLocalTime(item.created_at, 'n/a'));
        rows.push(
          '<label class="memory-delete-item">' +
          '<input type="checkbox" data-memory-index="' + i + '" checked>' +
          '<div><div>' + escapeHtml(item.summary || 'n/a') + '</div><div class="memory-delete-meta">' + escapeHtml(meta.join(' / ')) + '</div></div>' +
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
    await window.action('/api/memory/clear-selected', { body: { items: selected } });
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
          + '</div><div class="record-actions"><button class="ghost small" type="button" data-person-edit="' + Number(person.id || 0)
          + '">Edit</button><button class="ghost danger-outline small" type="button" data-person-delete="' + Number(person.id || 0)
          + '">Delete</button></div></article>';
      }).join('') : '<div class="empty-state">No known people stored yet. Add one to link Twitch, Discord, game, or other account IDs.</div>';
    }
    if (memoryTarget) {
      memoryTarget.classList.add('record-list');
      memoryTarget.innerHTML = items.length ? items.map(function (item) {
        return '<article class="record-card"><div><strong>' + escapeHtml(item.kind === 'episodic' ? 'Episodic memory' : 'Profile fact')
          + '</strong><div class="record-meta">' + escapeHtml(item.subject_name || item.subject_type || 'unscoped')
          + (item.created_at ? ' | ' + escapeHtml(fmtLocalDateTime(item.created_at)) : '') + '</div>'
          + '<div class="record-notes">' + escapeHtml(item.summary || 'No text stored') + '</div></div>'
          + '<div class="record-actions"><button class="ghost small" type="button" data-memory-edit="' + Number(item.id || 0)
          + '" data-memory-kind="' + escapeHtml(item.kind || '') + '">Edit</button><button class="ghost danger-outline small" type="button" data-memory-delete="'
          + Number(item.id || 0) + '" data-memory-kind="' + escapeHtml(item.kind || '') + '">Forget</button></div></article>';
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

  function setMemoryRecordMode(mode) {
    var isPerson = mode === 'person';
    document.getElementById('memoryRecordMode').value = mode;
    document.getElementById('memoryPersonFields').hidden = !isPerson;
    document.getElementById('memoryItemFields').hidden = isPerson;
    document.getElementById('memoryRecordTextLabel').textContent = isPerson ? 'Person notes' : 'Memory text';
  }

  function addPersonAccountRow(account) {
    account = account || {};
    var list = document.getElementById('memoryPersonAccounts');
    if (!list) return;
    var row = document.createElement('div');
    row.className = 'memory-account-row';
    row.innerHTML = '<input class="text-input memory-account-platform" list="memoryPlatformSuggestions" placeholder="Platform" title="Examples: Twitch, Discord, YouTube, game, or another service.">'
      + '<input class="text-input memory-account-id" placeholder="Account or user ID" title="Use the stable platform ID when available, not only a display name.">'
      + '<input class="text-input memory-account-display" placeholder="Display name">'
      + '<button class="ghost danger-outline" type="button" title="Remove this linked identity">Remove</button>';
    row.querySelector('.memory-account-platform').value = String(account.platform || 'twitch').toLowerCase();
    row.querySelector('.memory-account-id').value = account.platform_user_id || '';
    row.querySelector('.memory-account-display').value = account.display_name || '';
    row.querySelector('button').addEventListener('click', function () { row.remove(); });
    list.appendChild(row);
  }

  function setPersonAccountRows(accounts) {
    var list = document.getElementById('memoryPersonAccounts');
    if (!list) return;
    list.innerHTML = '';
    (accounts && accounts.length ? accounts : [{}]).forEach(addPersonAccountRow);
  }

  function collectPersonAccounts() {
    return Array.prototype.map.call(document.querySelectorAll('#memoryPersonAccounts .memory-account-row'), function (row) {
      return {
        platform: row.querySelector('.memory-account-platform').value.trim(),
        platform_user_id: row.querySelector('.memory-account-id').value.trim(),
        display_name: row.querySelector('.memory-account-display').value.trim()
      };
    }).filter(function (account) { return account.platform && account.platform_user_id; });
  }

  function editMemoryItem(id, kind) {
    var item = (memoryData().recent_items || []).find(function (entry) {
      return Number(entry.id) === Number(id) && entry.kind === kind;
    });
    if (!item) return;
    setMemoryRecordMode('memory');
    document.getElementById('memoryRecordId').value = item.id;
    document.getElementById('memoryRecordKind').value = item.kind;
    document.getElementById('memoryRecordName').value = item.subject_name || '';
    document.getElementById('memoryRecordRelationship').value = item.relationship_to_user || '';
    document.getElementById('memoryRecordSubjectType').value = item.subject_type || 'primary_user';
    document.getElementById('memoryRecordCategory').value = item.category || '';
    document.getElementById('memoryRecordConfidence').value = item.confidence == null ? 0.5 : item.confidence;
    document.getElementById('memoryRecordSessionId').value = item.session_id || '';
    document.getElementById('memoryRecordText').value = item.summary || '';
    openRecordModal('Edit Memory');
  }

  function editKnownPerson(id) {
    var person = (memoryData().known_people || []).find(function (entry) { return Number(entry.id) === Number(id); });
    if (!person) return;
    setMemoryRecordMode('person');
    document.getElementById('memoryRecordId').value = person.id;
    document.getElementById('memoryRecordName').value = person.name || '';
    document.getElementById('memoryRecordRelationship').value = person.relationship_to_user || '';
    document.getElementById('memoryPersonTrust').value = person.trust || 'guest';
    document.getElementById('memoryRecordText').value = person.notes || '';
    setPersonAccountRows(person.accounts || []);
    openRecordModal('Edit Known Person');
  }

  function addKnownPerson() {
    setMemoryRecordMode('person');
    document.getElementById('memoryRecordId').value = '';
    document.getElementById('memoryRecordName').value = '';
    document.getElementById('memoryRecordRelationship').value = '';
    document.getElementById('memoryPersonTrust').value = 'guest';
    document.getElementById('memoryRecordText').value = '';
    setPersonAccountRows([]);
    openRecordModal('Add Known Person');
  }

  function addMemoryItem() {
    setMemoryRecordMode('memory');
    document.getElementById('memoryRecordId').value = '';
    document.getElementById('memoryRecordKind').value = 'profile_fact';
    document.getElementById('memoryRecordName').value = 'Neety';
    document.getElementById('memoryRecordRelationship').value = 'owner';
    document.getElementById('memoryRecordSubjectType').value = 'primary_user';
    document.getElementById('memoryRecordCategory').value = '';
    document.getElementById('memoryRecordConfidence').value = '0.8';
    document.getElementById('memoryRecordSessionId').value = '';
    document.getElementById('memoryRecordText').value = '';
    openRecordModal('Add Memory');
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
      payload.accounts = collectPersonAccounts();
    } else {
      path = payload.id ? '/api/memory/item' : '/api/memory/item/create';
      payload.kind = document.getElementById('memoryRecordKind').value;
      payload.summary = payload.notes;
      payload.subject_name = payload.name;
      payload.subject_type = document.getElementById('memoryRecordSubjectType').value;
      payload.category = document.getElementById('memoryRecordCategory').value.trim();
      payload.confidence = Number(document.getElementById('memoryRecordConfidence').value || 0.5);
      payload.session_id = document.getElementById('memoryRecordSessionId').value.trim();
    }
    await window.action(path, { body: payload });
    closeMemoryRecordModal();
  }

  async function deleteMemoryItem(id, kind) {
    if (!confirm('Forget this memory permanently?')) return;
    await window.action('/api/memory/clear-selected', { body: { items: [{ id: id, kind: kind }] } });
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
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }


  document.addEventListener('change', function (event) {
    var input = event.target;
    if (input && input.matches && input.matches('#memoryDeleteList input[data-memory-index]')) {
      toggleMemoryDeleteSelection(Number(input.getAttribute('data-memory-index')), input.checked);
    }
  });

  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('button') : null;
    if (!button) return;
    var data = button.dataset;
    if (data.personEdit) editKnownPerson(Number(data.personEdit));
    else if (data.personDelete) deleteKnownPerson(Number(data.personDelete)).catch(function (error) {
      if (window.showActionResult) window.showActionResult('Delete failed: ' + String(error.message || error), 'bad');
    });
    else if (data.memoryEdit) editMemoryItem(Number(data.memoryEdit), data.memoryKind);
    else if (data.memoryDelete) deleteMemoryItem(Number(data.memoryDelete), data.memoryKind).catch(function () {});
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    closeMemoryRecordModal();
    closeMemoryDeleteModal();
  });

  window.clearRecentMemory = clearRecentMemory;
  window.closeMemoryDeleteModal = closeMemoryDeleteModal;
  window.setAllMemorySelections = setAllMemorySelections;
  window.submitMemoryDeletion = submitMemoryDeletion;
  window.renderMemoryRecords = renderMemoryRecords;
  window.addKnownPerson = addKnownPerson;
  window.addPersonAccountRow = addPersonAccountRow;
  window.addMemoryItem = addMemoryItem;
  window.saveMemoryRecord = saveMemoryRecord;
  window.closeMemoryRecordModal = closeMemoryRecordModal;
})();
