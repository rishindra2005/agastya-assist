/**
 * app.js — Main Web UI logic for Agastya Assist (अगस्त्य सहायक).
 *
 * ES Module with no build step, no frameworks, no CDN dependencies.
 * Complies strictly with INTERFACES.md WS protocol, REST API, and event types.
 */

import { AudioRecorder } from './recorder.js';

/* ==========================================================================
   State & Constants
   ========================================================================== */
const urlParams = new URLSearchParams(window.location.search);
const userId = urlParams.get('user_id') || 'default';

let ws = null;
let reconnectTimer = null;
let reconnectDelay = 1000;
const MIN_RECONNECT_DELAY = 1000;
const MAX_RECONNECT_DELAY = 10000;

let currentLanguage = ''; // '' represents Auto
let speakerEnabled = true;
let isRecording = false;
let recordTimerInterval = null;
let recordStartTime = 0;
let isTouchInteraction = false;
let currentHighlightedField = null;
let activityCount = 0;

/* ==========================================================================
   Audio Playback Queue (Sequential TTS playback)
   ========================================================================== */
class AudioPlayerQueue {
  constructor(onPlaybackStateChange) {
    this.queue = [];
    this.isPlaying = false;
    this.currentAudio = null;
    this.onPlaybackStateChange = onPlaybackStateChange;
  }

  enqueue(audioB64, mime = 'audio/wav') {
    if (!speakerEnabled) {
      return;
    }
    this.queue.push({ audioB64, mime });
    this.playNext();
  }

  playNext() {
    if (this.isPlaying || this.queue.length === 0) {
      if (!this.isPlaying && typeof this.onPlaybackStateChange === 'function') {
        this.onPlaybackStateChange(false);
      }
      return;
    }

    const item = this.queue.shift();
    this.isPlaying = true;
    if (typeof this.onPlaybackStateChange === 'function') {
      this.onPlaybackStateChange(true);
    }

    try {
      const dataUri = `data:${item.mime};base64,${item.audioB64}`;
      const audio = new Audio(dataUri);
      this.currentAudio = audio;

      const onFinish = () => {
        this.isPlaying = false;
        this.currentAudio = null;
        this.playNext();
      };

      audio.onended = onFinish;
      audio.onerror = (err) => {
        console.warn('Audio playback encountered an error:', err);
        onFinish();
      };

      audio.play().catch((err) => {
        console.warn('Audio play() was rejected or blocked by browser autoplay policy:', err);
        onFinish();
      });
    } catch (err) {
      console.warn('Failed to initialize Audio object:', err);
      this.isPlaying = false;
      this.playNext();
    }
  }

  stopAll() {
    this.queue = [];
    if (this.currentAudio) {
      try {
        this.currentAudio.pause();
        this.currentAudio.currentTime = 0;
      } catch {
        // ignore
      }
      this.currentAudio = null;
    }
    this.isPlaying = false;
    if (typeof this.onPlaybackStateChange === 'function') {
      this.onPlaybackStateChange(false);
    }
  }
}

const audioQueue = new AudioPlayerQueue((isPlaying) => {
  if (isPlaying) {
    updateStatus('speaking', 'बोल रहे हैं... (Speaking...)');
  } else if (!isRecording) {
    updateStatus('idle', 'तैयार (Ready)');
  }
});

/* ==========================================================================
   Microphone Recorder Instance
   ========================================================================== */
const recorder = new AudioRecorder({
  targetSampleRate: 16000,
  maxDurationSeconds: 60,
  onTimeout: () => {
    stopVoiceRecording();
  },
  onStateChange: (state) => {
    if (state === 'processing') {
      updateStatus('thinking', 'प्रसंस्करण हो रहा है... (Processing audio...)');
    }
  },
});

/* ==========================================================================
   DOM Elements
   ========================================================================== */
const connDot = document.getElementById('conn-dot');
const statusText = document.getElementById('status-text');
const langSelect = document.getElementById('lang-select');
const ttsToggle = document.getElementById('tts-toggle');
const ttsIcon = document.getElementById('tts-icon');

