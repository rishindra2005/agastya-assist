"""Record demo videos: ITR-1 + Form 16 (Hindi voice) and Voter Form 6 (Tamil voice) on the local DEMO REPLICA portals.

Usage:  .venv/bin/python scripts/autopilot/itr_voter.py [itr|voter|both]   (default both, run in parallel)

Starts its own Agastya server on :8102 (DATA_DIR=data/demo_run/server_b/data), plays the citizen in a recorded
Chromium (fake mic fed with Sarvam TTS), answers ask_user cards, completes replica login/captcha/OTP and the final
submit/e-verify THROUGH the live canvas (the agent is blocked from those by design). The agent's own Chrome is only
read over CDP (plus: demo_autopilot localStorage flag, captcha refresh, scrollIntoView so targets are on screen).
"""
from __future__ import annotations

import asyncio, json, os, re, shutil, subprocess, sys, time, wave
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/demo_run"
SERVER_DATA = OUT / "server_b/data"
PORT = int(os.environ.get("AUTOPILOT_PORT", "8102"))
BASE = f"http://127.0.0.1:{PORT}"
PDF = ROOT / "demo_portals/samples/form16_demo_test_kumar.pdf"

PROFILE = dict(full_name="Test Kumar Demo", father_name="Sample Prasad Demo", mother_name="Sample Devi Demo",
               date_of_birth="01/01/1990", gender="male", mobile="9000000001", email="test.agastya@example.com",
               pan="ABCDE1234F", address_line1="123 Test Marg", address_line2="Connaught Place", city="New Delhi",
               district="New Delhi", state="Delhi", pincode="110001")

SCENES = {
    "itr": dict(user="demo", lang="hi-IN", portal="/mock/itr",
                line="मुझे डेमो पोर्टल पर अपना आयकर रिटर्न भरना है, मेरे पास फॉर्म 16 है",
                filling_page="filing.html", timeout=1500),
    "voter": dict(user="demo_ta", lang="ta-IN", portal="/mock/voter",
                  line="எனக்கு டெமோ போர்ட்டலில் புதிய வாக்காளர் அடையாள அட்டை வேண்டும்",
                  filling_page="form6.html", timeout=1500),
}

# Citizen actions on the replica during a handoff, in priority order: (selector, kind)
CITIZEN_STEPS = {
    "itr": [
        ("#btn_header_login", "click"),  # agent is blocked from 'Login' links (stop_before) -> citizen opens login page
        ("#login_captcha", "captcha"), ("#btn_login_step1_continue", "click"),
        ("#secure_message_confirm", "check"), ("#password", "password"), ("#btn_login", "click"),
        ("#login_otp", "otp"), ("#btn_submit_login_otp", "click"),
        ("#declaration_agree", "check"), ("#btn_proceed_validation", "click"),
        ("#btn_proceed_verification", "click"), ("#btn_continue_verification", "click"),
        ("#everify_otp", "otp"), ("#btn_everify_submit", "click"),
    ],
    "voter": [
        ("#mobile_number", "mobile"), ("#login_captcha", "captcha"), ("#btn-request-otp", "click"),
        ("#login_otp", "otp"), ("#btn-verify-login", "click"),
        ("#submit_captcha", "captcha"), ("#btn-final-submit", "click"),
    ],
}


def log(scene, msg):
    print(f"[{time.strftime('%H:%M:%S')}] [{scene}] {msg}", flush=True)


# ---------------------------------------------------------------- server / profile
def seed_profiles():
    os.environ["DATA_DIR"] = str(SERVER_DATA)
    from agastya.profile.store import ProfileStore
    SERVER_DATA.mkdir(parents=True, exist_ok=True)
    for sc in SCENES.values():
        st = ProfileStore(sc["user"], data_dir=SERVER_DATA)
        for k, v in PROFILE.items():
            try:
                st.set(k, v, source="user")
            except Exception as e:  # validator mismatch -> report, keep going
                print("profile seed failed", k, e)


