/**
 * app.js — Main Web UI logic for Agastya Assist (अगस्त्य सहायक).
 *
 * ES Module with no build step, no frameworks, no CDN dependencies.
 * Complies strictly with INTERFACES.md WS protocol, REST API, and event types.
 */

import { AudioRecorder } from './recorder.js';
import { initLiveBrowser } from './live.js';

/* ==========================================================================
   State & Constants
   ========================================================================== */
const urlParams = new URLSearchParams(window.location.search);
const userId = urlParams.get('user_id') || 'default';

let ws = null;
let liveBrowser = null;
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
const currentHighlightedFields = new Set();
let profileMetadata = {};
let activityCount = 0;
let isReady = false;

/* Standard profile field labels fallback */
const FALLBACK_FIELD_LABELS = {
  pan: { label_hi: 'पैन नंबर', label_en: 'PAN' },
  aadhaar: { label_hi: 'आधार संख्या', label_en: 'Aadhaar' },
  name: { label_hi: 'पूरा नाम', label_en: 'Full Name' },
  father_name: { label_hi: 'पिता का नाम', label_en: "Father's Name" },
  dob: { label_hi: 'जन्म तिथि', label_en: 'Date of Birth' },
  mobile: { label_hi: 'मोबाइल नंबर', label_en: 'Mobile Number' },
  email: { label_hi: 'ईमेल पता', label_en: 'Email Address' },
  address: { label_hi: 'पता', label_en: 'Address' },
  pincode: { label_hi: 'पिन कोड', label_en: 'PIN Code' },
  state: { label_hi: 'राज्य', label_en: 'State' },
  gender: { label_hi: 'लिंग', label_en: 'Gender' },
  epic: { label_hi: 'मतदाता पहचान पत्र (EPIC)', label_en: 'Voter ID (EPIC)' },
  bank_account: { label_hi: 'बैंक खाता संख्या', label_en: 'Bank Account Number' },
  ifsc: { label_hi: 'IFSC कोड', label_en: 'IFSC Code' },
  din: { label_hi: 'निदेशक पहचान संख्या (DIN)', label_en: 'Director Identification Number (DIN)' },
};

function getFieldLabels(fieldKey) {
  if (profileMetadata && profileMetadata[fieldKey]) {
    const meta = profileMetadata[fieldKey];
    return {
      label_hi: meta.label_hi || fieldKey,
      label_en: meta.label_en || fieldKey,
    };
  }
  if (FALLBACK_FIELD_LABELS[fieldKey]) {
    return FALLBACK_FIELD_LABELS[fieldKey];
  }
  return {
    label_hi: fieldKey,
    label_en: fieldKey,
  };
}

/* ==========================================================================
   Audio Playback Queue (Sequential TTS playback with gapless preloading)
   ========================================================================== */
class AudioPlayerQueue {
  constructor(options = {}) {
    this.queue = [];
    this.isPlaying = false;
    this.currentAudio = null;
    this.onStateChange = options.onStateChange || null;
  }

  get isSpeaking() {
    return this.isPlaying || this.queue.length > 0;
  }

  _notifyState() {
    if (typeof this.onStateChange === 'function') {
      this.onStateChange(this.isSpeaking);
    }
  }

  enqueue(audioB64, mime = 'audio/wav', meta = {}) {
    if (!speakerEnabled) {
      return;
    }

    const item = {
      audioB64,
      mime,
      seq: meta.seq,
      final: meta.final,
      text: meta.text,
      audioElement: null,
    };

    // Preload Audio element immediately so next sentence starts with zero gap
    try {
      const dataUri = `data:${item.mime};base64,${item.audioB64}`;
      const audio = new Audio(dataUri);
      audio.preload = 'auto';
      audio.load();
      item.audioElement = audio;
    } catch (e) {
      console.warn('Failed to preload Audio element:', e);
    }

    this.queue.push(item);
    this._notifyState();

    if (!this.isPlaying) {
      this.playNext();
    }
  }