const amberBanner = document.getElementById('amber-banner');
const amberBannerReason = document.getElementById('amber-banner-reason');
const amberBannerClose = document.getElementById('amber-banner-close');
const toastContainer = document.getElementById('toast-container');

const profileFieldsList = document.getElementById('profile-fields-list');
const profileRefreshBtn = document.getElementById('profile-refresh-btn');

const chatMessages = document.getElementById('chat-messages');
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const micBtn = document.getElementById('mic-btn');
const sendBtn = document.getElementById('send-btn');
const recordingStatus = document.getElementById('recording-status');
const recordingTimer = document.getElementById('recording-timer');

const browserUrlText = document.getElementById('browser-url-text');
const browserUrlLink = document.getElementById('browser-url-link');
const browserScreenshot = document.getElementById('browser-screenshot');
const browserPlaceholder = document.getElementById('browser-placeholder');
const activityLog = document.getElementById('activity-log');
const activityCountEl = document.getElementById('activity-count');

/* ==========================================================================
   Helpers
   ========================================================================== */
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function updateConnectionStatus(state) {
  connDot.className = 'conn-dot';
  if (state === 'connected') {
    connDot.classList.add('connected');
    connDot.title = 'जुड़े हुए हैं (Connected)';
    updateStatus('idle', 'तैयार (Connected)');
  } else if (state === 'connecting') {
    connDot.classList.add('connecting');
    connDot.title = 'जुड़ रहे हैं... (Connecting...)';
    updateStatus('connecting', 'जुड़ रहे हैं... (Connecting...)');
  } else {
    connDot.title = 'संपर्क टूटा (Disconnected)';
    updateStatus('disconnected', 'संपर्क टूटा (Disconnected)');
  }
}

function updateStatus(type, label) {
  if (label) {
    statusText.textContent = label;
  } else {
    switch (type) {
      case 'thinking':
        statusText.textContent = 'सोच रहे हैं... (Thinking...)';
        break;
      case 'listening':
        statusText.textContent = 'सुन रहे हैं... (Listening...)';
        break;
      case 'speaking':
        statusText.textContent = 'बोल रहे हैं... (Speaking...)';
        break;
      case 'idle':
        statusText.textContent = 'तैयार (Ready)';
        break;
      default:
        statusText.textContent = type;
    }
  }
}

function showToast(message) {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.innerHTML = `
    <span class="toast-icon">⚠️</span>
    <span class="toast-message">${escapeHtml(message)}</span>
    <button type="button" class="toast-close" aria-label="Dismiss error">✕</button>
  `;
  const closeBtn = toast.querySelector('.toast-close');
  closeBtn.addEventListener('click', () => toast.remove());

  toastContainer.appendChild(toast);
  setTimeout(() => {
    if (toast.isConnected) {
      toast.remove();
    }
  }, 7000);
}

function showAmberBanner(reason) {
  const text = reason || 'कार्रवाई मानवीय पुष्टि की प्रतीक्षा कर रही है (Action paused for human verification).';
  amberBannerReason.textContent = text;
  amberBanner.classList.remove('hidden');
}

function hideAmberBanner() {
  amberBanner.classList.add('hidden');
}

/* ==========================================================================
   Chat Rendering
   ========================================================================== */
function appendUserMessage(text) {
  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble bubble-user';
  bubble.innerHTML = `
    <div class="bubble-content">
      <p>${escapeHtml(text)}</p>
    </div>
  `;
  chatMessages.appendChild(bubble);
  scrollChatToBottom();
}

function appendAssistantMessage(text) {
  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble bubble-assistant';
  bubble.innerHTML = `
    <div class="bubble-avatar" aria-hidden="true">🏛️</div>
    <div class="bubble-content">
      <p>${escapeHtml(text).replace(/\n/g, '<br/>')}</p>
    </div>
  `;
  chatMessages.appendChild(bubble);
  scrollChatToBottom();
}

