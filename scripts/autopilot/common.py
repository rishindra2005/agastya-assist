"""Shared helpers for recorded demo autopilot runs (scripts/autopilot/*.py).

The autopilot plays the CITIZEN: it talks to the Agastya UI, answers question cards and, on handoff,
acts through the live view (#live-canvas). The agent's own Chrome is inspected via a READ-ONLY CDP
connection only (to locate elements / read the demo captcha answer + mock OTP on DEMO REPLICA pages).
Never use against real government portals.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv/bin/python")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROFILE = {
    "full_name": "Test Kumar Demo",
    "father_name": "Sample Prasad Demo",
    "mother_name": "Sample Devi Demo",
    "date_of_birth": "01/01/1990",
    "gender": "male",
    "mobile": "9000000001",
    "email": "test.agastya@example.com",
    "address_line1": "123 Test Marg",
    "address_line2": "Connaught Place",
    "city": "New Delhi",
    "district": "New Delhi",
    "state": "Delhi",
    "pincode": "110001",
}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- server
def seed_profile(data_dir: Path, user_id: str = "demo", extra: dict | None = None) -> None:
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    code = (
        "import json,sys\n"
        "from agastya.profile.store import ProfileStore\n"
        "s=ProfileStore(sys.argv[1], data_dir=sys.argv[2])\n"
        "for k,v in json.loads(sys.argv[3]).items(): s.set(k,v,source='user')\n"
        "print('seeded', sys.argv[1])\n"
    )
    vals = dict(PROFILE, **(extra or {}))
    env = dict(os.environ, DATA_DIR=str(data_dir))
    subprocess.run([PY, "-c", code, user_id, str(data_dir), json.dumps(vals)], cwd=ROOT, env=env, check=True)


def start_server(port: int, data_dir: Path, log_path: Path) -> subprocess.Popen:
    data_dir = Path(data_dir).resolve()
    env = dict(os.environ, DEMO_BASE_URL=f"http://127.0.0.1:{port}", HEADLESS="true", DATA_DIR=str(data_dir))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(log_path, "ab")
    proc = subprocess.Popen([PY, "-m", "agastya", "serve", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    import urllib.request
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            log("server up on", port)
            return proc
        except Exception:
            time.sleep(1)
    raise RuntimeError("server did not start")


def stop_server(proc: subprocess.Popen) -> None:
    import signal
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=15)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass


# ---------------------------------------------------------------- voice
def make_voice_wav(text: str, lang: str, out: Path) -> float:
    """Synthesize `text` with Sarvam TTS, resample to 16 kHz mono with 1 s lead silence. Returns duration."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_suffix(".raw.wav")
    if not out.exists():
        raw.write_bytes(_synth(text, lang))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", "adelay=800|800,apad=pad_dur=1",
                        "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", str(out)], check=True)
    with wave.open(str(out)) as w:
        return w.getnframes() / w.getframerate()


def _synth(text: str, lang: str) -> bytes:
    from agastya.voice.tts import SarvamTTS

    async def go():
        return await SarvamTTS().synthesize(text, lang)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


async def synth_voice(text: str, lang: str, out: Path) -> float:
    return await asyncio.to_thread(make_voice_wav, text, lang, out)


# ---------------------------------------------------------------- UI events
class EventLog:
    """Records JSON frames the UI websocket receives (assistant_text, tts, ask_user, blocked, ...)."""

    def __init__(self, t0: float | None = None):
        self.t0 = t0 or time.time()
        self.events: list[dict] = []
        self.first: dict[str, float] = {}

    def attach(self, page):
        def on_ws(ws):
            if "/ws/live" in ws.url:
                return
            ws.on("framereceived", self._frame)
        page.on("websocket", on_ws)

    def _frame(self, payload):
        try:
            d = json.loads(payload)
        except Exception:
            return
        if not isinstance(d, dict):
            return
        t = round(time.time() - self.t0, 2)
        typ = d.get("type", "?")
        self.first.setdefault(typ, t)
        slim = {k: v for k, v in d.items() if k not in ("png_b64", "audio_b64")}
        if typ == "tts":
            slim = {"type": "tts", "text": str(d.get("text", ""))[:200], "seq": d.get("seq")}
        if typ == "screenshot":
            slim = {"type": "screenshot", "url": d.get("url")}
        slim["t"] = t
        self.events.append(slim)

    def of(self, typ):
        return [e for e in self.events if e.get("type") == typ]