def start_server():
    env = dict(os.environ, DEMO_BASE_URL=BASE, HEADLESS="true", DATA_DIR=str(SERVER_DATA))
    logf = open(OUT / "server_b/server.log", "w")
    proc = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "agastya", "serve", "--port", str(PORT)],
                            cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
    import urllib.request
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/", timeout=2)
            return proc
        except Exception:
            time.sleep(1)
    raise RuntimeError("server did not start")


# ---------------------------------------------------------------- voice
async def make_wav(text, lang, dest: Path) -> float:
    from agastya.voice.tts import SarvamTTS
    raw = dest.with_suffix(".raw.wav")
    raw.write_bytes(await SarvamTTS().synthesize(text, lang))
    # 0.6 s lead-in silence, 16 kHz mono, 2.5 s trailing silence (the fake device loops the file)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", "adelay=600,apad=pad_dur=2.5",
                    "-ar", "16000", "-ac", "1", str(dest)], check=True)
    with wave.open(str(dest)) as w:
        return w.getnframes() / w.getframerate()


# ---------------------------------------------------------------- agent chrome over CDP
async def find_portal(p, prefix, cache):
    """Return (browser, page) of the agent Chrome page whose URL starts with BASE+prefix."""
    if cache.get("page") is not None and not cache["page"].is_closed() and cache["page"].url.startswith(BASE + prefix):
        return cache["page"]
    ps = subprocess.run(["pgrep", "-af", "remote-debugging-port"], capture_output=True, text=True).stdout
    for port in reversed(re.findall(r"--remote-debugging-port=(\d+)", ps)):
        try:
            br = cache.get(port) or await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
            cache[port] = br
            pages = [pg for c in br.contexts for pg in c.pages if pg.url.startswith(BASE + prefix)]
            if pages:
                cache["page"] = pages[-1]
                return pages[-1]
        except Exception:
            cache.pop(port, None)
    return None


SNAP_JS = """() => {
  const vis = e => !!(e.offsetParent || e.getClientRects().length);
  const els = [...document.querySelectorAll('input,select,textarea')].filter(e => e.type !== 'hidden');
  const changed = els.filter(e => (e.type === 'checkbox' || e.type === 'radio') ? e.checked !== e.defaultChecked
        : (e.tagName === 'SELECT' ? [...e.options].some(o => o.selected !== o.defaultSelected) : e.value !== e.defaultValue));
  const filled = els.filter(e => (e.type === 'checkbox' || e.type === 'radio') ? false : (e.value || '').trim() !== '');
  const v = id => { const e = document.getElementById(id); return e ? e.value : null; };
  return {url: location.href, changed: changed.length, filled: filled.length, total: els.length,
          changed_ids: changed.map(e => e.id || e.name).slice(0, 80),
          values: {salary_17_1: v('salary_17_1'), perquisites_17_2: v('perquisites_17_2'), gross_salary: v('gross_salary'),
                   tds_amount_0: v('tds_amount_0'), total_tax_paid: v('total_tax_paid'), deduction_80c: v('deduction_80c'),
                   tax_liability_total: v('tax_liability_total'), refund_amount: v('refund_amount'),
                   disp_new: document.getElementById('disp_new_liability')?.innerText || null,
                   disp_old: document.getElementById('disp_old_liability')?.innerText || null,
                   state: v('state'), district: v('district'), assembly_constituency: v('assembly_constituency'),
                   first_name: v('first_name'), dob: v('dob'), taxpayer_name: v('taxpayer_name'), mobile: v('mobile')},
          ack: document.getElementById('ack_number')?.innerText || document.getElementById('ack-reference-number')?.innerText || null};
}"""