  playNext() {
    if (this.isPlaying) {
      return;
    }

    if (this.queue.length === 0) {
      this.isPlaying = false;
      this.currentAudio = null;
      this._notifyState();
      return;
    }

    const item = this.queue.shift();
    this.isPlaying = true;
    this._notifyState();

    let audio = item.audioElement;
    if (!audio) {
      try {
        const dataUri = `data:${item.mime};base64,${item.audioB64}`;
        audio = new Audio(dataUri);
      } catch (err) {
        console.warn('Failed to initialize Audio object:', err);
        this.isPlaying = false;
        this.playNext();
        return;
      }
    }

    this.currentAudio = audio;

    let endedHandled = false;
    const onFinish = () => {
      if (endedHandled) return;
      endedHandled = true;
      this.isPlaying = false;
      this.currentAudio = null;
      this.playNext();
    };

    audio.onended = onFinish;
    audio.onerror = (err) => {
      console.warn('Audio playback error:', err);
      onFinish();
    };

    audio.play().catch((err) => {
      console.warn('Audio play() was rejected or blocked by browser autoplay policy:', err);
      onFinish();
    });
  }

  stopAll() {
    this.queue = [];
    if (this.currentAudio) {
      try {
        this.currentAudio.pause();
        this.currentAudio.currentTime = 0;
        this.currentAudio.src = '';
      } catch {
        // ignore
      }
      this.currentAudio = null;
    }
    this.isPlaying = false;
    this._notifyState();
  }
}

const audioQueue = new AudioPlayerQueue({
  onStateChange: (isSpeaking) => {
    if (isSpeaking) {
      updateStatus('speaking', 'बोल रहा है / Speaking');
      if (stopSpeakingBtn) {
        stopSpeakingBtn.classList.remove('hidden');
      }
      if (ttsToggle) {
        ttsToggle.classList.add('is-speaking');
        ttsToggle.title = 'बोलना बंद करें / Stop speaking';
      }
    } else {
      if (stopSpeakingBtn) {
        stopSpeakingBtn.classList.add('hidden');
      }
      if (ttsToggle) {
        ttsToggle.classList.remove('is-speaking');
        ttsToggle.title = speakerEnabled
          ? 'ध्वनि चालू/बंद (Toggle Voice Output)'
          : 'ध्वनि चालू/बंद (Toggle Voice Output)';
      }
      if (!isRecording) {
        if (isReady) {
          updateStatus('idle', 'तैयार (Ready)');
        } else if (ws && ws.readyState === WebSocket.OPEN) {
          updateStatus('warming_up', 'तैयार हो रहा है… / Warming up…');
        }
      }
    }
  },
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
const stopSpeakingBtn = document.getElementById('stop-speaking-btn');

const amberBanner = document.getElementById('amber-banner');
const amberBannerReason = document.getElementById('amber-banner-reason');
const amberBannerClose = document.getElementById('amber-banner-close');
const bannerViewBrowserBtn = document.getElementById('banner-view-browser-btn');
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

const attachBtn = document.getElementById('attach-btn');
const docFileInput = document.getElementById('doc-file-input');
const chatDropOverlay = document.getElementById('chat-drop-overlay');
const chatPanel = document.getElementById('chat-panel');
const documentsList = document.getElementById('documents-list');
const documentsCount = document.getElementById('documents-count');
const docsRefreshBtn = document.getElementById('docs-refresh-btn');
const pwaInstallBtn = document.getElementById('pwa-install-btn');

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
    if (!isReady) {
      updateStatus('warming_up', 'तैयार हो रहा है… / Warming up…');
    } else {
      updateStatus('idle', 'तैयार (Ready)');
    }
  } else if (state === 'connecting') {
    connDot.classList.add('connecting');
    connDot.title = 'जुड़ रहे हैं... (Connecting...)';
    updateStatus('warming_up', 'तैयार हो रहा है… / Warming up…');
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
        statusText.textContent = 'बोल रहा है / Speaking';
        break;
      case 'warming_up':
        statusText.textContent = 'तैयार हो रहा है… / Warming up…';
        break;
      case 'ready':
        statusText.textContent = 'तैयार (Ready)';
        break;
      case 'idle':
        statusText.textContent = isReady ? 'तैयार (Ready)' : 'तैयार हो रहा है… / Warming up…';
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

function appendDocumentChip(doc) {
  const summary = doc.summary || doc.filename || 'दस्तावेज़ (Document)';
  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble bubble-user doc-bubble';
  bubble.innerHTML = `
    <div class="bubble-content">
      <div class="doc-chip-wrap">
        <span class="doc-chip">📄 ${escapeHtml(summary)}</span>
      </div>
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

  // Extract all requested fields (support Batch D fields list or legacy single field)
  let reqFields = [];
  if (Array.isArray(data.fields) && data.fields.length > 0) {
    reqFields = data.fields;
  } else if (data.field) {
    reqFields = [data.field];
  }

  // Generate chips for requested field labels (Hindi + English)
  let chipsHtml = '';
  if (reqFields.length > 0) {
    const chips = reqFields.map((f) => {
      const labels = getFieldLabels(f);
      return `<span class="ask-field-chip">${escapeHtml(labels.label_hi)} (${escapeHtml(labels.label_en)})</span>`;
    });
    chipsHtml = `<div class="ask-field-chips" aria-label="पूछी गई जानकारी / Requested details">${chips.join('')}</div>`;
  }

  card.innerHTML = `
    <div class="ask-header">
      <span>❓ प्रश्न / Question:</span>
    </div>
    <div class="ask-question-text">${escapeHtml(data.question)}</div>
    ${chipsHtml}
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

    // Clear highlights for the fields requested by this question
    clearFieldHighlights(reqFields);
  });

  // Highlight ALL requested fields in the profile panel
  if (reqFields.length > 0) {
    highlightProfileFields(reqFields);
  }
}