async def wait_ready(ui, timeout=90000):
    await ui.wait_for_function(
        "document.body.innerText.includes('Ready') || document.body.innerText.includes('तैयार')", timeout=timeout)


async def speak_or_type(ui, text: str, wav_ok: bool, clip_s: float, events: EventLog) -> dict:
    """Click mic, wait the clip length, click mic again; fall back to typing if no transcript arrives."""
    info = {"mode": "typed", "transcript": None}
    if wav_ok:
        n0 = len(events.of("transcript"))
        await ui.click("#mic-btn")
        await ui.wait_for_timeout(int((clip_s + 1.0) * 1000))
        await ui.click("#mic-btn")
        for _ in range(30):
            await ui.wait_for_timeout(1000)
            tr = events.of("transcript")
            if len(tr) > n0:
                info.update(mode="voice", transcript=tr[-1].get("text"), language=tr[-1].get("language")
                            or tr[-1].get("language_code") or tr[-1].get("lang"), raw=tr[-1])
                return info
            if events.of("assistant_text") or events.of("ask_user"):
                break
        log("no transcript from voice; falling back to typing")
        info["voice_failed"] = True
    await ui.fill("#chat-input", text)
    await ui.click("#send-btn")
    info["transcript"] = text
    return info


# ---------------------------------------------------------------- agent chrome (read-only CDP)
def cdp_ports() -> list[int]:
    ps = subprocess.run(["pgrep", "-af", "remote-debugging-port"], capture_output=True, text=True).stdout
    return [int(x) for x in re.findall(r"--remote-debugging-port=(\d+)", ps)]


async def find_portal(p, url_prefix: str, cache: dict):
    """Return (browser, page) of the agent Chrome whose page URL starts with url_prefix (latest page)."""
    br = cache.get("browser")
    if br is not None:
        try:
            pages = [pg for c in br.contexts for pg in c.pages if pg.url.startswith(url_prefix)]
            if pages:
                return br, pages[-1]
        except Exception:
            cache.pop("browser", None)
    for port in reversed(cdp_ports()):
        try:
            b = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
        except Exception:
            continue
        pages = [pg for c in b.contexts for pg in c.pages if pg.url.startswith(url_prefix)]
        if pages:
            cache["browser"] = b
            return b, pages[-1]
        try:
            await b.close()
        except Exception:
            pass
    return None, None