# ---------------------------------------------------------------- scene
class Scene:
    def __init__(self, name, attempt):
        self.name, self.cfg, self.attempt = name, SCENES[name], attempt
        self.dir = OUT / name
        self.shots = self.dir / "screenshots"
        self.vdir = self.dir / f"video_raw_{attempt}"
        for d in (self.shots, self.vdir):
            d.mkdir(parents=True, exist_ok=True)
        self.events, self.t0 = [], None
        self.timings, self.questions, self.handoffs, self.blocked = {}, [], [], []
        self.notes, self.taken = [], set()
        self.snap_best, self.ack = None, None
        self.cdp = {}
        self.vt0, self.vmarks = None, {}

    def t(self):
        return round(time.time() - self.t0, 1) if self.t0 else None

    def mark(self, key):
        if key not in self.timings:
            self.timings[key] = self.t()

    def on_frame(self, payload):
        try:
            ev = json.loads(payload)
        except Exception:
            return
        typ, data = ev.get("type"), ev.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        rec = {"t": self.t(), "type": typ}
        if typ == "assistant_text":
            self.mark("first_assistant_text"); rec["text"] = data.get("text")
        elif typ == "tts":
            self.mark("first_tts_audio"); rec["text"] = (data.get("text") or "")[:200]
        elif typ == "transcript":
            self.mark("transcript"); rec.update(text=data.get("text"), language_code=data.get("language_code"))
        elif typ == "ask_user":
            rec["data"] = {k: v for k, v in data.items() if k != "png_b64"}
        elif typ == "blocked":
            rec["data"] = data; self.blocked.append(rec)
        elif typ == "tool_call":
            rec["data"] = {k: (str(v)[:300]) for k, v in data.items()}
        elif typ == "status":
            rec["data"] = data
            if data.get("handoff") or data.get("status") == "handoff":
                self.handoffs.append({"t": self.t(), "reason": data.get("reason")})
        elif typ in ("screenshot",):
            return
        else:
            rec["data"] = {k: str(v)[:200] for k, v in data.items()}
        self.events.append(rec)

    async def shot(self, ui, name, force=False):
        if name in self.taken and not force:
            return
        self.taken.add(name)
        await ui.screenshot(path=str(self.shots / f"{name}.png"))
        if getattr(self, "vt0", None):
            self.vmarks[name] = round(time.time() - self.vt0, 2)
        log(self.name, f"screenshot {name} @ {self.t()}s")

    # ----- answers to ask_user cards
    def answer(self, q):
        ql = q.lower()
        if self.name == "itr":
            if re.search(r"regime|व्यवस्था|रिजीम|रेजीम", ql):
                return "नई टैक्स व्यवस्था"
            if re.search(r"upload|अपलोड|फॉर्म 16|form 16", ql):
                return "मैंने फॉर्म 16 अपलोड कर दिया है, कृपया उसे पढ़ लीजिए"
            if re.search(r"employ|नौकरी|रोजगार|नियोक्ता", ql):
                return "मैं प्राइवेट कंपनी में नौकरी करता हूँ"
            if re.search(r"aadhaar|आधार", ql):
                return "आधार मैं खुद पोर्टल पर डालूँगा, आप आगे बढ़िए"
            if re.search(r"place|स्थान|जगह", ql):
                return "नई दिल्ली"
            return "हाँ, सही है, आगे बढ़िए"
        if re.search(r"constituency|தொகுதி|निर्वाचन|assembly", ql):
            return "40 - New Delhi தொகுதி"
        if re.search(r"aadhaar|ஆதார்|आधार", ql):
            return "ஆதார் எண் இல்லை, அதை விட்டுவிடுங்கள்"
        if re.search(r"proof|ஆவண|சான்று|document|प्रमाण", ql):
            return "பிறப்புச் சான்றிதழ்"
        if re.search(r"disab|மாற்றுத்திறன்|ऊन", ql):
            return "இல்லை, எனக்கு எந்த மாற்றுத்திறனும் இல்லை"
        if re.search(r"place|இடம்|village|கிராம|town", ql):
            return "புது டெல்லி"
        if re.search(r"relat|தந்தை|father|உறவு", ql):
            return "தந்தை - Sample Prasad Demo"
        return "ஆம், சரி, தொடருங்கள்"

    # ----- citizen work through the live canvas
    async def canvas_point(self, ui, portal, sel):
        info = await portal.evaluate("""(sel) => { const e = document.querySelector(sel); if (!e) return null;
            e.scrollIntoView({block: 'center', inline: 'center'}); const r = e.getBoundingClientRect();
            return [r.x + r.width / 2, r.y + r.height / 2, innerWidth, innerHeight]; }""", sel)
        if not info:
            return None
        await ui.wait_for_timeout(700)  # let the scrolled frame reach the stream
        info = await portal.evaluate("""(sel) => { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect();
            return [r.x + r.width / 2, r.y + r.height / 2, innerWidth, innerHeight]; }""", sel)
        c = await ui.locator("#live-canvas").bounding_box()
        if not info or not c:
            self.notes.append(f"canvas_point failed for {sel}: info={info} canvas={c}")
            return None
        return c["x"] + info[0] / info[2] * c["width"], c["y"] + info[1] / info[3] * c["height"]

    async def citizen_handoff(self, p, ui):
        n = len(self.handoffs) or 1
        log(self.name, f"handoff #{n}: {(await ui.inner_text('#live-handoff-banner')).strip()[:150]!r}")
        self.mark(f"handoff_{n}")
        await self.shot(ui, "04_handoff")
        portal = await find_portal(p, self.cfg["portal"], self.cdp)
        if portal is None:
            self.notes.append("handoff: portal page not found over CDP")
            return
        await portal.evaluate("() => { try { localStorage.setItem('demo_autopilot', '1'); } catch (e) {} }")
        await ui.click("#live-control-btn", force=True)
        await ui.wait_for_timeout(1500)
        done_buttons, url0 = set(), portal.url
        for _ in range(30):
          try:
            portal = await find_portal(p, self.cfg["portal"], self.cdp)
            if portal is None:
                break
            if portal.url != url0:
                url0, done_buttons = portal.url, set()
                await ui.wait_for_timeout(1500)
            if "ack" in portal.url:
                break
            acted = False
            for sel, kind in CITIZEN_STEPS[self.name]:
                st = await portal.evaluate("""(sel) => { const e = document.querySelector(sel);
                    if (!e || !(e.offsetParent || e.getClientRects().length) || e.disabled) return null;
                    return {value: e.value, checked: e.checked}; }""", sel)
                if st is None:
                    continue
                if kind == "click":
                    if sel in done_buttons:
                        continue
                    done_buttons.add(sel)
                elif kind == "check":
                    if st["checked"]:
                        continue
                elif st["value"]:
                    continue
                value = None
                if kind == "captcha":
                    await portal.evaluate("""(sel) => { const inp = document.querySelector(sel);
                        let el = inp; let btn = null;
                        while (el && !btn) { btn = [...(el.parentElement?.querySelectorAll('.demo-captcha button') || [])][0]; el = el.parentElement; }
                        if (btn) btn.click(); }""", sel)
                    await ui.wait_for_timeout(400)
                    value = await portal.evaluate("window.__demoCaptchaAnswer || ''")
                elif kind == "otp":
                    value = await portal.evaluate("document.querySelector('.demo-sms .demo-sms-otp')?.innerText.trim() || ''")
                elif kind == "password":
                    value = "Demo@2026"
                elif kind == "mobile":
                    value = PROFILE["mobile"]
                if kind in ("captcha", "otp") and not value:
                    continue
                pt = await self.canvas_point(ui, portal, sel)
                if not pt:
                    continue
                await ui.mouse.click(*pt)
                await ui.wait_for_timeout(500)
                if value is not None:
                    await ui.keyboard.type(value, delay=90)
                    await ui.wait_for_timeout(900)
                    if kind == "otp":
                        await self.shot(ui, "05_citizen_login_otp")
                log(self.name, f"citizen {kind} {sel} {'' if value is None or kind == 'password' else value}")
                acted = True
                await ui.wait_for_timeout(1300)
                break
            if not acted:
                break
          except Exception as e:
            import traceback; traceback.print_exc()
            self.notes.append(f"citizen step error: {type(e).__name__}: {e}"[:300])
            await ui.wait_for_timeout(1500)
        await ui.wait_for_timeout(1000)
        portal = await find_portal(p, self.cfg["portal"], self.cdp)
        if portal is not None and "ack" in portal.url:
            await ui.wait_for_timeout(2500)
            await self.capture_ack(ui, portal)
        # give control back ("Done")
        await ui.click("#live-control-btn", force=True)
        await ui.wait_for_timeout(2500)

    async def capture_ack(self, ui, portal):
        snap = await portal.evaluate(SNAP_JS)
        self.ack = snap.get("ack")
        self.mark("ack")
        await self.shot(ui, "07_acknowledgement")
        log(self.name, f"ACK {self.ack}")

    async def snapshot(self, p, ui):
        portal = await find_portal(p, self.cfg["portal"], self.cdp)
        if portal is None:
            return None
        try:
            snap = await portal.evaluate(SNAP_JS)
        except Exception:
            return None
        if self.cfg["filling_page"] in snap["url"]:
            if not self.snap_best or snap["changed"] >= self.snap_best["changed"]:
                self.snap_best = snap
            if snap["changed"] >= 3:
                await self.shot(ui, "03_agent_filling")
            if self.name == "itr":
                tab = await portal.evaluate("document.querySelector('.itr-tab-btn.active')?.id || ''")
                if "liability" in tab:
                    await ui.wait_for_timeout(1200)
                    await self.shot(ui, "06_tax_computation")
            elif snap["changed"] >= 15:
                await self.shot(ui, "06_sections_filled")
        return snap

    async def run(self, p):
        cfg = self.cfg
        wav = self.dir / "request.wav"
        dur = await make_wav(cfg["line"], cfg["lang"], wav)
        b = await p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                          f"--use-file-for-fake-audio-capture={wav}", "--autoplay-policy=no-user-gesture-required"])
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(self.vdir),
                                  record_video_size={"width": 1440, "height": 900}, permissions=["microphone"])
        ui = await ctx.new_page()
        self.vt0 = time.time()
        ui.on("websocket", lambda ws: ws.on("framereceived", self.on_frame))
        errors = []
        ui.on("pageerror", lambda e: errors.append(str(e)[:200]))
        try:
            await ui.goto(f"{BASE}/?user_id={cfg['user']}")
            await ui.wait_for_function("document.body.innerText.includes('Ready')", timeout=120000)
            await ui.wait_for_timeout(1500)
            self.t0 = time.time()
            if self.name == "itr":
                async with ui.expect_file_chooser() as fc:
                    await ui.click("#attach-btn")
                await (await fc.value).set_files(str(PDF))
                try:
                    await ui.wait_for_function("document.body.innerText.toLowerCase().includes('form16') || document.body.innerText.includes('Form 16')", timeout=30000)
                except Exception:
                    self.notes.append("upload chip not detected within 30s")
                await ui.wait_for_timeout(2500)
                self.mark("form16_uploaded")
                await self.shot(ui, "02_form16_uploaded")
            # voice request
            await ui.click("#mic-btn")
            self.mark("request")
            await ui.wait_for_timeout(int((dur + 1) * 1000))
            await ui.click("#mic-btn")
            for _ in range(40):
                if "transcript" in self.timings:
                    break
                await ui.wait_for_timeout(500)
            if "transcript" not in self.timings:
                self.notes.append("voice transcript not received in 20s -> typed fallback")
                await ui.fill("#chat-input", cfg["line"]); await ui.click("#send-btn")
            await ui.wait_for_timeout(1500)
            await self.shot(ui, "01_request")

            answered, last_handoff_seen = 0, False
            deadline = time.time() + cfg["timeout"]
            while time.time() < deadline:
                await ui.wait_for_timeout(1500)
                cards = ui.locator(".ask-user-card")
                inputs = ui.locator(".ask-user-card input:visible")
                if await inputs.count() > 0 and await cards.count() > answered:
                    q = (await cards.nth(await cards.count() - 1).inner_text()).strip().replace("\n", " ")[:300]
                    a = self.answer(q)
                    if self.name == "voter" and not self.questions:
                        await self.shot(ui, "02_question")
                    self.questions.append({"t": self.t(), "question": q, "answer": a})
                    log(self.name, f"Q: {q[:120]!r} -> {a}")
                    if self.name == "itr" and re.search(r"regime|व्यवस्था|रिजीम", q.lower()):
                        await self.snapshot(p, ui)
                        await self.shot(ui, "06_tax_computation")
                    await inputs.nth(0).fill(a)
                    await ui.wait_for_timeout(600)
                    await inputs.nth(0).press("Enter")
                    answered = await cards.count()
                    continue
                snap = await self.snapshot(p, ui)
                if snap and ("ack" in snap["url"]):
                    await self.capture_ack(ui, await find_portal(p, cfg["portal"], self.cdp))
                    break
                visible = await ui.locator("#live-handoff-banner:not(.hidden)").count() > 0
                pulsing = await ui.locator("#live-control-btn.pulse-attention").count() > 0
                if (visible or pulsing) and not last_handoff_seen:
                    await self.citizen_handoff(p, ui)
                    last_handoff_seen = True
                    handled_at = time.time()
                    if self.ack:
                        break
                elif not visible and not pulsing:
                    last_handoff_seen = False
                elif last_handoff_seen and time.time() - handled_at > 25:
                    last_handoff_seen = False  # still/again asking -> handle again
            await ui.wait_for_timeout(6000)  # let the agent speak its closing line
            if self.ack:
                await self.shot(ui, "08_final_chat", force=True)
        finally:
            self.ui_errors = errors
            await ctx.close()
            await b.close()
            for br in [v for k, v in self.cdp.items() if k != "page"]:
                try:
                    await br.close()
                except Exception:
                    pass
        return self.ack is not None

    def write_summary(self, success):
        tr = [e for e in self.events if e["type"] == "transcript"]
        texts = [e for e in self.events if e["type"] == "assistant_text"]
        tools = [e for e in self.events if e["type"] == "tool_call"]
        s = {
            "scene": self.name, "attempt": self.attempt, "success": success, "language": self.cfg["lang"],
            "voice_line": self.cfg["line"], "timings_s": self.timings,
            "transcript": tr[0].get("text") if tr else None, "detected_language": tr[0].get("language_code") if tr else None,
            "questions": self.questions, "handoffs": self.handoffs, "blocked_events": self.blocked,
            "ack_number": self.ack,
            "fields_changed_by_agent_or_citizen": (self.snap_best or {}).get("changed"),
            "fields_nonempty": (self.snap_best or {}).get("filled"), "fields_total": (self.snap_best or {}).get("total"),
            "changed_field_ids": (self.snap_best or {}).get("changed_ids"),
            "portal_values": (self.snap_best or {}).get("values"),
            "assistant_texts": [{"t": e["t"], "text": e.get("text")} for e in texts],
            "tool_calls": [{"t": e["t"], **e.get("data", {})} for e in tools][:200],
            "video_marks_s": self.vmarks,
            "ui_errors": getattr(self, "ui_errors", []), "notes": self.notes,
        }
        if self.name == "itr":
            from agastya.documents.form16 import extract_form16
            f16 = extract_form16(PDF.read_bytes())
            v = s["portal_values"] or {}
            s["form16_pdf"] = {k: f16.get(k) for k in ("gross_salary", "salary_17_1", "perquisites_17_2", "tds_total", "deduction_80c")}
            s["form16_used_on_portal"] = {"gross_salary": v.get("gross_salary"), "salary_17_1": v.get("salary_17_1"),
                                          "tds_amount_0": v.get("tds_amount_0"), "total_tax_paid": v.get("total_tax_paid")}
            s["tds_matches_pdf"] = str(v.get("tds_amount_0")) == str(f16.get("tds_total"))
            s["regime_comparison_spoken"] = [e.get("text") for e in texts if re.search(r"regime|व्यवस्था|रिजीम|रेजीम", (e.get("text") or "").lower())]
            s["used_document_tools"] = sorted({t.get("data", {}).get("name", "") for t in tools if "document" in str(t.get("data", {}))})
        (self.dir / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2))
        (self.dir / "events.json").write_text(json.dumps(self.events, ensure_ascii=False, indent=1))


