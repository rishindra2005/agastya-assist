(function () {
  'use strict';

  // Guard against double install in the same frame/window
  if (window.__agastyaGuardInstalled) {
    return;
  }
  window.__agastyaGuardInstalled = true;

  if (typeof window.__agastyaAgentActive !== 'boolean') {
    window.__agastyaAgentActive = false;
  }
  if (!Array.isArray(window.__agastyaBlocked)) {
    window.__agastyaBlocked = [];
  }

  // Navigation block targets: payment gateways and logout endpoints
  const PAYMENT_HOST_KEYWORDS = [
    'paygov',
    'billdesk',
    'razorpay',
    'payu',
    'sbiepay',
    'ccavenue',
    'paytm',
  ];

  const LOGOUT_PATH_KEYWORDS = [
    '/logout',
    '/signout',
    'logout',
    'signout',
  ];

  // Explicit ALLOW list overrides generic submit rules:
  // 'save draft','save as draft','next','continue','previous','back','add',
  // 'search','select','preview','download','clear','reset filter'
  const ALLOW_PATTERNS = [
    [/\bsave\s+as\s+draft\b/i, 'save as draft'],
    [/\bsave\s+draft\b/i, 'save draft'],
    [/\bnext\b/i, 'next'],
    [/\bcontinue\b/i, 'continue'],
    [/\bprevious\b/i, 'previous'],
    [/\bback\b/i, 'back'],
    [/\badd\b/i, 'add'],
    [/\bsearch\b/i, 'search'],
    [/\bselect\b/i, 'select'],
    [/\bpreview\b/i, 'preview'],
    [/\bdownload\b/i, 'download'],
    [/\bclear\b/i, 'clear'],
    [/\breset\s+filters?\b/i, 'reset filter'],
  ];

  // Generic text values for submit buttons that trigger 'generic submit' block
  const GENERIC_SUBMIT_TEXTS = new Set(['', 'go', 'ok']);

  // Block rules: list of [regex, reason]
  const BLOCK_RULES = [
    // Submit / Final action combinations (more specific rules first)
    [/\b(i\s+)?agree\s+(&|and\s+)?submit\b/i, 'agree and submit'],
    [/\bfinal\s+submit\b/i, 'final submit'],
    [/\bsubmit\s+return\b/i, 'submit return'],
    [/\bsubmit\s+form\b/i, 'submit form'],
    [/\bfile\s+(return|now)\b/i, 'file return'],
    [/\b(final\s+)?submission\b/i, 'final submit'],
    // Register only when combined with final/submit
    [/\b(final\s+.*regist(er|ration)|regist(er|ration)\s+.*(final|submit)|submit\s+.*regist(er|ration))\b/i, 'register (final)'],
    [/\bsubmit\b/i, 'submit'],
    // Hindi submit terms (जमा करें, जमा करे, जमा करना)
    [/जमा\s+कर(ें|े|ना)/i, 'submit (hindi)'],

    // OTP actions
    [/\b(generate|resend|verify|validate|send|get|aadhaar)\s+otp\b/i, 'otp'],
    [/\botp\b/i, 'otp'],
    // Hindi OTP terms (ओटीपी)
    [/ओटीपी/i, 'otp (hindi)'],

    // Payment actions
    [/\bproceed\s+to\s+pay\b/i, 'proceed to pay'],
    [/\b(make\s+payment|pay\s+now)\b/i, 'payment'],
    [/\b(pay|payment)\b/i, 'payment'],
    // Hindi payment terms (भुगतान)
    [/भुगतान(\s+कर(ें|े))?/i, 'payment (hindi)'],

    // E-Verify & Verification
    [/\b(e[\s-]?verify|everify)\b/i, 'e-verify'],
    [/\bverify\b/i, 'verify'],
    // ITR: "Proceed to Verification" files the return before e-verify
    [/\b(proceed|continue|go)\s+to\s+(e[\s-]?)?verification\b/i, 'file return (proceed to verification)'],
    // Hindi verify terms (सत्यापित, सत्यापन)
    [/सत्यापित(\s+कर(ें|े))?|सत्यापन/i, 'verify (hindi)'],

    // Login / Sign In / Logout
    [/\b(log\s*in|login)\b/i, 'login'],
    [/\b(sign\s*in|signin|sign-in)\b/i, 'sign in'],
    [/\b(log\s*out|logout|sign\s*out|signout|sign-out)\b/i, 'logout'],
    // Hindi login terms (लॉगिन, लॉग इन)
    [/लॉग(\s*इन|िन)/i, 'login (hindi)'],

    // Confirmation
    [/\bconfirm\b/i, 'confirm'],
    // Hindi confirm terms (पुष्टि करें)
    [/पुष्टि(\s+कर(ें|े))?/i, 'confirm (hindi)'],

    // Captcha refresh
    [/\b(captcha\s+refresh|refresh\s+captcha|reload\s+captcha|new\s+captcha)\b/i, 'captcha refresh'],
    // Hindi captcha
    [/कैप्चा/i, 'captcha (hindi)'],
  ];

  function normalizeText(s) {
    if (s == null) return '';
    let text = String(s).trim();
    if (!text) return '';
    text = text.replace(/&/g, ' and ');
    text = text.replace(/\s+/g, ' ');
    return text.trim().toLowerCase();
  }

  function normalizeIdentifier(s) {
    if (s == null) return '';
    let text = String(s).trim();
    if (!text) return '';
    // Split camelCase: btnSubmit -> btn Submit, getOTP -> get OTP
    text = text.replace(/([a-z])([A-Z])/g, '$1 $2');
    text = text.replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2');
    // Split common prefixes: btnSubmit -> btn submit
    text = text.replace(/^(btn|button|lnk|link)([a-zA-Z])/i, '$1 $2');
    text = text.replace(/&/g, ' and ');
    text = text.replace(/[-_./:#?&=]+/g, ' ');
    text = text.replace(/\s+/g, ' ');
    return text.trim().toLowerCase();
  }

  function checkNavigation(url) {
    if (!url || typeof url !== 'string') {
      return { allowed: true, reason: 'allowed' };
    }
    const cleaned = url.trim();
    if (!cleaned) {
      return { allowed: true, reason: 'allowed' };
    }

    let host = '';
    let path = '';
    try {
      const base = window.location && window.location.href ? window.location.href : 'http://localhost/';
      const parsed = new URL(cleaned, base);
      host = (parsed.hostname || parsed.host || '').toLowerCase();
      path = (parsed.pathname || '').toLowerCase();
    } catch (e) {
      const raw = cleaned.toLowerCase();
      if (!raw.startsWith('/') && raw.includes('.')) {
        const parts = raw.split('/', 2);
        host = parts[0];
        path = parts.length > 1 ? '/' + parts[1] : '';
      } else {
        path = raw;
      }
    }

    for (const kw of PAYMENT_HOST_KEYWORDS) {
      if (host.includes(kw) || path.includes(kw)) {
        return { allowed: false, reason: 'payment gateway (' + kw + ')' };
      }
    }

    for (const kw of LOGOUT_PATH_KEYWORDS) {
      if (path.includes(kw) || host.includes(kw) || cleaned.toLowerCase() === kw) {
        return { allowed: false, reason: 'logout endpoint (' + kw + ')' };
      }
    }

    return { allowed: true, reason: 'allowed' };
  }

  function checkElement(el) {
    if (!el || !(el instanceof Element || el.tagName)) {
      return { allowed: true, reason: 'allowed' };
    }

    const tag = (el.tagName || '').toLowerCase();
    const elem_type = (el.getAttribute('type') || el.type || '').toLowerCase();

    const raw_text = el.innerText !== undefined ? el.innerText : (el.textContent || '');
    let raw_value = '';
    if (el.value !== undefined && typeof el.value === 'string') {
      raw_value = el.value;
    } else {
      raw_value = el.getAttribute('value') || '';
    }

    const raw_aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.getAttribute('title') || '';
    const raw_id = el.id || el.getAttribute('id') || '';
    const raw_name = el.name || el.getAttribute('name') || '';
    const raw_href = el.getAttribute('href') || el.href || '';
    const raw_formaction = el.getAttribute('formaction') || '';

    // 1. Navigation checks on href / formaction
    if (raw_href && (tag === 'a' || raw_href.length > 0)) {
      const navDecision = checkNavigation(raw_href);
      if (!navDecision.allowed) {
        return navDecision;
      }
    }
    if (raw_formaction) {
      const navDecision = checkNavigation(raw_formaction);
      if (!navDecision.allowed) {
        return navDecision;
      }
    }

    const norm_text = normalizeText(raw_text);
    const norm_value = normalizeText(raw_value);
    const norm_aria = normalizeText(raw_aria);

    const norm_id = normalizeIdentifier(raw_id);
    const norm_name = normalizeIdentifier(raw_name);
    const norm_href = normalizeIdentifier(raw_href);

    const primary_texts = [norm_text, norm_value, norm_aria].filter(Boolean);
    const secondary_texts = [norm_id, norm_name, norm_href].filter(Boolean);

    // 2. Check BLOCK rules on primary visible texts
    for (const text of primary_texts) {
      for (const [pattern, reason] of BLOCK_RULES) {
        if (pattern.test(text)) {
          return { allowed: false, reason: reason };
        }
      }
    }

    // 3. Explicit ALLOW list overrides generic submit rules
    const candidate_allow_texts = primary_texts.length > 0 ? primary_texts : secondary_texts;
    for (const text of candidate_allow_texts) {
      for (const [pattern, allow_reason] of ALLOW_PATTERNS) {
        if (pattern.test(text)) {
          return { allowed: true, reason: 'allowed: ' + allow_reason };
        }
      }
    }

    // 4. Check BLOCK rules on secondary attributes
    for (const text of secondary_texts) {
      for (const [pattern, reason] of BLOCK_RULES) {
        if (pattern.test(text)) {
          return { allowed: false, reason: reason };
        }
      }
    }

    // 5. Generic submit check
    const is_submit_type = (elem_type === 'submit') ||
      (tag === 'input' && elem_type === 'submit') ||
      (tag === 'button' && (elem_type === 'submit' || elem_type === ''));

    if (is_submit_type) {
      const primary_repr = norm_text || norm_value || norm_aria;
      if (GENERIC_SUBMIT_TEXTS.has(primary_repr)) {
        return { allowed: false, reason: 'generic submit' };
      }
    }

    return { allowed: true, reason: 'allowed' };
  }

  function getElementText(el) {
    if (!el) return '';
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'form') {
      return el.id || el.name || el.getAttribute('action') || 'form';
    }
    const text = (el.innerText || el.textContent || '').trim();
    if (text) return text;
    const val = (el.value !== undefined ? String(el.value) : (el.getAttribute('value') || '')).trim();
    if (val) return val;
    const aria = (el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
    if (aria) return aria;
    const alt = (el.getAttribute('alt') || '').trim();
    if (alt) return alt;
    const nameOrId = (el.name || el.id || el.getAttribute('name') || el.getAttribute('id') || '').trim();
    if (nameOrId) return nameOrId;
    return tag;
  }

  function findBlockedTarget(target) {
    if (!target || !target.tagName) return null;

    // Check target itself first
    const decTarget = checkElement(target);
    if (!decTarget.allowed) {
      return { el: target, decision: decTarget };
    }

    // Check ancestor button/a/input[type=submit|button|image] or role=button
    if (target.closest) {
      const ancestor = target.closest('button, a, input[type="submit"], input[type="button"], input[type="image"], [role="button"]');
      if (ancestor && ancestor !== target) {
        const decAncestor = checkElement(ancestor);
        if (!decAncestor.allowed) {
          return { el: ancestor, decision: decAncestor };
        }
      }
    }

    return null;
  }

  let lastBlockedTime = 0;
  let lastBlockedEl = null;

  function recordBlock(text, reason, el) {
    const now = Date.now();
    // De-duplicate if part of the same physical gesture (pointerdown -> mousedown -> click) within 100ms
    if (el && el === lastBlockedEl && (now - lastBlockedTime) < 100) {
      return;
    }
    lastBlockedEl = el;
    lastBlockedTime = now;

    const item = {
      text: String(text || '').trim(),
      reason: String(reason || 'blocked').trim(),
      ts: now,
    };
    window.__agastyaBlocked.push(item);

    try {
      const event = new CustomEvent('agastya-blocked', { detail: item, bubbles: true });
      window.dispatchEvent(event);
    } catch (err) {
      // Ignore in environments where CustomEvent dispatch might fail
    }
  }

  // Intercept click, mousedown, pointerdown
  ['click', 'mousedown', 'pointerdown'].forEach(function (eventType) {
    document.addEventListener(
      eventType,
      function (e) {
        if (!window.__agastyaAgentActive) return;
        const blocked = findBlockedTarget(e.target);
        if (blocked) {
          e.preventDefault();
          e.stopImmediatePropagation();
          recordBlock(getElementText(blocked.el), blocked.decision.reason, blocked.el);
        }
      },
      true // capture phase
    );
  });

  // Intercept keydown (Enter / Space on matching button / input / link)
  document.addEventListener(
    'keydown',
    function (e) {
      if (!window.__agastyaAgentActive) return;
      const key = e.key;
      if (key === 'Enter' || key === ' ' || key === 'Spacebar') {
        const target = e.target;
        const blocked = findBlockedTarget(target);
        if (blocked) {
          const tag = (blocked.el.tagName || '').toLowerCase();
          const type = (blocked.el.getAttribute('type') || blocked.el.type || '').toLowerCase();
          const isButtonLike = tag === 'button' || tag === 'a' ||
            (tag === 'input' && (type === 'submit' || type === 'button' || type === 'image')) ||
            blocked.el.getAttribute('role') === 'button';

          if (isButtonLike || !blocked.decision.allowed) {
            e.preventDefault();
            e.stopImmediatePropagation();
            recordBlock(getElementText(blocked.el), blocked.decision.reason, blocked.el);
          }
        }
      }
    },
    true // capture phase
  );

  // Intercept submit events on document
  document.addEventListener(
    'submit',
    function (e) {
      if (!window.__agastyaAgentActive) return;
      e.preventDefault();
      e.stopImmediatePropagation();

      const form = e.target;
      const submitter = e.submitter;
      let text = '';
      let reason = 'submit';

      if (submitter) {
        text = getElementText(submitter);
        const dec = checkElement(submitter);
        if (!dec.allowed) {
          reason = dec.reason;
        }
      }
      if (!text && form) {
        text = getElementText(form) || form.id || form.name || 'submit';
      }

      recordBlock(text || 'submit', reason, submitter || form);
    },
    true // capture phase
  );

  // Override HTMLFormElement.prototype.submit and requestSubmit
  const originalSubmit = HTMLFormElement.prototype.submit;
  const originalRequestSubmit = HTMLFormElement.prototype.requestSubmit;

  HTMLFormElement.prototype.submit = function () {
    if (window.__agastyaAgentActive) {
      const text = getElementText(this) || this.id || this.name || 'form.submit()';
      recordBlock(text, 'submit', this);
      return;
    }
    return originalSubmit.apply(this, arguments);
  };

  if (originalRequestSubmit) {
    HTMLFormElement.prototype.requestSubmit = function (submitter) {
      if (window.__agastyaAgentActive) {
        let text = '';
        let reason = 'submit';
        if (submitter) {
          text = getElementText(submitter);
          const dec = checkElement(submitter);
          if (!dec.allowed) {
            reason = dec.reason;
          }
        }
        if (!text) {
          text = getElementText(this) || this.id || this.name || 'form.requestSubmit()';
        }
        recordBlock(text, reason, submitter || this);
        return;
      }
      return originalRequestSubmit.apply(this, arguments);
    };
  }
})();
