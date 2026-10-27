"""Recorded demo autopilot: PAN 49A (Hindi voice), MCA SPICe+ name reservation (English voice), mobile shots.

Runs ONLY against the local DEMO REPLICA portals served by our own server (/mock/...). The script plays the
citizen: speaks the request, answers question cards, and on handoff acts through the live view canvas
(consent, captcha, OTP, final submit) — exactly what a real user would do. The agent's Chrome is only
inspected over a read-only CDP connection.

Usage: .venv/bin/python scripts/autopilot/pan_mca.py [pan] [mca] [mobile]
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (ROOT, make_edit, Canvas, EventLog, count_filled, duration, enable_autopilot_captcha, find_portal,  # noqa: E402
                    give_back, log, make_voice_wav, seed_profile, speak_or_type, start_server, stop_server,
                    take_control, to_gif, to_mp4, wait_ready)
from playwright.async_api import async_playwright  # noqa: E402

PORT = 8101
BASE = f"http://127.0.0.1:{PORT}"
RUN = ROOT / "data/demo_run"
DATA_DIR = (RUN / "server_a/data").resolve()

SCENES = {
    "pan": {
        "line": "मुझे डेमो पोर्टल पर नया पैन कार्ड बनवाना है", "lang": "hi-IN",
        "prefix": f"{BASE}/mock/pan", "ack": "/mock/pan/ack.html", "max_s": 900,
    },
    "mca": {
        "line": "I want to reserve a name for my new company on the demo portal", "lang": "en-IN",
        "prefix": f"{BASE}/mock/mca", "ack": "/mock/mca/ack.html", "max_s": 720,
    },
}


def answer_for(scene: str, q: str, n: int) -> str:
    ql = q.lower()
    if "aadhaar" in ql or "आधार" in q:
        return "आधार मैं खुद डालूँगा" if scene == "pan" else "I will enter Aadhaar myself"
    if any(k in ql for k in ("business", "activity", "industry", "objects", "nic")) or "व्यवसाय" in q:
        return "software for citizen services"
    if "name" in ql and scene == "mca":
        return "Agastya Civic Tech Private Limited and Sahayak Digital Services Private Limited"
    if "occupation" in ql or "व्यवसाय" in q or "पेशा" in q:
        return "प्राइवेट नौकरी (Salaried)"
    if "income" in ql or "आय" in q:
        return "वेतन (Salary), सालाना 6 लाख"
    if "din" in ql or "pan" in ql and scene == "mca":
        return "I don't have a DIN. Use my profile details; PAN I will enter myself if needed."
    if "type" in ql or "class" in ql or "category" in ql:
        return "Private limited company, limited by shares, non-government"
    if "place" in ql or "स्थान" in q:
        return "New Delhi"
    if "documents" in ql or "proof" in ql or "दस्तावेज़" in q or "प्रमाण" in q:
        return "आधार कार्ड ही सब प्रमाण के लिए"
    return "हाँ, आगे बढ़िए" if scene == "pan" else "Yes, please go ahead"


# ---------------------------------------------------------------- citizen actions in the live view
async def citizen_pan(cv: Canvas, portal, notes: list) -> str:
    url = portal.url
    if "register.html" in url:
        if await portal.evaluate("document.getElementById('token-modal')?.classList.contains('active')"):
            await cv.click("#btn-continue-form", pause=2500)
            return "register_continue"
        if not await portal.evaluate("document.getElementById('consent')?.checked"):
            if await portal.evaluate("!!document.getElementById('consent')?.offsetParent"):
                await cv.click("#consent")
        await cv.click("#captcha-container .demo-captcha button")  # ↻ Refresh -> answer exposed for autopilot
        ans = await portal.evaluate("window.__demoCaptchaAnswer || ''")
        await cv.type_into("#captcha_input", ans)
        await cv.click("#btn-submit", pause=2000)
        return "register_submitted"
    if "form49a.html" in url:
        if await portal.evaluate("!!document.getElementById('aadhaar') && !document.getElementById('aadhaar').value"
                                 " && !!document.getElementById('aadhaar').offsetParent"):
            await cv.type_into("#aadhaar", "999900001111")
            notes.append("citizen typed a dummy Aadhaar 999900001111 in the live view")
            return "aadhaar_typed"
        return "form49a_noop"
    if "preview.html" in url:
        await cv.click("#btn-proceed-pay", pause=2500)
        await portal.wait_for_load_state()
        await enable_autopilot_captcha(portal)
        return "proceed_pay+" + await citizen_pan(cv, portal, notes)
    if "payment.html" in url:
        if not await portal.evaluate("document.getElementById('btn-mock-pay').disabled"):
            await cv.click("#btn-mock-pay", pause=1800)
        if not await portal.evaluate("document.getElementById('esign-consent').checked"):
            await cv.click("#esign-consent")
        if not await portal.evaluate("!!document.querySelector('.demo-sms .demo-sms-otp')"):
            await cv.click("#btn-send-otp", pause=1500)
        otp = await portal.evaluate("document.querySelector('.demo-sms .demo-sms-otp')?.textContent.trim() || ''")
        await cv.type_into("#otp_input", otp)
        await cv.ui.screenshot(path=str(cv.out / "05_citizen_captcha_otp.png"))
        await cv.click("#btn-authenticate-submit", pause=3500)
        return "authenticated"
    return "unknown"


async def citizen_mca(cv: Canvas, portal, notes: list) -> str:
    if "spice.html" not in portal.url:
        return "unknown"
    modal_open = await portal.evaluate(
        "(() => { const o = document.getElementById('spice_otp'); return !!(o && o.offsetParent); })()")
    if not modal_open:
        await cv.click("#btn-submit-name", pause=1800)
        modal_open = await portal.evaluate(
            "(() => { const o = document.getElementById('spice_otp'); return !!(o && o.offsetParent); })()")
        if not modal_open:
            err = await portal.evaluate("document.body.innerText.match(/(error|required|please)[^\\n]{0,120}/i)?.[0] || ''")
            notes.append(f"submit modal did not open: {err}")
            return "submit_blocked"
    if not await portal.evaluate("window.__demoCaptchaAnswer || ''"):
        await cv.click("#spice-captcha-box .demo-captcha button")
    otp = await portal.evaluate("document.querySelector('.demo-sms .demo-sms-otp')?.textContent.trim() || ''")
    await cv.type_into("#spice_otp", otp)
    await cv.type_into("#spice_captcha", await portal.evaluate("window.__demoCaptchaAnswer || ''"))
    await cv.ui.screenshot(path=str(cv.out / "05_citizen_captcha_otp.png"))
    await cv.click("#btn-confirm-submit", pause=3500)
    return "confirmed"


# ---------------------------------------------------------------- scene
async def run_scene(p, scene: str, attempt: int) -> dict:
    cfg = SCENES[scene]
    out = RUN / scene
    vid_dir = out / f"video_raw_{attempt}"
    shutil.rmtree(vid_dir, ignore_errors=True)
    wav = out / "voice_request.wav"
    wav_ok, clip = True, 0.0
    try:
        clip = make_voice_wav(cfg["line"], cfg["lang"], wav)
    except Exception as e:  # noqa: BLE001
        wav_ok = False
        log("TTS failed:", e)
    args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
    if wav_ok:
        args.append(f"--use-file-for-fake-audio-capture={wav.resolve()}")
    browser = await p.chromium.launch(args=args)
    ctx = await browser.new_context(record_video_dir=str(vid_dir), record_video_size={"width": 1440, "height": 900},
                                    viewport={"width": 1440, "height": 900}, permissions=["microphone"])
    ui = await ctx.new_page()
    rec_start = time.time()
    marks: list = []

    def mark(name, hold=4.0, speed=1.0):
        marks.append((name, round(time.time() - rec_start, 2), hold, speed))
    errors: list[str] = []
    ui.on("pageerror", lambda e: errors.append(str(e)[:200]))
    ev = EventLog()
    ev.attach(ui)
    await ui.goto(f"{BASE}/?user_id=demo")
    await wait_ready(ui)
    await ui.wait_for_timeout(1500)
    ev.t0 = time.time()
    t = {}
    notes: list[str] = []
    t["request_sent"] = 0.0
    mark("request", 2.0, 1.0)
    req = await speak_or_type(ui, cfg["line"], wav_ok, clip, ev)
    if req["mode"] == "typed":
        notes.append("voice request failed -> typed fallback")
    await ui.wait_for_timeout(1500)
    await ui.screenshot(path=str(out / "01_request.png"))
    mark("transcript", 3.0, 1.0)

    questions, cache, actions = [], {}, []
    answered, shot_fill, shot_handoff, success, portal = 0, False, False, False, None
    max_filled, ack_no, gif_start = 0, None, None
    deadline = time.time() + cfg["max_s"]
    handoffs = 0
    handled_ev = 0
    while time.time() < deadline:
        await ui.wait_for_timeout(1500)
        cards = ui.locator(".ask-user-card input:visible")
        if await cards.count():
            q = (await ui.locator(".ask-user-card").last.inner_text())[:300].replace("\n", " ")
            if answered == 0:
                await ui.screenshot(path=str(out / "02_question.png"))
            mark(f"question{answered}", 2.5, 1.0)
            a = answer_for(scene, q, answered)
            t.setdefault("first_question_shown", round(time.time() - ev.t0, 1))
            questions.append({"t": round(time.time() - ev.t0, 1), "question": q, "answer": a})
            log("Q:", q[:120], "->", a)
            await cards.first.fill(a)
            await ui.wait_for_timeout(600)
            await cards.first.press("Enter")
            answered += 1
            await ui.wait_for_timeout(1500)
            continue
        if portal is None or portal.is_closed():
            _, portal = await find_portal(p, cfg["prefix"], cache)
            if portal is not None:
                try:
                    await enable_autopilot_captcha(portal)
                except Exception:
                    pass
        else:
            try:
                _, portal = await find_portal(p, cfg["prefix"], cache)
                n = await count_filled(portal)
                max_filled = max(max_filled, n)
                if cfg["ack"] in portal.url:
                    success = True
                    break
                if not shot_fill and n >= 4 and await ui.locator("#live-handoff-banner.hidden").count():
                    await ui.screenshot(path=str(out / "03_agent_filling.png"))
                    shot_fill = True
                    mark("filling", 10.0, 2.0)
                    gif_start = time.time() - rec_start - 8
            except Exception as e:  # noqa: BLE001
                log("portal probe:", str(e)[:120])
                portal = None
        # Real handoffs = agent's handoff status events / blocked events. (Taking control ourselves also emits
        # status user_control, which re-shows the banner, so the banner alone is not a reliable signal.)
        n_handoff_ev = len([e for e in ev.events if e.get("type") == "status" and e.get("handoff")]) + len(ev.of("blocked"))
        idle = ev.events and time.time() - ev.t0 - ev.events[-1]["t"] > 25
        stuck_at_citizen_step = portal is not None and idle and any(k in portal.url for k in ("preview.html", "payment.html"))
        if portal is not None and (n_handoff_ev > handled_ev or stuck_at_citizen_step):
            handled_ev = n_handoff_ev
            handoffs += 1
            t.setdefault("handoff", round(time.time() - ev.t0, 1))
            await ui.wait_for_timeout(2500)
            mark(f"handoff{handoffs}", 3.0, 1.0)
            if not shot_handoff:
                await ui.screenshot(path=str(out / "04_handoff.png"))
                shot_handoff = True
            banner = (await ui.inner_text("#live-handoff-banner")).strip().replace("\n", " ")[:200]
            log("HANDOFF", handoffs, portal.url, "|", banner)
            await enable_autopilot_captcha(portal)
            await take_control(ui)
            mark(f"citizen{handoffs}", 0.5, 1.0)
            cv = Canvas(ui, portal)
            cv.out = out
            try:
                act = await (citizen_pan if scene == "pan" else citizen_mca)(cv, portal, notes)
            except Exception as e:  # noqa: BLE001
                act = f"error: {str(e)[:160]}"
            actions.append({"t": round(time.time() - ev.t0, 1), "url": portal.url, "banner": banner, "action": act})
            log("citizen action:", act)
            mark(f"citizen_done{handoffs}", 2.0, 1.0)
            await ui.wait_for_timeout(1500)
            _, portal = await find_portal(p, cfg["prefix"], cache)
            if portal is not None and cfg["ack"] in portal.url:
                success = True
                break
            await give_back(ui)
            await ui.wait_for_timeout(4000)
            if handoffs > 8:
                notes.append("too many handoffs; stopping")
                break
    if success:
        t["ack"] = round(time.time() - ev.t0, 1)
        await ui.wait_for_timeout(4000)
        try:
            if scene == "pan":
                ack_no = await portal.evaluate("document.getElementById('ack-number')?.textContent.trim()")
            else:
                ack_no = await portal.evaluate("document.getElementById('ack-srn-badge')?.textContent.trim()")
        except Exception:
            pass
        await ui.screenshot(path=str(out / "06_acknowledgement.png"))
        mark("ack", 4.0, 1.0)
        try:
            await give_back(ui)
            await ui.wait_for_timeout(5000)
        except Exception:
            pass
    else:
        await ui.screenshot(path=str(out / f"failed_attempt{attempt}.png"))
    t["first_assistant_text"] = ev.first.get("assistant_text")
    t["first_tts_audio"] = ev.first.get("tts")
    t["first_ask_user"] = ev.first.get("ask_user")
    if cache.get("browser"):
        try:
            await cache["browser"].close()
        except Exception:
            pass
    await ctx.close()
    await browser.close()

    webm = sorted(vid_dir.glob("*.webm"))
    mp4 = out / f"{scene}_demo.mp4"
    gif = out / f"{scene}_highlight.gif"
    if webm:
        to_mp4(webm[0], mp4)
        total = duration(mp4)
        start = max(0.0, min(gif_start or total * 0.3, total - 22))
        to_gif(mp4, gif, start, 20.0)
        try:
            make_edit(mp4, marks, out / f"scene_{scene}_edit.mp4", total)
        except Exception as e:  # noqa: BLE001
            notes.append(f"edit failed: {e}")
    summary = {
        "scene": scene, "attempt": attempt, "success": success, "ack_number": ack_no,
        "timings_s": t, "request": req, "questions": questions, "handoffs": handoffs, "citizen_actions": actions,
        "max_fields_filled_seen": max_filled, "video_marks": marks,
        "blocked_events": ev.of("blocked"), "assistant_texts": [e.get("text") for e in ev.of("assistant_text")],
        "tool_calls": [e.get("name") or e.get("tool") for e in ev.of("tool_call")],
        "ui_page_errors": errors[:10], "notes": notes,
        "files": {"mp4": str(mp4), "gif": str(gif), "video_s": duration(mp4) if mp4.exists() else 0,
                  "gif_bytes": gif.stat().st_size if gif.exists() else 0},
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (out / f"events_attempt{attempt}.json").write_text(json.dumps(ev.events, ensure_ascii=False, indent=1))
    log(scene, "success" if success else "FAILED", "ack", ack_no)
    return summary


async def run_mobile(p) -> None:
    out = RUN / "mobile"
    out.mkdir(parents=True, exist_ok=True)
    browser = await p.chromium.launch()
    ctx = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                    has_touch=True)
    ui = await ctx.new_page()
    await ui.goto(f"{BASE}/?user_id=demo_m")
    await wait_ready(ui)
    await ui.screenshot(path=str(out / "01_mobile_home.png"))
    await ui.fill("#chat-input", "मुझे डेमो पोर्टल पर नया पैन कार्ड बनवाना है")
    await ui.click("#send-btn")
    cards_seen = 0
    deadline = time.time() + 420
    shots = set()
    while time.time() < deadline:
        await ui.wait_for_timeout(1500)
        cards = ui.locator(".ask-user-card input:visible")
        if await cards.count():
            if "q" not in shots:
                await ui.screenshot(path=str(out / "02_mobile_question_card.png"))
                shots.add("q")
            q = await ui.locator(".ask-user-card").last.inner_text()
            await cards.first.fill(answer_for("pan", q, cards_seen))
            await cards.first.press("Enter")
            cards_seen += 1
            continue
        if "q" in shots and "live" not in shots and cards_seen and time.time() > deadline - 360:
            await ui.click("#tab-browser")
            await ui.wait_for_timeout(5000)
            await ui.screenshot(path=str(out / "03_mobile_browser_live_view.png"))
            shots.add("live")
        if "live" in shots and await ui.locator("#live-handoff-banner:not(.hidden)").count():
            await ui.click("#tab-browser")
            await ui.wait_for_timeout(2500)
            await ui.screenshot(path=str(out / "04_mobile_handoff_banner.png"))
            shots.add("handoff")
            break
    await ui.click("#tab-chat")
    await ui.wait_for_timeout(1000)
    await ui.screenshot(path=str(out / "05_mobile_chat.png"))
    (out / "summary.json").write_text(json.dumps({"shots": sorted(shots), "questions": cards_seen}, indent=2))
    await ctx.close()
    await browser.close()


async def main(which):
    async with async_playwright() as p:
        if "mobile" in which and len(which) > 1:
            mob = asyncio.create_task(run_mobile(p))
        else:
            mob = None
        for scene in [s for s in which if s in SCENES]:
            for attempt in range(1, 3):
                try:
                    s = await run_scene(p, scene, attempt)
                    if s["success"]:
                        break
                except Exception as e:  # noqa: BLE001
                    log(scene, "attempt", attempt, "crashed:", e)
        if mob:
            await mob
        elif which == ["mobile"]:
            await run_mobile(p)


if __name__ == "__main__":
    which = sys.argv[1:] or ["pan", "mca", "mobile"]
    for d in ("pan", "mca", "mobile", "server_a"):
        (RUN / d).mkdir(parents=True, exist_ok=True)
    seed_profile(DATA_DIR, "demo")
    seed_profile(DATA_DIR, "demo_m")
    server = start_server(PORT, DATA_DIR, RUN / "server_a/server.log")
    try:
        asyncio.run(main(which))
    finally:
        stop_server(server)
