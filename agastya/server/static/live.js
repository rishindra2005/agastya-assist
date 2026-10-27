/**
 * live.js — Live Controllable Browser View for Agastya Assist (अगस्त्य सहायक).
 *
 * Plain ES module without frameworks or external dependencies.
 * Implements Batch E "Server live socket" protocol over ws(s)://host/ws/live?user_id=
 *
 * Features:
 * - WebSocket connection with retry and exponential backoff on 4404
 * - Binary JPEG frame rendering onto <canvas> via createImageBitmap (fallback Image + objectURL)
 * - Automatic aspect-ratio maintenance (meta width / height) fitting panel width
 * - URL + title bar with back / reload navigation buttons
 * - Human control toggle ('नियंत्रण लें / Take control' ↔ 'हो गया, आगे बढ़ो / Done — let Agastya continue')
 * - Saffron outline on canvas while citizen is in control
 * - Mouse capture (scaled to page CSS px), clicks (click_count), wheel, mousemove throttled to 30/s
 * - Keyboard events on canvas focus (keydown/keyup, preventDefault for navigation keys, text payload)
 * - Paste (Ctrl+V) handler sending {type: 'text'}
 * - Mobile touch tap = click and dedicated "Type…" input box for OTP / text entry
 * - Handoff / blocked / user_control alert handling with pulsating button and notice
 * - Real-time FPS / 'live' dot and 5-second inactivity stream-paused detection
 * - Screenshot <img> fallback when live socket is unavailable
 */

export class LiveBrowser {
  constructor(options = {}) {
    this.userId = options.userId || 'default';
    this.onTabSwitch = options.onTabSwitch || null;

    // DOM Elements
    this.canvas = document.getElementById('live-canvas');
    this.viewport = document.getElementById('browser-viewport');
    this.screenshot = document.getElementById('browser-screenshot');
    this.placeholder = document.getElementById('browser-placeholder');

    this.urlText = document.getElementById('browser-url-text');
    this.urlLink = document.getElementById('browser-url-link');
    this.pageTitle = document.getElementById('live-page-title');
    this.navBackBtn = document.getElementById('live-nav-back');
    this.navReloadBtn = document.getElementById('live-nav-reload');

    this.liveDot = document.getElementById('live-stream-dot');
    this.liveFps = document.getElementById('live-stream-fps');
    this.pausedOverlay = document.getElementById('live-stream-paused');

    this.controlBtn = document.getElementById('live-control-btn');
    this.controlBtnText = document.getElementById('live-control-btn-text');
    this.controlBtnIcon = document.getElementById('live-control-btn-icon');
    this.noteContainer = document.getElementById('live-note-container');
    this.noteInput = document.getElementById('live-note-input');
    this.mobileTypeContainer = document.getElementById('live-mobile-type-container');
    this.mobileTypeForm = document.getElementById('live-mobile-type-form');
    this.mobileTypeInput = document.getElementById('live-mobile-type-input');

    this.handoffBanner = document.getElementById('live-handoff-banner');
    this.handoffText = document.getElementById('live-handoff-text');

    // Expanded view elements
    this.previewCard = document.querySelector('.browser-preview-card');
    this.expandBtn = document.getElementById('live-expand-btn');
    this.closeExpandedBtn = document.getElementById('live-expanded-close-btn');
    this.expandedBackdrop = document.getElementById('live-expanded-backdrop');

    // One-line citizen control hint
    this.controlHint = document.getElementById('live-control-hint');

    // Canvas 2D context
    this.ctx = this.canvas ? this.canvas.getContext('2d') : null;

    // State
    this.ws = null;
    this.isMainWsOpen = false;
    this.isConnected = false;
    this.userInControl = false;
    this.isExpanded = false;
    this.reconnectTimer = null;
    this.reconnectDelay = 1000;
    this.MIN_RECONNECT_DELAY = 1000;
    this.MAX_RECONNECT_DELAY = 10000;

    this.meta = {
      width: 1280,
      height: 800,
      url: 'about:blank',
      title: 'पोर्टल (Portal)',
    };

    // Frame & FPS tracking
    this.hasReceivedLiveFrame = false;
    this.framesThisSecond = 0;
    this.currentFps = 0;
    this.fpsInterval = null;
    this.isStreamPaused = false;
    this.pauseTimeout = null;

    // Frame decoding queue
    this.latestBlob = null;
    this.isDecoding = false;

    // Throttled mouse movement (~30 calls per second)
    this.lastMouseMoveTime = 0;
    this.MOUSE_MOVE_INTERVAL_MS = 33; // ~30/s

    // Mobile touch tracking
    this.touchStart = null;

    this._initDomEvents();
    this._startFpsMonitor();
  }