function appendAskUserCard(data) {
  const cardId = `ask-${data.question_id || Date.now()}`;
  const card = document.createElement('div');
  card.className = 'ask-user-card';
  card.id = cardId;
  card.innerHTML = `
    <div class="ask-header">
      <span>❓ प्रश्न / Question:</span>
    </div>
    <div class="ask-question-text">${escapeHtml(data.question)}</div>
    <form class="ask-form" id="form-${cardId}">
      <input
        type="text"
        class="ask-input"
        placeholder="उत्तर यहाँ लिखें / Type your answer here..."
        required
        autocomplete="off"
      />
      <button type="submit" class="ask-submit-btn">उत्तर दें (Submit)</button>
    </form>
  `;

  chatMessages.appendChild(card);
  scrollChatToBottom();

  const form = card.querySelector(`#form-${cardId}`);
  const input = form.querySelector('.ask-input');

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const answer = input.value.trim();
    if (!answer) return;

    // Send answer message over WebSocket
    sendWsMessage({
      type: 'answer',
      question_id: data.question_id,
      answer,
    });

    // Replace form with answered indicator
    form.replaceWith(
      Object.assign(document.createElement('div'), {
        className: 'ask-answered-status',
        textContent: `✓ उत्तर दिया (Answered): ${answer}`,
      })
    );

    // Clear field highlight if this field was highlighted
    if (data.field && currentHighlightedField === data.field) {
      clearFieldHighlight();
    }
  });

  // Highlight corresponding field in profile if given
  if (data.field) {
    highlightProfileField(data.field);
  }
}

function scrollChatToBottom() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

/* ==========================================================================
   Activity Log (Tool Calls)
   ========================================================================== */
function logToolCall(data) {
  // Strip mcp__agastya__ prefix
  const rawName = data.name || data.tool || 'tool_call';
  const cleanName = rawName.replace(/^mcp__agastya__/, '');

  // Format short arguments without revealing sensitive get_profile_value values
  let argsStr = '';
  const input = data.input || {};

  if (cleanName === 'get_profile_value') {
    // SECURITY RULE: Never show values of get_profile_value results or requests
    argsStr = `field: ${input.field || '(unspecified)'}`;
  } else if (cleanName === 'save_profile_field') {
    argsStr = `field: ${input.field || ''}, value: ••••••`;
  } else if (cleanName === 'browser_goto') {
    argsStr = `url: "${input.url || ''}"`;
  } else if (cleanName === 'browser_fill') {
    argsStr = `ref: "${input.ref || ''}", value: ••••••`;
  } else if (cleanName === 'browser_click') {
    argsStr = `ref: "${input.ref || ''}"`;
  } else if (cleanName === 'browser_select') {
    argsStr = `ref: "${input.ref || ''}", option: "${input.option || ''}"`;
  } else if (cleanName === 'ask_user') {
    argsStr = `question: "${(input.question || '').slice(0, 40)}..."`;
  } else if (cleanName === 'handoff_to_user') {
    argsStr = `reason: "${input.reason || ''}"`;
  } else {
    try {
      const keys = Object.keys(input);
      if (keys.length === 0) {
        argsStr = '';
      } else {
        const parts = keys.map((k) => {
          const val = String(input[k]);
          return `${k}: ${val.length > 30 ? val.slice(0, 30) + '...' : val}`;
        });
        argsStr = parts.join(', ');
      }
    } catch {
      argsStr = '';
    }
  }

  // Remove empty placeholder if needed
  const emptyPlaceholder = activityLog.querySelector('.empty-log');
  if (emptyPlaceholder) {
    emptyPlaceholder.remove();
  }

  activityCount += 1;
  activityCountEl.textContent = String(activityCount);

  const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const item = document.createElement('div');
  item.className = 'activity-item';
  item.innerHTML = `
    <div class="activity-header">
      <span class="activity-tool-name">${escapeHtml(cleanName)}</span>
      <span class="activity-time">${timeStr}</span>
    </div>
    ${argsStr ? `<div class="activity-args">${escapeHtml(argsStr)}</div>` : ''}
  `;

  activityLog.prepend(item);
}

/* ==========================================================================
   Profile Management (REST: GET / PUT /api/profile)
   ========================================================================== */