def encode(scene_dir: Path, raw_dir: Path, name: str, gif_start: float | None):
    vids = sorted(raw_dir.glob("*.webm"), key=lambda f: f.stat().st_size)
    if not vids:
        return
    src = vids[-1]
    mp4 = scene_dir / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
                    "-movflags", "+faststart", "-pix_fmt", "yuv420p", str(mp4)], check=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                               capture_output=True, text=True).stdout.strip() or 0)
    start = max(0.0, min(gif_start if gif_start is not None else 0, dur - 20))
    gif = scene_dir / f"{name}_highlight.gif"
    for fps, length in ((8, 20), (6, 16), (5, 15)):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.1f}", "-t", str(length), "-i", str(mp4), "-vf",
                        f"fps={fps},scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer",
                        str(gif)], check=True)
        if gif.stat().st_size < 6 * 1024 * 1024:
            break


def make_edit(sc):
    """Cut a 20-40 s clip around the key moments (screenshot marks), waits removed."""
    mp4 = sc.dir / f"{sc.name}_demo.mp4"
    if not mp4.exists() or not sc.vmarks:
        return
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                               capture_output=True, text=True).stdout.strip() or 0)
    # (mark, seconds before, seconds after, speed)
    plan = [("02_form16_uploaded", 4, 1.5, 1.5), ("01_request", 6, 2, 2.0), ("02_question", 1, 3, 1.0),
            ("03_agent_filling", 1, 8, 2.5), ("04_handoff", 0.5, 3, 1.0), ("05_citizen_login_otp", 8, 2, 2.0),
            ("06_tax_computation", 1, 4, 1.0), ("06_sections_filled", 1, 6, 2.0), ("07_acknowledgement", 3, 4, 1.0)]
    segs = []
    for mark, before, after, speed in plan:
        if mark in sc.vmarks:
            a, b = max(0, sc.vmarks[mark] - before), min(dur, sc.vmarks[mark] + after)
            if segs and a < segs[-1][1]:
                a = segs[-1][1]
            if b - a > 0.5:
                segs.append((a, b, speed))
    segs.sort()
    if not segs:
        return
    parts, labels = [], []
    for i, (a, b, sp) in enumerate(segs):
        parts.append(f"[0:v]trim={a:.2f}:{b:.2f},setpts=(PTS-STARTPTS)/{sp},fps=25[v{i}]")
        labels.append(f"[v{i}]")
    fc = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=1:a=0,scale=1440:900[out]"
    out = sc.dir / f"scene_{sc.name}_edit.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-filter_complex", fc, "-map", "[out]",
                    "-c:v", "libx264", "-crf", "24", "-preset", "veryfast", "-movflags", "+faststart", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    log(sc.name, f"edit clip {out} from {len(segs)} segments")