  /* ==========================================================================
     WebSocket Lifecycle & Protocol
     ========================================================================== */

  onMainWsOpen() {
    this.isMainWsOpen = true;
    this.reconnectDelay = this.MIN_RECONNECT_DELAY;
    this.connect();
  }

  onMainWsClose() {
    this.isMainWsOpen = false;
    this.disconnect();
    this.updateLiveIndicator();
  }

  connect() {
    if (!this.isMainWsOpen) {
      return;
    }
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = `${protocol}//${host}/ws/live?user_id=${encodeURIComponent(this.userId)}`;

    try {
      this.ws = new WebSocket(url);
      this.ws.binaryType = 'blob';
    } catch (err) {
      console.warn('[live] Failed to instantiate WebSocket:', err);
      this._scheduleReconnect();
      return;
    }

    this.updateLiveIndicator();

    this.ws.onopen = () => {
      this.isConnected = true;
      this.reconnectDelay = this.MIN_RECONNECT_DELAY;
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
      this.updateLiveIndicator();
    };

    this.ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        try {
          const msg = JSON.parse(event.data);
          this._handleTextMessage(msg);
        } catch (err) {
          console.warn('[live] Invalid JSON received:', err, event.data);
        }
      } else if (event.data instanceof Blob) {
        this._handleBinaryFrame(event.data);
      } else if (event.data instanceof ArrayBuffer) {
        this._handleBinaryFrame(new Blob([event.data], { type: 'image/jpeg' }));
      }
    };

    this.ws.onclose = (event) => {
      this.isConnected = false;
      this.ws = null;
      this.updateLiveIndicator();

      // Retry with exponential backoff on 4404 (session still starting) or unexpected drop
      if (this.isMainWsOpen) {
        this._scheduleReconnect();
      }
    };

    this.ws.onerror = (err) => {
      console.warn('[live] WebSocket error:', err);
      // onclose handles reconnect
    };
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.pauseTimeout) {
      clearTimeout(this.pauseTimeout);
      this.pauseTimeout = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        // ignore
      }
      this.ws = null;
    }
    this.isConnected = false;
  }

  _scheduleReconnect() {
    if (this.reconnectTimer || !this.isMainWsOpen) {
      return;
    }
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.reconnectDelay = Math.min(this.MAX_RECONNECT_DELAY, Math.round(this.reconnectDelay * 1.5));
      this.connect();
    }, this.reconnectDelay);
  }

  sendJson(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(obj));
        return true;
      } catch (e) {
        console.warn('[live] Failed to send JSON payload:', e, obj);
      }
    }
    return false;
  }

  /* ==========================================================================
     Incoming Message Dispatcher
     ========================================================================== */

  _handleTextMessage(msg) {
    const type = msg.type;
    switch (type) {
      case 'meta':
        if (msg.width) this.meta.width = Number(msg.width);
        if (msg.height) this.meta.height = Number(msg.height);
        if (msg.url) this.meta.url = String(msg.url);
        if (msg.title) this.meta.title = String(msg.title);
        this._applyMeta();
        break;

      case 'control':
        const inControl = Boolean(msg.user_in_control);
        this.setUserInControl(inControl);
        if (inControl) {
          if (window.innerWidth > 900) {
            this.expandView();
          }
        } else {
          this.collapseView();
        }
        break;

      case 'error':
        console.warn('[live] Server returned error:', msg.detail || msg.error || msg);
        this._showLiveError(msg.message || msg.detail || 'Live control error');
        break;

      default:
        console.debug('[live] Unhandled text message:', msg);
    }
  }

  _showLiveError(text) {
    if (!this.controlHint) return;
    this.controlHint.textContent = text;
    this.controlHint.classList.remove('hidden');
    clearTimeout(this._hintTimer);
    this._hintTimer = setTimeout(() => {
      if (!this.userInControl) this.controlHint.classList.add('hidden');
    }, 4000);
  }

  _applyMeta() {
    // 1. Update URL bar
    if (this.urlText) {
      this.urlText.textContent = this.meta.url || 'about:blank';
    }
    if (this.urlLink) {
      this.urlLink.href = this.meta.url || '#';
    }

    // 2. Update page title
    if (this.pageTitle) {
      this.pageTitle.textContent = this.meta.title || 'पोर्टल (Portal)';
      this.pageTitle.title = this.meta.title || '';
    }

    // 3. Keep canvas & viewport aspect ratio = meta.width / meta.height
    if (this.meta.width && this.meta.height) {
      const ratioStr = `${this.meta.width} / ${this.meta.height}`;
      if (this.canvas) {
        this.canvas.style.aspectRatio = ratioStr;
      }
      if (this.viewport) {
        this.viewport.style.aspectRatio = ratioStr;
      }
    }
  }

  /* ==========================================================================
     Binary Frame Decoding & Rendering
     ========================================================================== */

  _handleBinaryFrame(blob) {
    this.hasReceivedLiveFrame = true;
    this.framesThisSecond += 1;

    // Reset 5-second inactivity timer
    if (this.pauseTimeout) {
      clearTimeout(this.pauseTimeout);
    }
    if (this.isStreamPaused) {
      this.isStreamPaused = false;
      this._updatePausedUi(false);
    }
    this.pauseTimeout = setTimeout(() => {
      this._onStreamPaused();
    }, 5000);

    // Queue frame for decode (drops older frames if decode is in progress)
    this.latestBlob = blob;
    if (!this.isDecoding) {
      this._decodeNextFrame();
    }
  }

  async _decodeNextFrame() {
    if (this.isDecoding || !this.latestBlob) {
      return;
    }
    this.isDecoding = true;

    while (this.latestBlob) {
      const blob = this.latestBlob;
      this.latestBlob = null;
      let drawn = false;

      // Method 1: createImageBitmap (high performance, GPU-accelerated)
      if (window.createImageBitmap) {
        try {
          const bitmap = await createImageBitmap(blob);
          if (this.canvas) {
            if (this.canvas.width !== bitmap.width || this.canvas.height !== bitmap.height) {
              this.canvas.width = bitmap.width;
              this.canvas.height = bitmap.height;
              this.canvas.style.aspectRatio = `${bitmap.width} / ${bitmap.height}`;
            }
            if (this.ctx) {
              this.ctx.drawImage(bitmap, 0, 0);
            }
          }
          if (typeof bitmap.close === 'function') {
            bitmap.close();
          }
          drawn = true;
        } catch (bmErr) {
          drawn = false;
        }
      }

      // Method 2: Image + objectURL fallback (revokes URL immediately on load/error)
      if (!drawn) {
        await new Promise((resolve) => {
          const objectUrl = URL.createObjectURL(blob);
          const img = new Image();
          img.onload = () => {
            if (this.canvas) {
              if (this.canvas.width !== img.naturalWidth || this.canvas.height !== img.naturalHeight) {
                this.canvas.width = img.naturalWidth;
                this.canvas.height = img.naturalHeight;
                this.canvas.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
              }
              if (this.ctx) {
                this.ctx.drawImage(img, 0, 0);
              }
            }
            URL.revokeObjectURL(objectUrl);
            resolve();
          };
          img.onerror = () => {
            URL.revokeObjectURL(objectUrl);
            resolve();
          };
          img.src = objectUrl;
        });
      }

      this._showCanvasView();
    }

    this.isDecoding = false;
  }

  _showCanvasView() {
    if (this.canvas) {
      this.canvas.classList.remove('hidden');
    }
    if (this.screenshot) {
      this.screenshot.classList.add('hidden');
    }
    if (this.placeholder) {
      this.placeholder.classList.add('hidden');
    }
  }

  updateScreenshotFallback(pngB64, url) {
    if (url) {
      this.meta.url = url;
      if (this.urlText) this.urlText.textContent = url;
      if (this.urlLink) this.urlLink.href = url;
    }

    if (this.screenshot && pngB64) {
      this.screenshot.src = `data:image/png;base64,${pngB64}`;
      if (!this.hasReceivedLiveFrame) {
        this.screenshot.classList.remove('hidden');
        if (this.canvas) this.canvas.classList.add('hidden');
        if (this.placeholder) this.placeholder.classList.add('hidden');
      }
    }
  }

  /* ==========================================================================
     FPS & Stream State Monitor
     ========================================================================== */

  _startFpsMonitor() {
    if (this.fpsInterval) clearInterval(this.fpsInterval);
    this.fpsInterval = setInterval(() => {
      this.currentFps = this.framesThisSecond;
      this.framesThisSecond = 0;
      this.updateLiveIndicator();
    }, 1000);
  }

  _onStreamPaused() {
    this.isStreamPaused = true;
    this._updatePausedUi(true);
    this.updateLiveIndicator();
  }

  _updatePausedUi(isPaused) {
    if (this.pausedOverlay) {
      if (isPaused) {
        this.pausedOverlay.classList.remove('hidden');
      } else {
        this.pausedOverlay.classList.add('hidden');
      }
    }
  }

  updateLiveIndicator() {
    if (!this.liveDot || !this.liveFps) return;

    if (!this.isConnected) {
      this.liveDot.className = 'live-dot disconnected';
      this.liveFps.textContent = this.isMainWsOpen ? 'जुड़ रहे हैं… (Connecting…)' : 'ऑफलाइन (Offline)';
    } else if (this.isStreamPaused) {
      this.liveDot.className = 'live-dot paused';
      this.liveFps.textContent = 'stream paused';
    } else if (this.hasReceivedLiveFrame) {
      this.liveDot.className = 'live-dot active';
      this.liveFps.textContent = `● LIVE (${this.currentFps} fps)`;
    } else {
      this.liveDot.className = 'live-dot connecting';
      this.liveFps.textContent = 'तैयार हो रहा है… (Starting…)';
    }
  }

  /* ==========================================================================
     Expanded View (Fixed Overlay: 92vw x 88vh desktop / 100vw x 100dvh mobile)
     ========================================================================== */

  toggleExpand() {
    if (this.isExpanded) {
      this.collapseView();
    } else {
      this.expandView();
    }
  }

  expandView() {
    if (this.isExpanded) return;
    this.isExpanded = true;

    if (this.previewCard) {
      this.previewCard.classList.add('expanded');
    }
    if (this.expandedBackdrop) {
      this.expandedBackdrop.classList.remove('hidden');
    }
    if (this.closeExpandedBtn) {
      this.closeExpandedBtn.classList.remove('hidden');
    }
    if (this.expandBtn) {
      this.expandBtn.setAttribute('aria-expanded', 'true');
      this.expandBtn.title = 'छोटा दृश्य / Collapse view';
    }
    document.body.classList.add('live-expanded');

    if (this.userInControl && this.canvas) {
      this.canvas.focus();
    }
  }

  collapseView() {
    if (!this.isExpanded) return;
    this.isExpanded = false;

    if (this.previewCard) {
      this.previewCard.classList.remove('expanded');
    }
    if (this.expandedBackdrop) {
      this.expandedBackdrop.classList.add('hidden');
    }
    if (this.closeExpandedBtn) {
      this.closeExpandedBtn.classList.add('hidden');
    }
    if (this.expandBtn) {
      this.expandBtn.setAttribute('aria-expanded', 'false');
      this.expandBtn.title = 'बड़ा दृश्य / Expand Live View';
    }
    document.body.classList.remove('live-expanded');
  }

  /* ==========================================================================
     Control Toggle ('नियंत्रण लें' ↔ 'हो गया, आगे बढ़ो')
     ========================================================================== */

  toggleControl() {
    if (!this.userInControl) {
      this.takeControl();
    } else {
      const note = this.noteInput ? this.noteInput.value.trim() : '';
      this.releaseControl(note);
    }
  }

  takeControl() {
    this.sendJson({
      type: 'control',
      take: true,
    });
    this.setUserInControl(true);
    this.dismissHandoffPrompt();

    // Auto-open expanded view on desktop when taking control
    if (window.innerWidth > 900) {
      this.expandView();
    }

    if (this.canvas) {
      this.canvas.focus();
    }
  }

  releaseControl(note = '') {
    const payload = {
      type: 'control',
      take: false,
    };
    if (note) {
      payload.note = note;
    }
    this.sendJson(payload);

    if (this.noteInput) {
      this.noteInput.value = '';
    }
    this.setUserInControl(false);

    // Releasing control automatically closes expanded view
    this.collapseView();
  }

  setUserInControl(inControl) {
    this.userInControl = inControl;

    // Show/hide one-line citizen hint above canvas
    if (this.controlHint) {
      if (inControl) {
        this.controlHint.classList.remove('hidden');
      } else {
        this.controlHint.classList.add('hidden');
      }
    }

    // Toggle saffron outline and visual indicator on canvas
    if (this.canvas) {
      if (inControl) {
        this.canvas.classList.add('in-control');
      } else {
        this.canvas.classList.remove('in-control');
      }
    }

    // Toggle button label and icon
    if (this.controlBtn) {
      if (inControl) {
        this.controlBtn.classList.add('in-control');
        this.controlBtn.classList.remove('pulse-attention');
        if (this.controlBtnText) {
          this.controlBtnText.textContent = 'हो गया, आगे बढ़ो / Done — let Agastya continue';
        }
        if (this.controlBtnIcon) {
          this.controlBtnIcon.textContent = '✓';
        }
      } else {
        this.controlBtn.classList.remove('in-control');
        if (this.controlBtnText) {
          this.controlBtnText.textContent = 'नियंत्रण लें / Take control';
        }
        if (this.controlBtnIcon) {
          this.controlBtnIcon.textContent = '🎮';
        }
      }
    }

    // Show/hide optional note input and mobile type input
    if (this.noteContainer) {
      if (inControl) {
        this.noteContainer.classList.remove('hidden');
      } else {
        this.noteContainer.classList.add('hidden');
      }
    }

    if (this.mobileTypeContainer) {
      if (inControl) {
        this.mobileTypeContainer.classList.remove('hidden');
      } else {
        this.mobileTypeContainer.classList.add('hidden');
      }
    }
  }

  /* ==========================================================================
     Handoff Attention (Handoff / Blocked / Status user_control)
     ========================================================================== */

  triggerHandoffAttention(reason) {
    // 1. Switch to browser tab on mobile
    if (typeof this.onTabSwitch === 'function') {
      this.onTabSwitch('browser-panel');
    }

    // 2. On desktop, taking control / handoff attention auto-opens expanded view
    if (window.innerWidth > 900) {
      this.expandView();
    }

    // 3. Scroll live view into view
    if (this.viewport) {
      this.viewport.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // 4. Pulse the Take-control button
    if (this.controlBtn && !this.userInControl) {
      this.controlBtn.classList.add('pulse-attention');
    }

    // 5. Show 'कैप्चा / OTP आप भरें — Solve the captcha / OTP here'
    if (this.handoffBanner) {
      if (this.handoffText) {
        this.handoffText.textContent = 'कैप्चा / OTP आप भरें — Solve the captcha / OTP here';
      }
      this.handoffBanner.classList.remove('hidden');
    }
  }

  dismissHandoffPrompt() {
    if (this.controlBtn) {
      this.controlBtn.classList.remove('pulse-attention');
    }
    if (this.handoffBanner) {
      this.handoffBanner.classList.add('hidden');
    }
  }

  /* ==========================================================================
     Coordinate Mapping & Mouse / Keyboard / Touch Handlers
     ========================================================================== */

  mapToPageCoords(clientX, clientY) {
    if (!this.canvas) return { x: 0, y: 0 };
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };

    const metaW = this.meta.width || this.canvas.width || 1280;
    const metaH = this.meta.height || this.canvas.height || 800;

    const scaleX = metaW / rect.width;
    const scaleY = metaH / rect.height;

    const x = Math.max(0, Math.min(metaW, (clientX - rect.left) * scaleX));
    const y = Math.max(0, Math.min(metaH, (clientY - rect.top) * scaleY));

    return {
      x: Math.round(x * 10) / 10,
      y: Math.round(y * 10) / 10,
    };
  }

  _initDomEvents() {
    // 0. Expand / Collapse view bindings
    if (this.expandBtn) {
      this.expandBtn.addEventListener('click', () => {
        this.toggleExpand();
      });
    }

    if (this.closeExpandedBtn) {
      this.closeExpandedBtn.addEventListener('click', () => {
        this.collapseView();
      });
    }

    if (this.expandedBackdrop) {
      this.expandedBackdrop.addEventListener('click', () => {
        this.collapseView();
      });
    }

    // Global Escape key listener to close expanded view
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.isExpanded) {
        e.preventDefault();
        this.collapseView();
      }
    });

    // 1. Navigation buttons (Back / Reload)
    if (this.navBackBtn) {
      this.navBackBtn.addEventListener('click', () => {
        this.sendJson({ type: 'nav', action: 'back' });
      });
    }

    if (this.navReloadBtn) {
      this.navReloadBtn.addEventListener('click', () => {
        this.sendJson({ type: 'nav', action: 'reload' });
      });
    }

    // 2. Control Toggle Button
    if (this.controlBtn) {
      this.controlBtn.addEventListener('click', () => {
        this.toggleControl();
      });
    }

    // 3. Mouse interactions on canvas
    if (this.canvas) {
      this.canvas.addEventListener('click', (e) => {
        // Map before any layout change: taking control shows the hint bar and moves the canvas.
        const coords = this.mapToPageCoords(e.clientX, e.clientY);
        if (!this.userInControl) {
          // Clicking the stream means "I want to use the portal": take control, then deliver the click.
          this.takeControl();
        }
        const buttonMap = { 0: 'left', 1: 'middle', 2: 'right' };
        const button = buttonMap[e.button] || 'left';
        const clickCount = e.detail || 1;

        this.sendJson({
          type: 'mouse',
          kind: 'click',
          x: coords.x,
          y: coords.y,
          button,
          click_count: clickCount,
        });

        this.canvas.focus();
      });

      this.canvas.addEventListener('contextmenu', (e) => {
        if (!this.userInControl) return;
        e.preventDefault();
        const coords = this.mapToPageCoords(e.clientX, e.clientY);
        this.sendJson({
          type: 'mouse',
          kind: 'click',
          x: coords.x,
          y: coords.y,
          button: 'right',
          click_count: 1,
        });
      });

      this.canvas.addEventListener(
        'wheel',
        (e) => {
          if (!this.userInControl) return;
          e.preventDefault();
          const coords = this.mapToPageCoords(e.clientX, e.clientY);
          this.sendJson({
            type: 'mouse',
            kind: 'wheel',
            x: coords.x,
            y: coords.y,
            delta_y: e.deltaY,
          });
        },
        { passive: false }
      );

      this.canvas.addEventListener('mousemove', (e) => {
        if (!this.userInControl) return;
        const now = performance.now();
        if (now - this.lastMouseMoveTime >= this.MOUSE_MOVE_INTERVAL_MS) {
          this.lastMouseMoveTime = now;
          const coords = this.mapToPageCoords(e.clientX, e.clientY);
          this.sendJson({
            type: 'mouse',
            kind: 'move',
            x: coords.x,
            y: coords.y,
          });
        }
      });

      // 4. Keyboard interactions when canvas is focused
      const PREVENT_KEYS = new Set([
        'Tab',
        'Backspace',
        'ArrowUp',
        'ArrowDown',
        'ArrowLeft',
        'ArrowRight',
        'Space',
        ' ',
      ]);

      this.canvas.addEventListener('keydown', (e) => {
        if (!this.userInControl) return;

        if (e.key === 'Escape' && this.isExpanded) {
          e.preventDefault();
          this.collapseView();
          return;
        }

        if (PREVENT_KEYS.has(e.key)) {
          e.preventDefault();
        }

        // Allow paste event to fire for Ctrl+V / Cmd+V
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') {
          return;
        }

        const isPrintable = e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;
        const text = isPrintable ? e.key : '';

        this.sendJson({
          type: 'key',
          kind: 'down',
          key: e.key,
          code: e.code || '',
          text,
        });
      });

      this.canvas.addEventListener('keyup', (e) => {
        if (!this.userInControl) return;

        if (PREVENT_KEYS.has(e.key)) {
          e.preventDefault();
        }

        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') {
          return;
        }

        const isPrintable = e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;
        const text = isPrintable ? e.key : '';

        this.sendJson({
          type: 'key',
          kind: 'up',
          key: e.key,
          code: e.code || '',
          text,
        });
      });

      // 5. Paste handler (Ctrl+V -> { type: 'text' })
      this.canvas.addEventListener('paste', (e) => {
        if (!this.userInControl) return;
        e.preventDefault();
        const text = (e.clipboardData || window.clipboardData)?.getData('text');
        if (text) {
          this.sendJson({
            type: 'text',
            text,
          });
        }
      });

      // 6. Mobile Touch Tap = Click
      this.canvas.addEventListener(
        'touchstart',
        (e) => {
          if (!this.userInControl) return;
          if (e.touches.length === 1) {
            const t = e.touches[0];
            this.touchStart = {
              x: t.clientX,
              y: t.clientY,
              time: Date.now(),
            };
          }
        },
        { passive: true }
      );

      this.canvas.addEventListener(
        'touchend',
        (e) => {
          if (!this.userInControl || !this.touchStart) return;
          const touch = e.changedTouches[0];
          if (!touch) return;

          const dx = Math.abs(touch.clientX - this.touchStart.x);
          const dy = Math.abs(touch.clientY - this.touchStart.y);
          const elapsed = Date.now() - this.touchStart.time;

          // Detect tap if moved < 12px within 400ms
          if (dx < 12 && dy < 12 && elapsed < 400) {
            e.preventDefault();
            const coords = this.mapToPageCoords(touch.clientX, touch.clientY);
            this.sendJson({
              type: 'mouse',
              kind: 'click',
              x: coords.x,
              y: coords.y,
              button: 'left',
              click_count: 1,
            });
            this.canvas.focus();
          }
          this.touchStart = null;
        },
        { passive: false }
      );
    }

    // 7. Mobile 'Type…' Input Form for OTP & text entry
    if (this.mobileTypeForm) {
      this.mobileTypeForm.addEventListener('submit', (e) => {
        e.preventDefault();
        if (!this.mobileTypeInput) return;
        const text = this.mobileTypeInput.value;
        if (!text) return;

        this.sendJson({
          type: 'text',
          text,
        });
        this.mobileTypeInput.value = '';
      });
    }
  }
}

export function initLiveBrowser(options = {}) {
  return new LiveBrowser(options);
}