async function loadProfile() {
  try {
    const res = await fetch(`/api/profile?user_id=${encodeURIComponent(userId)}`);
    if (!res.ok) {
      throw new Error(`Profile fetch failed: ${res.status}`);
    }
    const profile = await res.json();
    renderProfileFields(profile);
  } catch (err) {
    console.error('Error loading profile:', err);
    profileFieldsList.innerHTML = `
      <div class="loading-state" style="color: #dc2626;">
        प्रोफ़ाइल लोड करने में त्रुटि (Failed to load profile).
      </div>
    `;
  }
}

function renderProfileFields(fieldsMap) {
  profileFieldsList.innerHTML = '';
  const entries = Object.entries(fieldsMap);

  if (entries.length === 0) {
    profileFieldsList.innerHTML = `<div class="loading-state">कोई फ़ील्ड उपलब्ध नहीं है (No fields).</div>`;
    return;
  }

  entries.forEach(([fieldKey, info]) => {
    const fieldCard = document.createElement('div');
    fieldCard.className = 'profile-field-item';
    fieldCard.id = `profile-field-${fieldKey}`;
    fieldCard.dataset.field = fieldKey;

    const labelHi = info.label_hi || fieldKey;
    const labelEn = info.label_en || fieldKey;
    const value = info.value;
    const isUnset = value === null || value === undefined || value === '';

    fieldCard.innerHTML = `
      <div class="field-header">
        <div class="field-labels">
          <span class="field-label-hi">${escapeHtml(labelHi)}</span>
          <span class="field-label-en">${escapeHtml(labelEn)}</span>
        </div>
        <button type="button" class="btn-edit" aria-label="Edit ${escapeHtml(labelEn)}">✏️ बदलें (Edit)</button>
      </div>

      <div class="field-view-mode">
        <span class="field-value ${isUnset ? 'unset' : ''}">
          ${isUnset ? 'सेट नहीं है (Not set)' : escapeHtml(value)}
        </span>
      </div>

      <form class="field-edit-form hidden" autocomplete="off">
        <input
          type="text"
          class="field-input"
          value="${escapeHtml(isUnset ? '' : value)}"
          placeholder="${escapeHtml(labelHi)} लिखें..."
        />
        <div class="field-edit-actions">
          <button type="submit" class="btn-save">सहेजें (Save)</button>
          <button type="button" class="btn-cancel">रद्द करें (Cancel)</button>
        </div>
        <div class="field-error-msg hidden" role="alert"></div>
      </form>
    `;

    // Inline edit handlers
    const editBtn = fieldCard.querySelector('.btn-edit');
    const viewMode = fieldCard.querySelector('.field-view-mode');
    const editForm = fieldCard.querySelector('.field-edit-form');
    const cancelBtn = fieldCard.querySelector('.btn-cancel');
    const input = fieldCard.querySelector('.field-input');
    const errorBox = fieldCard.querySelector('.field-error-msg');

    editBtn.addEventListener('click', () => {
      viewMode.classList.add('hidden');
      editBtn.classList.add('hidden');
      editForm.classList.remove('hidden');
      errorBox.classList.add('hidden');
      input.focus();
    });

    cancelBtn.addEventListener('click', () => {
      editForm.classList.add('hidden');
      viewMode.classList.remove('hidden');
      editBtn.classList.remove('hidden');
      errorBox.classList.add('hidden');
    });

    editForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      errorBox.classList.add('hidden');
      const newValue = input.value.trim();

      try {
        const res = await fetch(`/api/profile/${encodeURIComponent(fieldKey)}?user_id=${encodeURIComponent(userId)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ value: newValue }),
        });

        if (res.ok) {
          // Refresh profile on successful update
          await loadProfile();
        } else if (res.status === 422) {
          // Validation error: display message inline
          const errData = await res.json().catch(() => ({}));
          let errorMsg = 'अमान्य मान (Invalid value format)';
          if (typeof errData.detail === 'string') {
            errorMsg = errData.detail;
          } else if (Array.isArray(errData.detail) && errData.detail.length > 0) {
            errorMsg = errData.detail[0].msg || errorMsg;
          }
          errorBox.textContent = errorMsg;
          errorBox.classList.remove('hidden');
        } else {
          const errData = await res.json().catch(() => ({}));
          errorBox.textContent = errData.detail || `Error ${res.status}`;
          errorBox.classList.remove('hidden');
        }
      } catch (err) {
        console.error('Failed to save profile field:', err);
        errorBox.textContent = 'सहेजने में विफल (Network error saving field)';
        errorBox.classList.remove('hidden');
      }
    });

    profileFieldsList.appendChild(fieldCard);
  });

  // Re-apply highlight if a field was previously highlighted
  if (currentHighlightedField) {
    highlightProfileField(currentHighlightedField);
  }
}

function highlightProfileField(fieldKey) {
  clearFieldHighlight();
  currentHighlightedField = fieldKey;
  const target = document.getElementById(`profile-field-${fieldKey}`);
  if (target) {
    target.classList.add('highlighted-field');
    const header = target.querySelector('.field-labels');
    if (header && !target.querySelector('.field-badge-asking')) {
      const badge = document.createElement('span');
      badge.className = 'field-badge-asking';
      badge.textContent = 'अगस्त्य यह जानकारी पूछ रहे हैं (Requested)';
      header.appendChild(badge);
    }
    target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

function clearFieldHighlight() {
  if (currentHighlightedField) {
    const prev = document.getElementById(`profile-field-${currentHighlightedField}`);
    if (prev) {
      prev.classList.remove('highlighted-field');
      const badge = prev.querySelector('.field-badge-asking');
      if (badge) badge.remove();
    }
    currentHighlightedField = null;
  }
}

/* ==========================================================================
   Languages (REST: GET /api/languages)
   ========================================================================== */
async function loadLanguages() {
  try {
    const res = await fetch('/api/languages');
    if (!res.ok) throw new Error(`Languages fetch status: ${res.status}`);
    const langs = await res.json();

    // Clear existing options except Auto
    langSelect.innerHTML = '<option value="">भाषा: स्वतः (Auto)</option>';
    Object.entries(langs).forEach(([code, name]) => {
      const opt = document.createElement('option');
      opt.value = code;
      opt.textContent = `${name} (${code})`;
      langSelect.appendChild(opt);
    });
  } catch (err) {
    console.warn('Could not load languages, using standard defaults:', err);
    const defaults = {
      'hi-IN': 'Hindi',
      'en-IN': 'English',
      'bn-IN': 'Bengali',
      'ta-IN': 'Tamil',
      'te-IN': 'Telugu',
      'kn-IN': 'Kannada',
      'ml-IN': 'Malayalam',
      'mr-IN': 'Marathi',
      'gu-IN': 'Gujarati',
      'pa-IN': 'Punjabi',
      'od-IN': 'Odia',
    };
    langSelect.innerHTML = '<option value="">भाषा: स्वतः (Auto)</option>';
    Object.entries(defaults).forEach(([code, name]) => {
      const opt = document.createElement('option');
      opt.value = code;
      opt.textContent = `${name} (${code})`;
      langSelect.appendChild(opt);
    });
  }
}

/* ==========================================================================
   WebSocket Client & Auto-reconnect
   ========================================================================== */
function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  updateConnectionStatus('connecting');

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host;
  const wsUrl = `${protocol}//${host}/ws?user_id=${encodeURIComponent(userId)}`;

  try {
    ws = new WebSocket(wsUrl);
  } catch (err) {
    console.warn('Failed to construct WebSocket:', err);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    updateConnectionStatus('connected');
    reconnectDelay = MIN_RECONNECT_DELAY;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    // Send initial configuration
    sendWsMessage({ type: 'tts', enabled: speakerEnabled });
    if (currentLanguage) {
      sendWsMessage({ type: 'set_language', language_code: currentLanguage });
    }
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleAgentEvent(msg);
    } catch (err) {
      console.error('Error handling WebSocket message payload:', err, event.data);
    }
  };

  ws.onclose = () => {
    updateConnectionStatus('disconnected');
    scheduleReconnect();
  };

  ws.onerror = (err) => {
    console.warn('WebSocket error:', err);
    // onclose will handle reconnect
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectDelay = Math.min(MAX_RECONNECT_DELAY, Math.round(reconnectDelay * 1.5));
    connectWebSocket();
  }, reconnectDelay);
}

function sendWsMessage(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
    return true;
  }
  console.warn('Cannot send WS message: WebSocket is not open', obj);
  return false;
}

/* ==========================================================================
   Agent Event Dispatcher
   ========================================================================== */
function handleAgentEvent(event) {
  const type = event.type;
  const data = event.data || {};

  switch (type) {
    case 'status':
      if (data.handoff) {
        showAmberBanner(data.reason);
        updateStatus('idle', 'आपकी बारी (Your Turn)');
      } else if (data.status) {
        updateStatus(data.status);
      }
      break;

    case 'assistant_text':
      if (data.text) {
        appendAssistantMessage(data.text);
      }
      break;

    case 'transcript':
      if (data.text) {
        appendUserMessage(data.text);
      }
      break;

    case 'ask_user':
      appendAskUserCard(data);
      break;

    case 'blocked':
      showAmberBanner(data.reason || 'सुरक्षा नीति के तहत यह अंतिम कार्रवाई अवरुद्ध है (Action blocked by safety policy).');
      break;

    case 'tool_call':
      logToolCall(data);
      break;

    case 'screenshot':
      if (data.png_b64) {
        browserScreenshot.src = `data:image/png;base64,${data.png_b64}`;
        browserScreenshot.classList.remove('hidden');
        browserPlaceholder.classList.add('hidden');
      }
      if (data.url) {
        browserUrlText.textContent = data.url;
        browserUrlLink.href = data.url;
      }
      break;

    case 'profile_updated':
      loadProfile();
      break;

    case 'tts':
      if (data.audio_b64) {
        audioQueue.enqueue(data.audio_b64, data.mime || 'audio/wav');
      }
      break;

    case 'error':
      showToast(data.error || data.detail || 'एक त्रुटि उत्पन्न हुई (An error occurred).');
      updateStatus('idle', 'त्रुटि (Error)');
      break;

    case 'done':
      if (!audioQueue.isPlaying) {
        updateStatus('idle', 'तैयार (Ready)');
      }
      break;

    default:
      console.log('Unhandled AgentEvent type:', type, data);
  }
}

/* ==========================================================================
   Voice Recording Controls
   ========================================================================== */
async function startVoiceRecording() {
  if (isRecording) return;

  // Pause/stop any ongoing audio playback while recording
  audioQueue.stopAll();

  try {
    await recorder.start();
    isRecording = true;
    recordStartTime = Date.now();

    micBtn.classList.add('recording');
    recordingStatus.classList.remove('hidden');
    updateStatus('listening', 'सुन रहे हैं... (Listening...)');

    // Timer display
    recordingTimer.textContent = '00:00';
    if (recordTimerInterval) clearInterval(recordTimerInterval);
    recordTimerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - recordStartTime) / 1000);
      const mins = String(Math.floor(elapsed / 60)).padStart(2, '0');
      const secs = String(elapsed % 60).padStart(2, '0');
      recordingTimer.textContent = `${mins}:${secs}`;
    }, 500);
  } catch (err) {
    console.error('Microphone access denied or error:', err);
    showToast('माइक्रोफ़ोन अनुमति नहीं मिली (Microphone access denied or unavailable).');
    cancelVoiceRecording();
  }
}

