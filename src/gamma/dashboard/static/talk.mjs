import { element, requestJson, setText } from './core.mjs';

const SESSION_KEY = 'gammaTalkSessionId';
const HISTORY_PREFIX = 'gammaTalkHistory:';
const HISTORY_LIMIT = 50;

let sessionId = loadSessionId();
let history = loadHistory(sessionId);
let submitting = false;

function newSessionId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  return `talk-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function loadSessionId() {
  const existing = window.localStorage.getItem(SESSION_KEY);
  if (existing) return existing;
  const created = newSessionId();
  window.localStorage.setItem(SESSION_KEY, created);
  return created;
}

function historyKey(id) {
  return `${HISTORY_PREFIX}${id}`;
}

function loadHistory(id) {
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(historyKey(id)) || '[]');
    return Array.isArray(parsed) ? parsed.slice(-HISTORY_LIMIT) : [];
  } catch (_error) {
    return [];
  }
}

function saveHistory() {
  history = history.slice(-HISTORY_LIMIT);
  window.sessionStorage.setItem(historyKey(sessionId), JSON.stringify(history));
}

function renderHistory() {
  const target = element('talkHistory');
  target.replaceChildren();
  if (!history.length) {
    const empty = document.createElement('p');
    empty.className = 'talk-empty';
    empty.textContent = 'Start a conversation with Shana.';
    target.append(empty);
    return;
  }
  history.forEach((message) => {
    const item = document.createElement('div');
    item.className = `talk-message talk-message-${message.role === 'user' ? 'user' : 'shana'}`;
    item.textContent = message.text;
    target.append(item);
  });
  target.scrollTop = target.scrollHeight;
}

function appendMessage(role, text) {
  history.push({ role, text: String(text || ''), at: new Date().toISOString() });
  saveHistory();
  renderHistory();
}

function setSubmitting(active) {
  submitting = active;
  element('talkSendButton').disabled = active;
  element('talkInput').disabled = active;
  setText('talkStatus', active ? 'Shana is thinking…' : 'Ready');
}

async function submitMessage(event) {
  event.preventDefault();
  if (submitting) return;
  const input = element('talkInput');
  const userText = input.value.trim();
  if (!userText) return;

  input.value = '';
  appendMessage('user', userText);
  setSubmitting(true);
  try {
    const response = await requestJson('/api/conversation/respond', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_text: userText,
        session_id: sessionId,
        synthesize_speech: false,
        fast_mode: false
      })
    });
    appendMessage('shana', response.spoken_text || 'Shana returned an empty response.');
  } catch (error) {
    setText('talkStatus', 'Request failed');
    appendMessage('shana', `I could not complete that turn. ${String(error)}`);
  } finally {
    setSubmitting(false);
    input.focus();
  }
}

function startNewConversation() {
  sessionId = newSessionId();
  window.localStorage.setItem(SESSION_KEY, sessionId);
  history = [];
  saveHistory();
  syncVoiceSession();
  renderHistory();
  setText('talkStatus', 'New conversation');
  element('talkInput').focus();
}

function syncVoiceSession() {
  const voiceSession = element('voiceSessionId');
  if (voiceSession) voiceSession.value = sessionId;
}

element('talkForm').addEventListener('submit', submitMessage);
element('newConversationButton').addEventListener('click', startNewConversation);
element('talkInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    element('talkForm').requestSubmit();
  }
});

syncVoiceSession();
renderHistory();