async def run_scene(p, name):
    for attempt in range(1, int(os.environ.get("AUTOPILOT_ATTEMPTS", "2")) + 1):  # first try + max 1 retry
        sc = Scene(name, attempt)
        ok = False
        try:
            ok = await sc.run(p)
        except Exception as e:
            import traceback; traceback.print_exc()
            sc.notes.append(f"exception: {type(e).__name__}: {e}"[:500])
            log(name, f"attempt {attempt} failed: {e}")
        sc.write_summary(ok)
        # highlight: start a few seconds before the first handoff
        hstart = None
        h = sc.timings.get("handoff_1")
        if h is not None:
            # video starts ~ when page opened; t0 is set after 'Ready' -> offset unknown, approximate with first file mtime
            hstart = max(0.0, h - 3)
        try:
            hstart = sc.vmarks.get("04_handoff", hstart + 0 if hstart else 0) - 3 if sc.vmarks.get("04_handoff") else hstart
            encode(sc.dir, sc.vdir, f"{name}_demo", hstart)
            make_edit(sc)
        except Exception as e:
            log(name, f"encode failed: {e}")
        if ok:
            return True
        log(name, f"attempt {attempt} did not reach the acknowledgement")
    return False


async def main(which):
    seed_profiles()
    server = start_server()
    log("main", f"server up on {BASE}")
    try:
        async with async_playwright() as p:
            names = ["itr", "voter"] if which == "both" else [which]
            res = await asyncio.gather(*(run_scene(p, n) for n in names))
            log("main", f"results {dict(zip(names, res))}")
    finally:
        os.killpg(server.pid, 15)
        try:
            server.wait(10)
        except Exception:
            os.killpg(server.pid, 9)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "both"))
