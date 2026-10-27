/* Shared behaviour for DEMO REPLICA portals: warning banner, watermark, mock captcha, mock OTP "phone".
   Include on EVERY page:  <link rel="stylesheet" href="/mock/_shared/demo.css"><script src="/mock/_shared/demo.js"></script>
   Nothing here talks to any real service. */
(function () {
  if (window.DemoPortal) return;

  function injectChrome() {
    if (!document.title.startsWith('[DEMO]')) document.title = '[DEMO] ' + document.title;
    const bar = document.createElement('div');
    bar.className = 'demo-warning-bar';
    bar.setAttribute('role', 'alert');
    bar.innerHTML = '⚠ DEMO REPLICA — NOT A GOVERNMENT WEBSITE · डेमो प्रतिकृति — यह सरकारी वेबसाइट नहीं है' +
      '<small>Built for the Agastya Assist hackathon demo. Nothing is submitted anywhere. Do not enter real personal data.</small>';
    document.body.prepend(bar);
    const foot = document.createElement('div');
    foot.className = 'demo-warning-footer';
    foot.textContent = 'This is a fictional replica for demonstration only. It is not affiliated with or endorsed by any government department. All numbers shown are fake.';
    document.body.append(foot);
    const wm = document.createElement('div');
    wm.className = 'demo-watermark';
    document.body.append(wm);
  }

  const CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  function randomText(n) { let s = ''; for (let i = 0; i < n; i++) s += CHARS[Math.floor(Math.random() * CHARS.length)]; return s; }

  /** Render a mock captcha into `container`. Returns {check(value) -> bool, refresh()}. */
  function captcha(container) {
    const wrap = document.createElement('span');
    wrap.className = 'demo-captcha';
    const canvas = document.createElement('canvas');
    canvas.width = 140; canvas.height = 44;
    canvas.setAttribute('aria-label', 'Captcha image (demo)');
    const btn = document.createElement('button');
    btn.type = 'button'; btn.textContent = '↻ Refresh';
    wrap.append(canvas, btn);
    container.append(wrap);
    let answer = '';
    function draw() {
      answer = randomText(5);
      const g = canvas.getContext('2d');
      g.clearRect(0, 0, 140, 44); g.fillStyle = '#eef'; g.fillRect(0, 0, 140, 44);
      for (let i = 0; i < 6; i++) { g.strokeStyle = `hsl(${Math.random() * 360},50%,60%)`; g.beginPath(); g.moveTo(Math.random() * 140, Math.random() * 44); g.lineTo(Math.random() * 140, Math.random() * 44); g.stroke(); }
      g.font = 'bold 24px monospace'; g.fillStyle = '#223';
      [...answer].forEach((ch, i) => { g.save(); g.translate(14 + i * 24, 30); g.rotate((Math.random() - .5) * .5); g.fillText(ch, 0, 0); g.restore(); });
    }
    btn.addEventListener('click', draw);
    draw();
    return { check: (v) => String(v || '').trim().toUpperCase() === answer, refresh: draw };
  }

  /** "Send" a mock OTP: shows it on an on-screen demo phone. Returns {check(value) -> bool}. */
  function sendOtp(label) {
    const otp = String(Math.floor(100000 + Math.random() * 900000));
    let box = document.querySelector('.demo-sms');
    if (!box) { box = document.createElement('div'); box.className = 'demo-sms'; document.body.append(box); }
    box.innerHTML = '<button type="button" aria-label="close">✕</button><div class="demo-sms-title">📱 DEMO PHONE · mock SMS (no real SMS is sent)</div>' +
      `<div>${label || 'Your one-time password is'}</div><div class="demo-sms-otp">${otp}</div><div style="font-size:11px;opacity:.7;margin-top:4px">Valid for 15 minutes · DEMO-OTP</div>`;
    box.querySelector('button').onclick = () => box.remove();
    return { check: (v) => String(v || '').trim() === otp };
  }

  function fakeNumber(prefix, digits) { let s = prefix || ''; for (let i = 0; i < digits; i++) s += Math.floor(Math.random() * 10); return s; }

  /** Persist demo form data across pages of one portal (sessionStorage, safe if unavailable). */
  function save(key, obj) { try { sessionStorage.setItem('demo:' + key, JSON.stringify(obj)); } catch (e) {} }
  function load(key) { try { return JSON.parse(sessionStorage.getItem('demo:' + key) || 'null'); } catch (e) { return null; } }

  window.DemoPortal = { captcha, sendOtp, fakeNumber, save, load };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', injectChrome); else injectChrome();
})();