class Canvas:
    """Maps portal CSS-pixel points onto #live-canvas and acts through it like the citizen."""

    def __init__(self, ui, portal):
        self.ui, self.portal = ui, portal

    async def vp(self):
        return await self.portal.evaluate("[innerWidth, innerHeight]")

    async def to_canvas(self, px, py):
        vw = await self.vp()
        c = await self.ui.locator("#live-canvas").bounding_box()
        return c["x"] + px / vw[0] * c["width"], c["y"] + py / vw[1] * c["height"]

    async def rect(self, sel: str):
        return await self.portal.evaluate(
            "(s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect();"
            " return {x: r.x, y: r.y, w: r.width, h: r.height, vis: !!(e.offsetParent || e.getClientRects().length)}; }", sel)

    async def ensure_visible(self, sel: str):
        for _ in range(8):
            r = await self.rect(sel)
            if not r:
                return None
            vw = await self.vp()
            if r["w"] > 0 and 70 <= r["y"] + r["h"] / 2 <= vw[1] - 40:
                return r
            delta = (r["y"] + r["h"] / 2) - vw[1] / 2
            cx, cy = await self.to_canvas(vw[0] / 2, vw[1] / 2)
            await self.ui.mouse.move(cx, cy)
            await self.ui.mouse.wheel(0, max(-1200, min(1200, delta)))
            await self.ui.wait_for_timeout(900)
        # last resort (still a visible scroll in the stream)
        await self.portal.evaluate("(s) => document.querySelector(s)?.scrollIntoView({block:'center'})", sel)
        await self.ui.wait_for_timeout(900)
        return await self.rect(sel)

    async def click(self, sel: str, pause=700):
        r = await self.ensure_visible(sel)
        if not r:
            raise RuntimeError(f"portal element not found: {sel}")
        x, y = await self.to_canvas(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
        await self.ui.mouse.click(x, y)
        await self.ui.wait_for_timeout(pause)

    async def type_into(self, sel: str, text: str):
        await self.click(sel, pause=300)
        await self.ui.keyboard.press("Control+A")
        await self.ui.keyboard.press("Backspace")
        await self.ui.keyboard.type(text, delay=90)
        await self.ui.wait_for_timeout(600)


async def take_control(ui):
    txt = (await ui.inner_text("#live-control-btn")).lower()
    if "take" in txt or "लें" in txt:
        await ui.click("#live-control-btn", force=True)
        await ui.wait_for_timeout(1000)


async def give_back(ui):
    txt = (await ui.inner_text("#live-control-btn")).lower()
    if not ("take" in txt or "लें" in txt):
        await ui.click("#live-control-btn", force=True)
        await ui.wait_for_timeout(1000)


async def enable_autopilot_captcha(portal):
    await portal.evaluate("localStorage.setItem('demo_autopilot','1')")


async def count_filled(portal) -> int:
    return await portal.evaluate(
        "() => [...document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea')]"
        ".filter(e => (e.value||'').trim()).length")


# ---------------------------------------------------------------- media
def to_mp4(webm: Path, mp4: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264", "-crf", "28",
                    "-preset", "veryfast", "-movflags", "+faststart", "-pix_fmt", "yuv420p", str(mp4)], check=True)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def to_gif(mp4: Path, gif: Path, start: float, length: float = 20.0, width: int = 720, fps: int = 8) -> None:
    pal = gif.with_suffix(".palette.png")
    vf = f"fps={fps},scale={width}:-1:flags=lanczos"
    for w, f in ((width, fps), (640, 7), (560, 6)):
        vf = f"fps={f},scale={w}:-1:flags=lanczos"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t", str(length), "-i", str(mp4),
                        "-vf", vf + ",palettegen=max_colors=128", str(pal)], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t", str(length), "-i", str(mp4),
                        "-i", str(pal), "-lavfi", vf + " [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4", str(gif)],
                       check=True)
        if gif.stat().st_size < 6 * 1024 * 1024:
            break
    pal.unlink(missing_ok=True)


def make_edit(mp4: Path, marks: list[tuple[str, float, float, float]], out: Path, total: float | None = None,
              gap_keep: float = 10.0, gap_speed: float = 4.0) -> list:
    """Cut a highlight edit. marks = [(name, t_video, hold_s, speed)] sorted by time. Each mark keeps
    [t-0.5, t+hold] at `speed`; the gap before it keeps only its last `gap_keep` s at `gap_speed`x."""
    total = total or duration(mp4)
    segs, prev_end = [], 0.0
    for _, t, hold, speed in sorted(marks, key=lambda m: m[1]):
        s, e = max(prev_end, t - 0.5), min(total, t + hold)
        if e <= s:
            continue
        if s - prev_end > 0.5:
            segs.append((max(prev_end, s - gap_keep), s, gap_speed))
        segs.append((s, e, speed))
        prev_end = e
    parts, labels = [], []
    for i, (s, e, sp) in enumerate(segs):
        parts.append(f"[0:v]trim=start={s:.2f}:end={e:.2f},setpts=(PTS-STARTPTS)/{sp},fps=25[v{i}]")
        labels.append(f"[v{i}]")
    fc = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=1:a=0,scale=1440:900[out]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-filter_complex", fc, "-map", "[out]",
                    "-c:v", "libx264", "-crf", "26", "-preset", "veryfast", "-movflags", "+faststart",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return segs
