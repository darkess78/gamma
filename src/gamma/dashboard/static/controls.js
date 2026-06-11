// Dashboard controls that are independent of the shared status renderer.
(function () {
  'use strict';

  function element(id) {
    return document.getElementById(id);
  }

  function value(id) {
    var target = element(id);
    return target ? target.value : '';
  }

  function checked(id) {
    var target = element(id);
    return !!(target && target.checked);
  }

  function setText(id, text) {
    var target = element(id);
    if (target) target.textContent = text;
  }

  async function jsonRequest(path, options) {
    var response = await fetch(path, options || {});
    var payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || ('HTTP ' + response.status));
    return payload;
  }

  function toggleViewMode() {
    var input = element('viewModeSwitch');
    localStorage.setItem('gammaDashboardViewMode', input && input.checked ? 'json' : 'human');
    if (window.loadStatus) window.loadStatus();
  }

  async function saveAssistantSettings() {
    var payload = {
      speech_filter_level: value('assistantSpeechFilterLevel'),
      speech_filter_hard_block_enabled: checked('assistantHardBlockEnabled'),
      speech_filter_heuristic_enabled: checked('assistantHeuristicEnabled'),
      speech_filter_llm_enabled: checked('assistantLlmEnabled'),
      speech_filter_auto_rewrite: checked('assistantAutoRewrite'),
      speech_filter_llm_model: value('assistantLlmModel'),
      speech_filter_llm_temperature: Number(value('assistantLlmTemperature') || 0),
      stream_safety_review_timeout_seconds: Number(value('assistantSafetyReviewTimeout') || 2),
      stream_safety_review_timeout_action: value('assistantSafetyReviewTimeoutAction'),
      llm_router_profile: value('assistantRouterProfile'),
      llm_router_allow_hosted_escalation: checked('assistantRouterHostedEscalation'),
      llm_router_persona_hosted_fallback_enabled: checked('assistantRouterPersonaHosted'),
      llm_router_persona_heavy_hosted_fallback_enabled: checked('assistantRouterPersonaHeavyHosted'),
      llm_router_chat_light_max_input_words: Number(value('assistantRouterLightWords') || 40),
      llm_router_complex_max_input_words: Number(value('assistantRouterComplexWords') || 120),
      assistant_state_enabled: checked('assistantStateEnabled'),
      assistant_emotion_decay_turns: Number(value('assistantEmotionDecayTurns') || 0),
      assistant_emotion_episode_threshold: Number(value('assistantEmotionEpisodeThreshold') || 0),
      assistant_emotion_pattern_threshold: Number(value('assistantEmotionPatternThreshold') || 1),
      proactive_idle_enabled: checked('assistantProactiveIdleEnabled'),
      proactive_idle_min_silence_seconds: Number(value('assistantProactiveIdleMinSilence') || 30),
      proactive_idle_target_silence_seconds: Number(value('assistantProactiveIdleTargetSilence') || 60),
      proactive_idle_cooldown_seconds: Number(value('assistantProactiveIdleCooldown') || 180),
      proactive_idle_max_attempts_per_topic: Number(value('assistantProactiveIdleMaxAttempts') || 2)
    };
    setText('assistantSettingsStatus', 'Saving settings...');
    try {
      var result = await jsonRequest('/api/assistant/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      setText('assistantSettingsStatus', result.detail || 'Assistant settings saved.');
      if (window.loadStatus) window.loadStatus();
    } catch (error) {
      setText('assistantSettingsStatus', 'Save failed: ' + String(error));
    }
  }

  function readTtsProfileValues() {
    var raw = value('ttsEditorValues').trim();
    if (!raw) return {};
    var parsed = JSON.parse(raw);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('Values JSON must be an object.');
    }
    return parsed;
  }

  async function saveTtsProfile() {
    setText('ttsProfileEditorStatus', 'Saving profile...');
    try {
      var providerSelect = element('ttsProviderSelect');
      var payload = {
        id: value('ttsEditorProfileId').trim(),
        label: value('ttsEditorLabel').trim(),
        description: value('ttsEditorDescription').trim(),
        provider: providerSelect ? providerSelect.value : 'qwen-tts',
        values: readTtsProfileValues()
      };
      var result = await jsonRequest('/api/providers/tts/profile/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      setText('ttsProfileEditorStatus', result.detail || 'TTS profile saved.');
      if (window.loadStatus) window.loadStatus();
    } catch (error) {
      setText('ttsProfileEditorStatus', 'Save failed: ' + String(error));
    }
  }

  function duplicateTtsProfile() {
    var profileId = element('ttsEditorProfileId');
    var label = element('ttsEditorLabel');
    if (profileId) profileId.value = (profileId.value || 'voice') + '_copy';
    if (label) label.value = (label.value || 'Voice') + ' Copy';
    setText('ttsProfileEditorStatus', 'Update the copied profile id or values, then save.');
  }

  function onTtsSynthesizeFileChange() {
    var input = element('ttsSynthesizeFileInput');
    var button = element('ttsSynthesizeButton');
    if (button) button.disabled = !(input && input.files && input.files.length);
    setText('ttsSynthesizeNote', input && input.files && input.files.length ? input.files[0].name : '');
  }

  async function ttsSynthesizeFromFile() {
    var input = element('ttsSynthesizeFileInput');
    if (!input || !input.files || !input.files.length) return;
    var form = new FormData();
    form.append('text_file', input.files[0]);
    setText('ttsSynthesizeNote', 'Synthesizing...');
    try {
      var result = await jsonRequest('/api/providers/tts/synthesize', { method: 'POST', body: form });
      setText('ttsSynthesizeNote', result.filename || result.detail || 'Synthesis completed.');
      if (window.loadStatus) window.loadStatus();
    } catch (error) {
      setText('ttsSynthesizeNote', 'Synthesis failed: ' + String(error));
    }
  }

  function ttsAudio() {
    return element('ttsAudioEngine');
  }

  function ttsPlayerLoadLatest() {
    var status = window.gammaDashboardStatus || {};
    var artifacts = status.recent_artifacts || [];
    if (!artifacts.length) {
      setText('ttsPlayerError', 'No recent audio artifact is available.');
      return;
    }
    ttsPlayerLoad(artifacts[0].name);
  }

  function ttsPlayerLoad(filename) {
    var audio = ttsAudio();
    if (!audio) return;
    audio.src = '/api/audio/' + encodeURIComponent(filename);
    setText('ttsPlayerTrack', filename);
    var error = element('ttsPlayerError');
    if (error) error.style.display = 'none';
    element('ttsPlayerPlayBtn').disabled = false;
    element('ttsPlayerSeek').disabled = false;
    var download = element('ttsPlayerDownload');
    if (download) {
      download.href = audio.src;
      download.download = filename;
      download.style.display = '';
    }
  }

  async function ttsArtifactDelete(filename) {
    if (!window.confirm('Delete generated audio file ' + filename + '?')) return;
    try {
      await jsonRequest('/api/audio/' + encodeURIComponent(filename), { method: 'DELETE' });
      if (window.loadStatus) window.loadStatus();
    } catch (error) {
      var target = element('ttsPlayerError');
      if (target) target.style.display = '';
      setText('ttsPlayerError', 'Delete failed: ' + String(error));
    }
  }

  function ttsPlayerTogglePlay() {
    var audio = ttsAudio();
    if (!audio || !audio.src) return;
    if (audio.paused) audio.play();
    else audio.pause();
  }

  function ttsPlayerSeekTo() {
    var audio = ttsAudio();
    var seek = element('ttsPlayerSeek');
    if (audio && seek && isFinite(audio.duration)) audio.currentTime = audio.duration * Number(seek.value || 0) / 100;
  }

  function ttsPlayerSetVolume() {
    var audio = ttsAudio();
    var slider = element('ttsPlayerVol');
    if (audio && slider) audio.volume = Number(slider.value || 0);
    setText('ttsPlayerVolValue', Math.round(Number(slider && slider.value || 0) * 100) + '%');
  }

  function ttsPlayerSetLoop() {
    var audio = ttsAudio();
    if (audio) audio.loop = checked('ttsPlayerLoop');
  }

  function ttsPlayerClear() {
    var audio = ttsAudio();
    if (audio) {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
    setText('ttsPlayerTrack', 'No file loaded');
  }

  window.toggleViewMode = toggleViewMode;
  window.saveAssistantSettings = saveAssistantSettings;
  window.saveTtsProfile = saveTtsProfile;
  window.duplicateTtsProfile = duplicateTtsProfile;
  window.onTtsSynthesizeFileChange = onTtsSynthesizeFileChange;
  window.ttsSynthesizeFromFile = ttsSynthesizeFromFile;
  window.ttsPlayerLoadLatest = ttsPlayerLoadLatest;
  window.ttsPlayerLoad = ttsPlayerLoad;
  window.ttsArtifactDelete = ttsArtifactDelete;
  window.ttsPlayerTogglePlay = ttsPlayerTogglePlay;
  window.ttsPlayerSeekTo = ttsPlayerSeekTo;
  window.ttsPlayerSetVolume = ttsPlayerSetVolume;
  window.ttsPlayerSetLoop = ttsPlayerSetLoop;
  window.ttsPlayerClear = ttsPlayerClear;
})();