async function stopVoiceRecording() {
  if (!isRecording) return;
  isRecording = false;

  if (recordTimerInterval) {
    clearInterval(recordTimerInterval);
    recordTimerInterval = null;
  }

  micBtn.classList.remove('recording');
  recordingStatus.classList.add('hidden');

  try {
    const audioResult = await recorder.stop();
    if (audioResult && audioResult.audio_b64) {
      updateStatus('thinking', 'सोच रहे हैं... (Processing voice...)');
      sendWsMessage({
        type: 'audio',
        audio_b64: audioResult.audio_b64,
        mime: 'audio/wav',
      });
    } else {
      updateStatus('idle', 'तैयार (Ready)');
    }
  } catch (err) {
    console.error('Failed to encode audio:', err);
    showToast('ऑडियो एन्कोडिंग में विफल (Failed to process recorded audio).');
    updateStatus('idle', 'तैयार (Ready)');
  }
}

function cancelVoiceRecording() {
  isRecording = false;
  if (recordTimerInterval) {
    clearInterval(recordTimerInterval);
    recordTimerInterval = null;
  }
  micBtn.classList.remove('recording');
  recordingStatus.classList.add('hidden');
  recorder.cancel();
  updateStatus('idle', 'तैयार (Ready)');
}

/* ==========================================================================
   UI Event Bindings
   ========================================================================== */