function scrollChatToBottom() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

/* ==========================================================================
   Activity Log (Tool Calls)
   ========================================================================== */
function logToolCall(data) {
  // Strip mcp__agastya__ and mcp__playwright__ prefixes; flag playwright tools with DOM tag
  const rawName = data.name || data.tool || 'tool_call';
  const isPlaywright = rawName.startsWith('mcp__playwright__');
  const cleanName = rawName.replace(/^(mcp__agastya__|mcp__playwright__)/, '');

  let argsStr = '';
  const input = data.input || data.args || data.arguments || {};

  if (cleanName === 'browser_fill_many') {
    // SECURITY: never show values — they may be sensitive!
    const items = Array.isArray(input.items)
      ? input.items
      : Array.isArray(input)
        ? input
        : [];
    const refs = items
      .map((item) => (item && item.ref != null ? String(item.ref).trim() : ''))
      .filter(Boolean);
    argsStr = `${items.length} fields: ${refs.join(', ')}`;
  } else if (cleanName === 'get_profile_value') {
    // SECURITY RULE: Never show values of get_profile_value results or requests
    argsStr = `field: ${input.field || '(unspecified)'}`;
  } else if (cleanName === 'save_profile_field') {
    argsStr = `field: ${input.field || ''}, value: ••••••`;
  } else if (cleanName === 'browser_goto' || cleanName === 'browser_navigate') {
    argsStr = `url: "${input.url || ''}"`;
  } else if (cleanName === 'browser_fill' || cleanName === 'browser_type') {
    const target = input.ref || input.element || '';
    argsStr = `ref: "${target}", value: ••••••`;
  } else if (cleanName === 'browser_click') {
    const target = input.ref || input.element || '';
    argsStr = `ref: "${target}"`;
  } else if (cleanName === 'browser_select' || cleanName === 'browser_select_option') {
    const target = input.ref || input.element || '';
    argsStr = `ref: "${target}", option: "${input.option || input.value || ''}"`;
  } else if (cleanName === 'ask_user') {
    const q = input.question || '';
    argsStr = `question: "${q.length > 40 ? q.slice(0, 40) + '…' : q}"`;
  } else if (cleanName === 'handoff_to_user') {
    argsStr = `reason: "${input.reason || ''}"`;
  } else {
    try {
      if (typeof input === 'object' && input !== null) {
        if (Array.isArray(input)) {
          let s = JSON.stringify(input);
          argsStr = s.length > 80 ? s.slice(0, 80) + '…' : s;
        } else {
          const keys = Object.keys(input);
          if (keys.length === 0) {
            argsStr = '';
          } else {
            const parts = keys.map((k) => {
              const val = input[k];
              let valStr;
              if (typeof val === 'object' && val !== null) {
                valStr = JSON.stringify(val);
                if (valStr.length > 80) {
                  valStr = valStr.slice(0, 80) + '…';
                }
              } else {
                const s = String(val ?? '');
                valStr = s.length > 80 ? s.slice(0, 80) + '…' : s;
              }
              return `${k}: ${valStr}`;
            });
            argsStr = parts.join(', ');
          }
        }
      } else {
        const s = String(input ?? '');
        argsStr = s.length > 80 ? s.slice(0, 80) + '…' : s;
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
      <div class="activity-tool-wrap">
        ${isPlaywright ? '<span class="activity-tag-dom">DOM</span>' : ''}
        <span class="activity-tool-name">${escapeHtml(cleanName)}</span>
      </div>
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
    profileMetadata = profile;
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

    const isAsking = currentHighlightedFields.has(fieldKey);
    if (isAsking) {
      fieldCard.classList.add('highlighted-field');
    }

    fieldCard.innerHTML = `
      <div class="field-header">
        <div class="field-labels">
          <span class="field-label-hi">${escapeHtml(labelHi)}</span>
          <span class="field-label-en">${escapeHtml(labelEn)}</span>
          ${isAsking ? '<span class="field-badge-asking">अगस्त्य यह जानकारी पूछ रहे हैं (Requested)</span>' : ''}
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
}

function highlightProfileFields(fieldKeys) {
  clearFieldHighlights();
  if (!fieldKeys || fieldKeys.length === 0) return;

  let firstElement = null;
  fieldKeys.forEach((key) => {
    if (!key) return;
    currentHighlightedFields.add(key);
    const target = document.getElementById(`profile-field-${key}`);
    if (target) {
      target.classList.add('highlighted-field');
      const header = target.querySelector('.field-labels');
      if (header && !target.querySelector('.field-badge-asking')) {
        const badge = document.createElement('span');
        badge.className = 'field-badge-asking';
        badge.textContent = 'अगस्त्य यह जानकारी पूछ रहे हैं (Requested)';
        header.appendChild(badge);
      }
      if (!firstElement) {
        firstElement = target;
      }
    }
  });

  if (firstElement) {
    firstElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

function clearFieldHighlights(specificKeys = null) {
  const keysToClear = specificKeys ? specificKeys : Array.from(currentHighlightedFields);
  keysToClear.forEach((key) => {
    currentHighlightedFields.delete(key);
    const target = document.getElementById(`profile-field-${key}`);
    if (target) {
      target.classList.remove('highlighted-field');
      const badge = target.querySelector('.field-badge-asking');
      if (badge) badge.remove();
    }
  });
}

/* ==========================================================================
   Document Management (REST: GET / POST /api/documents)
   ========================================================================== */
async function loadDocuments() {
  if (!documentsList) return;
  try {
    const res = await fetch(`/api/documents?user_id=${encodeURIComponent(userId)}`);
    if (!res.ok) {
      throw new Error(`Documents status: ${res.status}`);
    }
    const docs = await res.json();
    renderDocumentsList(docs);
  } catch (err) {
    console.warn('Failed to load documents:', err);
    if (documentsList) {
      documentsList.innerHTML = `<div class="empty-docs-text">दस्तावेज़ लोड करने में त्रुटि (Error loading documents).</div>`;
    }
  }
}

function renderDocumentsList(docs) {
  if (!documentsList) return;
  documentsList.innerHTML = '';
  if (documentsCount) {
    documentsCount.textContent = String(docs ? docs.length : 0);
  }

  if (!Array.isArray(docs) || docs.length === 0) {
    documentsList.innerHTML = `<div class="empty-docs-text">कोई दस्तावेज़ अपलोड नहीं है / No documents uploaded yet.</div>`;
    return;
  }

  docs.forEach((doc) => {
    const card = document.createElement('div');
    card.className = 'doc-card';
    card.dataset.id = doc.id;

    const isImg = (doc.kind === 'image') || (doc.filename && /\.(png|jpe?g)$/i.test(doc.filename));
    const icon = isImg ? '🖼️' : '📄';
    const summary = doc.summary || doc.filename || 'Document';
    const kind = doc.kind || 'document';
    const dateStr = doc.uploaded_at ? new Date(doc.uploaded_at).toLocaleTimeString() : '';

    card.innerHTML = `
      <span class="doc-card-icon" aria-hidden="true">${icon}</span>
      <div class="doc-card-info">
        <span class="doc-card-summary">${escapeHtml(summary)}</span>
        <div class="doc-card-meta">
          <span class="doc-kind-badge">${escapeHtml(kind)}</span>
          <span class="doc-filename">${escapeHtml(doc.filename || '')}</span>
          ${dateStr ? `<span class="doc-time">${escapeHtml(dateStr)}</span>` : ''}
        </div>
      </div>
    `;
    documentsList.appendChild(card);
  });
}

async function uploadDocumentFile(file) {
  if (!file) return;

  const validExtensions = ['.pdf', '.png', '.jpg', '.jpeg'];
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  if (!validExtensions.includes(ext)) {
    showToast('केवल .pdf, .png, .jpg फ़ाइलें समर्थित हैं (Only .pdf, .png, .jpg allowed).');
    return;
  }

  const maxSize = 10 * 1024 * 1024; // 10 MB
  if (file.size > maxSize) {
    showToast('फ़ाइल 10 MB से बड़ी है (File size exceeds 10 MB limit).');
    return;
  }

  const formData = new FormData();
  formData.append('file', file);

  updateStatus('thinking', 'दस्तावेज़ अपलोड हो रहा है… (Uploading…)');

  try {
    const res = await fetch(`/api/documents?user_id=${encodeURIComponent(userId)}`, {
      method: 'POST',
      body: formData,
    });

    if (res.ok) {
      const doc = await res.json();
      appendDocumentChip(doc);
      await loadDocuments();
      if (isReady) {
        updateStatus('ready', 'तैयार (Ready)');
      } else {
        updateStatus('idle', '');
      }
    } else if (res.status === 413) {
      showToast('फ़ाइल 10 MB से बड़ी है (File size exceeds 10 MB limit).');
      updateStatus('idle', 'त्रुटि (Error)');
    } else if (res.status === 415) {
      showToast('केवल .pdf, .png, .jpg फ़ाइलें समर्थित हैं (Only .pdf, .png, .jpg allowed).');
      updateStatus('idle', 'त्रुटि (Error)');
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || `Upload failed: ${res.status}`);
      updateStatus('idle', 'त्रुटि (Error)');
    }
  } catch (err) {
    console.error('Document upload network error:', err);
    showToast('दस्तावेज़ अपलोड विफल रहा (Network error during upload).');
    updateStatus('idle', 'त्रुटि (Error)');
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
    isReady = false;
    updateConnectionStatus('connected');
    reconnectDelay = MIN_RECONNECT_DELAY;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    if (liveBrowser) {
      liveBrowser.onMainWsOpen();
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
    isReady = false;
    updateConnectionStatus('disconnected');
    if (liveBrowser) {
      liveBrowser.onMainWsClose();
    }
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
      if (data.status === 'ready') {
        isReady = true;
        if (!audioQueue.isSpeaking && !isRecording) {
          updateStatus('ready', 'तैयार (Ready)');
        }
      } else if (data.status === 'user_control') {
        // The citizen took control themselves: not a handoff, so no 'your turn' banner.
        updateStatus('user_control', 'आपका नियंत्रण (Citizen in control)');
      } else if (data.handoff || data.status === 'handoff') {
        showAmberBanner(data.reason);
        if (liveBrowser) {
          liveBrowser.triggerHandoffAttention(data.reason);
        }
        updateStatus('idle', 'आपकी बारी (Your Turn)');
      } else if (data.status) {
        if (!audioQueue.isSpeaking && !isRecording) {
          updateStatus(data.status);
        }
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
      if (liveBrowser) {
        liveBrowser.triggerHandoffAttention(data.reason);
      }
      break;

    case 'tool_call':
      logToolCall(data);
      break;

    case 'screenshot':
      if (data.png_b64) {
        browserScreenshot.src = `data:image/png;base64,${data.png_b64}`;
        if (!liveBrowser || !liveBrowser.hasReceivedLiveFrame) {
          browserScreenshot.classList.remove('hidden');
          browserPlaceholder.classList.add('hidden');
        }
      }
      if (data.url) {
        browserUrlText.textContent = data.url;
        browserUrlLink.href = data.url;
      }
      if (liveBrowser) {
        liveBrowser.updateScreenshotFallback(data.png_b64, data.url);
      }
      break;

    case 'profile_updated':
      loadProfile();
      break;

    case 'document_uploaded':
      if (data && (data.summary || data.filename)) {
        appendDocumentChip(data);
      }
      loadDocuments();
      break;

    case 'tts':
      if (data.audio_b64) {
        audioQueue.enqueue(data.audio_b64, data.mime || 'audio/wav', {
          seq: data.seq,
          final: data.final,
          text: data.text,
        });
      }
      break;

    case 'error':
      showToast(data.error || data.detail || 'एक त्रुटि उत्पन्न हुई (An error occurred).');
      updateStatus('idle', 'त्रुटि (Error)');
      break;

    case 'done':
      if (!audioQueue.isPlaying) {
        if (isReady) {
          updateStatus('ready', 'तैयार (Ready)');
        } else {
          updateStatus('warming_up', 'तैयार हो रहा है… / Warming up…');
        }
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

  // 5. TTS Speaker Toggle & Stop Speaking
  ttsToggle.addEventListener('click', () => {
    // Clicking the speaker icon while speaking clears the queue and stops playback
    if (audioQueue.isSpeaking) {
      audioQueue.stopAll();
      return;
    }

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

  // 6. Stop speaking button
  if (stopSpeakingBtn) {
    stopSpeakingBtn.addEventListener('click', () => {
      audioQueue.stopAll();
    });
  }

  // 7. Amber banner view browser button & close button
  if (bannerViewBrowserBtn) {
    bannerViewBrowserBtn.addEventListener('click', () => {
      switchToTab('browser-panel');
      if (liveBrowser && window.innerWidth > 900) {
        liveBrowser.expandView();
      }
    });
  }
  amberBannerClose.addEventListener('click', hideAmberBanner);

  // 8. Profile refresh button
  profileRefreshBtn.addEventListener('click', loadProfile);

  // 9. Mobile tab navigation
  const tabBtns = document.querySelectorAll('.tab-btn');
  tabBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.target;
      if (targetId) {
        switchToTab(targetId);
      }
    });
  });

  // 10. Document attach button and file input
  if (attachBtn && docFileInput) {
    attachBtn.addEventListener('click', () => {
      docFileInput.click();
    });

    docFileInput.addEventListener('change', async () => {
      if (docFileInput.files && docFileInput.files[0]) {
        const file = docFileInput.files[0];
        await uploadDocumentFile(file);
        docFileInput.value = '';
      }
    });
  }

  // 11. Documents refresh button
  if (docsRefreshBtn) {
    docsRefreshBtn.addEventListener('click', loadDocuments);
  }

  // 12. Drag and drop onto chat panel
  if (chatPanel) {
    ['dragenter', 'dragover'].forEach((eventName) => {
      chatPanel.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (chatDropOverlay) {
          chatDropOverlay.classList.remove('hidden');
        }
      });
    });

    ['dragleave', 'drop'].forEach((eventName) => {
      chatPanel.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (chatDropOverlay && (eventName === 'drop' || e.relatedTarget === null || !chatPanel.contains(e.relatedTarget))) {
          chatDropOverlay.classList.add('hidden');
        }
      });
    });

    chatPanel.addEventListener('drop', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (chatDropOverlay) {
        chatDropOverlay.classList.add('hidden');
      }
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
        await uploadDocumentFile(e.dataTransfer.files[0]);
      }
    });
  }

  // 13. PWA install button
  if (pwaInstallBtn) {
    pwaInstallBtn.addEventListener('click', async () => {
      if (!deferredInstallPrompt) return;
      deferredInstallPrompt.prompt();
      const choice = await deferredInstallPrompt.userChoice;
      if (choice && choice.outcome === 'accepted') {
        pwaInstallBtn.classList.add('hidden');
      }
      deferredInstallPrompt = null;
    });
  }
}

// Register Service Worker for PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js', { scope: '/' })
      .catch((err) => console.warn('[PWA] Service worker registration failed:', err));
  });
}

// PWA Install Prompt handling
let deferredInstallPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  if (pwaInstallBtn) {
    pwaInstallBtn.classList.remove('hidden');
  }
});

window.addEventListener('appinstalled', () => {
  deferredInstallPrompt = null;
  if (pwaInstallBtn) {
    pwaInstallBtn.classList.add('hidden');
  }
});

function switchToTab(panelId) {
  const tabBtns = document.querySelectorAll('.tab-btn');
  const panels = document.querySelectorAll('.panel');

  tabBtns.forEach((b) => {
    if (b.dataset.target === panelId) {
      b.classList.add('active');
    } else {
      b.classList.remove('active');
    }
  });

  panels.forEach((p) => {
    if (p.id === panelId) {
      p.classList.add('active');
      p.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
      p.classList.remove('active');
    }
  });
}

/* ==========================================================================
   Initialization
   ========================================================================== */
window.addEventListener('DOMContentLoaded', async () => {
  initEventListeners();
  liveBrowser = initLiveBrowser({
    userId,
    onTabSwitch: (panelId) => switchToTab(panelId),
  });
  await loadLanguages();
  await loadProfile();
  await loadDocuments();
  connectWebSocket();
});