function initEventListeners() {
  // 1. Text input submit
  chatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;

    appendUserMessage(text);
    chatInput.value = '';

    sendWsMessage({
      type: 'text',
      text,
      language_code: currentLanguage || null,
    });

    updateStatus('thinking', 'सोच रहे हैं... (Thinking...)');
  });

  // 2. Microphone interactions: Touch hold-to-talk, Desktop click-to-toggle
  micBtn.addEventListener(
    'touchstart',
    (e) => {
      e.preventDefault();
      isTouchInteraction = true;
      startVoiceRecording();
    },
    { passive: false }
  );

  micBtn.addEventListener(
    'touchend',
    (e) => {
      e.preventDefault();
      stopVoiceRecording();
      setTimeout(() => {
        isTouchInteraction = false;
      }, 500);
    },
    { passive: false }
  );

  micBtn.addEventListener(
    'touchcancel',
    (e) => {
      e.preventDefault();
      cancelVoiceRecording();
      setTimeout(() => {
        isTouchInteraction = false;
      }, 500);
    },
    { passive: false }
  );

  micBtn.addEventListener('click', (e) => {
    if (isTouchInteraction) return; // Prevent ghost clicks on mobile touch devices
    if (isRecording) {
      stopVoiceRecording();
    } else {
      startVoiceRecording();
    }
  });

  // 3. Quick-start chips
  const chips = document.querySelectorAll('.chip');
  chips.forEach((chip) => {
    chip.addEventListener('click', () => {
      const query = chip.dataset.query || chip.textContent.trim();
      if (!query) return;

      appendUserMessage(query);
      sendWsMessage({
        type: 'text',
        text: query,
        language_code: currentLanguage || null,
      });
      updateStatus('thinking', 'सोच रहे हैं... (Thinking...)');
    });
  });

  // 4. Language selector
  langSelect.addEventListener('change', () => {
    currentLanguage = langSelect.value;
    if (currentLanguage) {
      sendWsMessage({
        type: 'set_language',
        language_code: currentLanguage,
      });
    }
  });

  // 5. TTS Speaker Toggle
  ttsToggle.addEventListener('click', () => {
    speakerEnabled = !speakerEnabled;
    ttsToggle.setAttribute('aria-pressed', String(speakerEnabled));
    if (speakerEnabled) {
      ttsIcon.textContent = '🔊';
      ttsToggle.classList.remove('disabled');
      ttsToggle.title = 'ध्वनि चालू (Voice Enabled)';
    } else {
      ttsIcon.textContent = '🔇';
      ttsToggle.classList.add('disabled');
      ttsToggle.title = 'ध्वनि बंद (Voice Muted)';
      audioQueue.stopAll();
    }
    sendWsMessage({
      type: 'tts',
      enabled: speakerEnabled,
    });
  });

  // 6. Amber banner close button
  amberBannerClose.addEventListener('click', hideAmberBanner);

  // 7. Profile refresh button
  profileRefreshBtn.addEventListener('click', loadProfile);

  // 8. Mobile tab navigation
  const tabBtns = document.querySelectorAll('.tab-btn');
  const panels = document.querySelectorAll('.panel');

  tabBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.target;
      tabBtns.forEach((b) => b.classList.remove('active'));
      panels.forEach((p) => p.classList.remove('active'));

      btn.classList.add('active');
      const targetPanel = document.getElementById(targetId);
      if (targetPanel) {
        targetPanel.classList.add('active');
      }
    });
  });
}

/* ==========================================================================
   Initialization
   ========================================================================== */
window.addEventListener('DOMContentLoaded', async () => {
  initEventListeners();
  await loadLanguages();
  await loadProfile();
  connectWebSocket();
});
