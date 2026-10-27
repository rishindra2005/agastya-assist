"""Build the Agastya Assist product-intro film.

Cards are drawn with Pillow (libraqm shaping, so Indic scripts render correctly) and piped
frame-by-frame into ffmpeg; scene clips are composed with ffmpeg; segments are joined with
xfade transitions and a narration track is mixed on top.

Usage:  .venv/bin/python scripts/video/make_intro.py [--only-segments]
Narration WAVs come from scripts/video/narrate.py.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"
NARR = HERE / "narration"
BUILD = HERE / "build"
FINAL = ROOT / "data/demo_run/final"
DEMO = ROOT / "data/demo_run"

W, H, FPS = 1920, 1080, 30
XF = 0.5  # crossfade seconds

INK = (21, 32, 59)
PAPER = (246, 247, 242)
SAFFRON = (217, 102, 26)
GREEN = (46, 125, 79)
MUTED = (86, 96, 122)
RULE = (213, 219, 207)
WHITE = (255, 255, 255)
SHEET = (255, 255, 255)

NOTO = "/usr/share/fonts/truetype/noto"


# ---------------------------------------------------------------- fonts / text
_font_cache: dict = {}


def font(kind: str, size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    key = (kind, size, weight)
    if key in _font_cache:
        return _font_cache[key]
    if kind == "disp":
        f = ImageFont.truetype(str(FONTS / "BricolageGrotesque.ttf"), size)
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
    elif kind == "body":
        f = ImageFont.truetype(str(FONTS / f"Mukta-{weight}.ttf"), size)
    elif kind == "mono":
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", size)
    else:  # a Noto script name, e.g. "Tamil"
        w = "Bold" if weight in ("Bold", "ExtraBold") else weight
        p = Path(NOTO) / f"NotoSans{kind}-{w}.ttf"
        if not p.exists():
            p = Path(NOTO) / f"NotoSans{kind}-Regular.ttf"
        f = ImageFont.truetype(str(p), size)
    _font_cache[key] = f
    return f


def text_img(s: str, f, fill, pad: int = 12) -> Image.Image:
    """Render text onto a tight transparent RGBA layer."""
    d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    l, t, r, b = d.textbbox((0, 0), s, font=f)
    im = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((pad - l, pad - t), s, font=f, fill=fill)
    return im


def rounded(size, radius, fill, outline=None, width=0) -> Image.Image:
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=fill,
                                         outline=outline, width=width)
    return im


def shadow(im: Image.Image, blur=24, offset=(0, 14), opacity=60) -> Image.Image:
    """Return an RGBA layer = soft drop shadow + image (padded)."""
    pad = blur * 2
    out = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    a = im.getchannel("A").point(lambda v: v * opacity // 255)
    sh = Image.new("RGBA", im.size, (10, 16, 30, 0))
    sh.putalpha(a)
    out.alpha_composite(sh, (pad + offset[0], pad + offset[1]))
    out = out.filter(ImageFilter.GaussianBlur(blur))
    out.alpha_composite(im, (pad, pad))
    return out


def lock_icon(size: int, color) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = size
    lw = max(3, s // 11)
    d.arc((s * 0.27, s * 0.06, s * 0.73, s * 0.58), 180, 360, fill=color, width=lw)
    d.line((s * 0.27 + lw / 2, s * 0.32, s * 0.27 + lw / 2, s * 0.46), fill=color, width=lw)
    d.line((s * 0.73 - lw / 2, s * 0.32, s * 0.73 - lw / 2, s * 0.46), fill=color, width=lw)
    d.rounded_rectangle((s * 0.16, s * 0.44, s * 0.84, s * 0.94), s // 10, fill=color)
    d.ellipse((s * 0.44, s * 0.6, s * 0.56, s * 0.72), fill=(255, 255, 255, 255))
    d.rectangle((s * 0.475, s * 0.68, s * 0.525, s * 0.82), fill=(255, 255, 255, 255))
    return im


def ease(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def with_alpha(im: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return im
    im = im.copy()
    im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
    return im


@dataclass
class El:
    img: Image.Image
    x: int
    y: int
    t0: float = 0.0
    dur: float = 0.6
    dy: int = 28
    scale_in: bool = False


@dataclass
class Card:
    name: str
    duration: float
    base: Image.Image  # RGB background
    els: list[El] = field(default_factory=list)
    dynamic: list = field(default_factory=list)  # callables (frame:Image, t) -> None

    def frame(self, t: float) -> Image.Image:
        fr = self.base.copy().convert("RGBA")
        for fn in self.dynamic:
            fn(fr, t)
        for e in self.els:
            if t < e.t0:
                continue
            p = ease((t - e.t0) / e.dur)
            img = e.img
            x, y = e.x, e.y + int(round(e.dy * (1 - p)))
            if e.scale_in and p < 1:
                sc = 0.85 + 0.15 * p
                nw, nh = max(1, int(img.width * sc)), max(1, int(img.height * sc))
                img = img.resize((nw, nh), Image.BICUBIC)
                x += (e.img.width - nw) // 2
                y += (e.img.height - nh) // 2
            fr.alpha_composite(with_alpha(img, p), (x, y))
        return fr.convert("RGB")


def bg_paper() -> Image.Image:
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    for y in range(120, H, 54):  # faint ruled paper lines
        d.line((0, y, W, y), fill=(236, 239, 230), width=1)
    d.rectangle((0, 0, W, 10), fill=SAFFRON)
    d.rectangle((0, H - 10, W, H), fill=GREEN)
    return im


def bg_ink() -> Image.Image:
    im = Image.new("RGB", (W, H), INK)
    # soft radial glow
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((W * 0.15, -H * 0.4, W * 0.95, H * 0.9), fill=60)
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    im = Image.composite(Image.new("RGB", (W, H), (44, 60, 104)), im, glow)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W // 3, 8), fill=SAFFRON)
    d.rectangle((W // 3, 0, 2 * W // 3, 8), fill=WHITE)
    d.rectangle((2 * W // 3, 0, W, 8), fill=GREEN)
    return im


def centered(img: Image.Image, y: int) -> El:
    return El(img, (W - img.width) // 2, y)


# ---------------------------------------------------------------- cards
def card_cold(dur: float, shot: Path) -> Card:
    src = Image.open(shot).convert("RGB")
    # cover-fit then slow push-in
    sc = max(W / src.width, H / src.height) * 1.0
    big = src.resize((int(src.width * sc * 1.12), int(src.height * sc * 1.12)), Image.LANCZOS)
    base = Image.new("RGB", (W, H), PAPER)
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    y0 = int(H * 0.38)
    for y in range(y0, H):
        a = int(250 * min(1.0, ((y - y0) / (H * 0.42))) ** 0.9)
        gd.line((0, y, W, y), fill=(*INK, a))

    def kb(fr: Image.Image, t: float):
        p = t / dur
        z = 1.0 + 0.10 * p  # zoom from 1.0 to 1.10 of the cover size
        cw, ch = W * 1.12 / z, H * 1.12 / z
        cx, cy = big.width * 0.42, big.height * (0.38 + 0.1 * p)
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        box = (max(0, box[0]), max(0, box[1]), min(big.width, box[2]), min(big.height, box[3]))
        fr.paste(big.resize((W, H), Image.BILINEAR, box=box), (0, 0))
        fr.alpha_composite(grad)

    c = Card("a_cold", dur, base, dynamic=[kb])
    t1 = text_img("Government portals have to", font("disp", 92, "ExtraBold"), WHITE)
    t2 = text_img("explain everything.", font("disp", 92, "ExtraBold"), (255, 196, 150))
    c.els += [El(t1, 120, 760, 0.5), El(t2, 120, 860, 0.9)]
    return c


def card_title(dur: float) -> Card:
    c = Card("b_title", dur, bg_ink())
    eyebrow = text_img("INTRODUCING", font("mono", 30), (255, 196, 150))
    t1 = text_img("Agastya Assist", font("disp", 168, "ExtraBold"), WHITE)
    t2 = text_img("अगस्त्य सहायक", font("body", 120, "Bold"), (240, 138, 60))
    t3 = text_img("Speak what you need. Agastya fills the form.", font("body", 58, "Medium"), (214, 222, 240))
    y = 250
    c.els += [centered(eyebrow, y), centered(t1, y + 70), centered(t2, y + 280), centered(t3, y + 470)]
    for i, e in enumerate(c.els):
        e.t0 = 0.15 + 0.3 * i
        e.dur = 0.8
    c.els[1].scale_in = True
    return c


def stat_card(num: str, label: str, sub: str, color) -> Image.Image:
    w, h = 500, 420
    im = rounded((w, h), 28, SHEET, outline=RULE, width=2)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, 16), 8, fill=color)
    d.rectangle((0, 8, w - 1, 16), fill=color)
    size = 170
    while True:
        n = text_img(num, font("disp", size, "ExtraBold"), color, pad=0)
        if n.width <= w - 70:
            break
        size -= 6
    im.alpha_composite(n, ((w - n.width) // 2, 60 + (130 - n.height) // 2 + 20))
    l = text_img(label, font("body", 50, "Bold"), INK, pad=0)
    im.alpha_composite(l, ((w - l.width) // 2, 245))
    s = text_img(sub, font("body", 36, "Regular"), MUTED, pad=0)
    im.alpha_composite(s, ((w - s.width) // 2, 320))
    return shadow(im, blur=18, offset=(0, 10), opacity=45)


def card_problem(dur: float, times=(0.6, 4.6, 6.8)) -> Card:
    c = Card("c_problem", dur, bg_paper())
    eb = text_img("IN OUR DEMO REPLICAS", font("mono", 30), SAFFRON)
    hd = text_img("One form. Pages of rules.", font("disp", 96, "ExtraBold"), INK)
    c.els += [El(eb, 140, 110, 0.1), El(hd, 140, 160, 0.25)]
    cards = [
        stat_card("67", "fields", "PAN Form 49A · 6 pages", SAFFRON),
        stat_card("77", "fields", "ITR-1 return", GREEN),
        stat_card("5,000+", "words", "of instructions", INK),
    ]
    gap = -10  # shadow padding already separates the cards
    tw = sum(x.width for x in cards) + gap * 2
    x = (W - tw) // 2
    for im, t0 in zip(cards, times):
        c.els.append(El(im, x, 400, t0, 0.7, dy=50))
        x += im.width + gap
    return c


LANGS = [
    ("हिन्दी", "Hindi", "Devanagari"), ("English", "English", None), ("বাংলা", "Bengali", "Bengali"),
    ("தமிழ்", "Tamil", "Tamil"), ("తెలుగు", "Telugu", "Telugu"), ("ಕನ್ನಡ", "Kannada", "Kannada"),
    ("മലയാളം", "Malayalam", "Malayalam"), ("मराठी", "Marathi", "Devanagari"), ("ગુજરાતી", "Gujarati", "Gujarati"),
    ("ਪੰਜਾਬੀ", "Punjabi", "Gurmukhi"), ("ଓଡ଼ିଆ", "Odia", "Oriya"),
]

GREETINGS = [  # (key, lang index, text, script font, translation)
    ("d_hi", 0, "नमस्ते, मैं अगस्त्य हूँ। आज कौन सा फ़ॉर्म भरना है?", "Devanagari"),
    ("d_ta", 3, "வணக்கம், நான் அகஸ்தியா. இன்று எந்தப் படிவத்தை நிரப்ப வேண்டும்?", "Tamil"),
    ("d_bn", 2, "নমস্কার, আমি অগস্ত্য। আজ কোন ফর্মটি পূরণ করবেন?", "Bengali"),
    ("d_te", 4, "నమస్కారం, నేను అగస్త్య. ఈ రోజు ఏ ఫారం నింపాలి?", "Telugu"),
]


def lang_tile(native: str, eng: str, script, hi: bool) -> Image.Image:
    w, h = 360, 170
    im = rounded((w, h), 22, SHEET if not hi else (255, 244, 234), outline=SAFFRON if hi else RULE,
                 width=5 if hi else 2)
    f = font("disp", 62, "Bold") if script is None else font(script, 58, "Bold")
    n = text_img(native, f, SAFFRON if hi else INK, pad=0)
    im.alpha_composite(n, ((w - n.width) // 2, 28 + max(0, (70 - n.height) // 2)))
    e = text_img(eng.upper(), font("mono", 22), MUTED, pad=0)
    im.alpha_composite(e, ((w - e.width) // 2, 122))
    return im


def card_langs(dur: float, sched: dict[str, tuple[float, float]]) -> Card:
    c = Card("d_lang", dur, bg_paper())
    eb = text_img("VOICE IN · VOICE OUT", font("mono", 30), SAFFRON)
    hd = text_img("Speaks 11 Indian languages", font("disp", 90, "ExtraBold"), INK)
    c.els += [El(eb, 140, 70, 0.0), El(hd, 140, 115, 0.15)]
    tiles = [lang_tile(n, e, s, False) for n, e, s in LANGS]
    tiles_hi = [lang_tile(n, e, s, True) for n, e, s in LANGS]
    rows = [range(0, 4), range(4, 8), range(8, 11)]
    gap = 34
    pos = {}
    for r, idxs in enumerate(rows):
        idxs = list(idxs)
        rw = len(idxs) * 360 + (len(idxs) - 1) * gap
        x0 = (W - rw) // 2
        for k, i in enumerate(idxs):
            pos[i] = (x0 + k * (360 + gap), 290 + r * (170 + 28))
    for i in range(11):
        c.els.append(El(tiles[i], *pos[i], 0.4 + 0.32 * i, 0.5, dy=30, scale_in=True))

    # greeting subtitle + highlighted tile while each greeting plays
    subs = {}
    for key, li, txt, scr in GREETINGS:
        im = text_img(txt, font(scr, 50, "Bold"), INK, pad=0)
        if im.width > W - 280:
            im = im.resize((W - 280, int(im.height * (W - 280) / im.width)), Image.LANCZOS)
        subs[key] = im

    def hl(fr: Image.Image, t: float):
        for key, li, _txt, _scr in GREETINGS:
            if key not in sched:
                continue
            s, e = sched[key]
            if s - 0.1 <= t <= e + 0.25:
                a = ease((t - s + 0.1) / 0.3) * (1 - ease((t - e) / 0.25))
                fr.alpha_composite(with_alpha(tiles_hi[li], a), pos[li])
                sub = subs[key]
                y = 900 + int(12 * (1 - a))
                fr.alpha_composite(with_alpha(sub, a), ((W - sub.width) // 2, y))

    # draw highlights after tiles: run as an element-pass hook
    c._hl = hl  # type: ignore[attr-defined]
    orig = c.frame

    def frame(t: float, _orig=orig):
        fr = _orig(t).convert("RGBA")
        hl(fr, t)
        return fr.convert("RGB")

    c.frame = frame  # type: ignore[method-assign]
    return c


def card_charge(dur: float) -> Card:
    c = Card("f_charge", dur, bg_ink())
    eb = text_img("YOU STAY IN CHARGE", font("mono", 30), (255, 196, 150))
    hd = text_img("The last step is always yours.", font("disp", 92, "ExtraBold"), WHITE)
    c.els += [El(eb, 140, 100, 0.0), El(hd, 140, 145, 0.15)]
    items = ["Captcha", "OTP", "Consent", "Submit"]
    w, h, gap = 360, 250, 40
    x0 = (W - (4 * w + 3 * gap)) // 2
    for i, it in enumerate(items):
        im = rounded((w, h), 26, (32, 46, 82), outline=(70, 88, 135), width=2)
        im.alpha_composite(lock_icon(84, (240, 138, 60, 255)), ((w - 84) // 2, 32))
        tx = text_img(it, font("disp", 60, "Bold"), WHITE, pad=0)
        im.alpha_composite(tx, ((w - tx.width) // 2, 140))
        c.els.append(El(im, x0 + i * (w + gap), 330, 0.9 + 0.45 * i, 0.6, scale_in=True))
    yours = text_img("= always yours", font("disp", 56, "Bold"), (255, 196, 150))
    c.els.append(centered(yours, 615))
    c.els[-1].t0 = 3.0
    locks = ["Action guard", "Tool gate", "In-page lock"]
    pills = []
    for name in locks:
        tx = text_img(name, font("body", 40, "Bold"), INK, pad=0)
        pw = tx.width + 120
        p = rounded((pw, 84), 42, (232, 245, 236), outline=GREEN, width=3)
        p.alpha_composite(lock_icon(40, (*GREEN, 255)), (30, 20))
        p.alpha_composite(tx, (84, (84 - tx.height) // 2 - 6))
        pills.append(p)
    lab = text_img("Three safety locks:", font("body", 40, "Medium"), (214, 222, 240), pad=0)
    total = lab.width + 30 + sum(p.width for p in pills) + 24 * 2
    x = (W - total) // 2
    c.els.append(El(lab, x, 790 + (84 - lab.height) // 2 - 6, 5.0, 0.5))
    x += lab.width + 30
    for i, p in enumerate(pills):
        c.els.append(El(p, x, 790, 5.2 + 0.35 * i, 0.5))
        x += p.width + 24
    note = text_img("Agastya never clicks submit, pay, e-verify, OTP or login.", font("body", 34, "Regular"),
                    (170, 182, 210))
    c.els.append(centered(note, 930))
    c.els[-1].t0 = 6.6
    return c


def phone(shot: Path, height: int = 860) -> Image.Image:
    src = Image.open(shot).convert("RGB")
    sw = int(height * 0.462)
    scr = src.resize((sw, int(src.height * sw / src.width)), Image.LANCZOS).crop((0, 0, sw, height))
    bez = 22
    pw, ph = sw + 2 * bez, height + 2 * bez
    im = rounded((pw, ph), 64, (18, 22, 32, 255))
    mask = Image.new("L", (sw, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, sw - 1, height - 1), 46, fill=255)
    im.paste(scr, (bez, bez), mask)
    d = ImageDraw.Draw(im)
    d.ellipse((pw // 2 - 7, 6, pw // 2 + 7, 20), fill=(40, 46, 60, 255))
    return shadow(im, blur=28, offset=(0, 18), opacity=80)


def card_phone(dur: float, shots: list[Path]) -> Card:
    c = Card("g_phone", dur, bg_paper())
    eb = text_img("ON THE GO", font("mono", 30), SAFFRON)
    t1 = text_img("Also on", font("disp", 110, "ExtraBold"), INK)
    t1b = text_img("your phone", font("disp", 110, "ExtraBold"), SAFFRON)
    t2 = text_img("Installable app —", font("body", 46, "Medium"), MUTED)
    t2b = text_img("add it to your home screen.", font("body", 46, "Medium"), MUTED)
    c.els += [El(eb, 140, 290, 0.1), El(t1, 140, 340, 0.25), El(t1b, 140, 465, 0.35),
              El(t2, 140, 620, 0.5), El(t2b, 140, 680, 0.55)]
    shots = shots[:2]
    phones = [phone(s, 800) for s in shots]
    x = W - 80 - sum(p.width for p in phones) + 60 * (len(phones) - 1)
    for i, p in enumerate(phones):
        y = (H - p.height) // 2 + (30 if i % 2 else 0)
        c.els.append(El(p, x, y, 0.3 + 0.3 * i, 0.8, dy=80))
        x += p.width - 60
    return c


def card_outro(dur: float) -> Card:
    c = Card("h_outro", dur, bg_ink())
    t1 = text_img("Agastya Assist", font("disp", 150, "ExtraBold"), WHITE)
    t2 = text_img("अगस्त्य सहायक", font("body", 96, "Bold"), (240, 138, 60))
    t3 = text_img("11 Indian languages  ·  Voice in, voice out  ·  Never submits for you",
                  font("body", 50, "Medium"), (214, 222, 240))
    t4 = text_img("Built for the Vikas hackathon", font("mono", 32), (170, 182, 210))
    c.els += [centered(t1, 250), centered(t2, 450), centered(t3, 640), centered(t4, 800)]
    for i, e in enumerate(c.els):
        e.t0 = 0.1 + 0.35 * i
        e.dur = 0.8
    c.els[0].scale_in = True
    return c


# ---------------------------------------------------------------- scenes
SCENES = [
    # key, folder, clip, title, language label, one-liner
    ("e_pan", "pan", "scene_pan_edit.mp4", "New PAN card", ("हिन्दी", "Devanagari", "Hindi"),
     "Agastya asks one question at a time and fills all 6 pages of Form 49A."),
    ("e_mca", "mca", "scene_mca_edit.mp4", "Company name reservation", ("English", None, ""),
     "A founder reserves a company name on the MCA form from one spoken request."),
    ("e_itr", "itr", "scene_itr_edit.mp4", "Income tax return · ITR-1", ("हिन्दी", "Devanagari", "Hindi"),
     "Upload Form 16: Agastya reads it, fills ITR-1 and compares old vs new regime."),
    ("e_voter", "voter", "scene_voter_edit.mp4", "Voter registration · Form 6", ("தமிழ்", "Tamil", "Tamil"),
     "A first-time voter registers, speaking Tamil."),
]

CLIP_BOX = (240, 24, 1440, 900)  # x, y, w, h of scene footage inside the 1920x1080 frame


def scene_overlay(title: str, lang: str, line: str) -> Image.Image:
    """Full-frame RGBA: paper surround + caption band + corner tag (clip shows through a hole)."""
    x, y, w, h = CLIP_BOX
    im = Image.new("RGBA", (W, H), (*PAPER, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W, 8), fill=SAFFRON)
    # soft shadow around clip
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x, y + 8, x + w, y + h + 8), 18, fill=(10, 16, 30, 70))
    sh = sh.filter(ImageFilter.GaussianBlur(16))
    im.alpha_composite(sh)
    hole = Image.new("L", (W, H), 255)
    ImageDraw.Draw(hole).rounded_rectangle((x, y, x + w - 1, y + h - 1), 16, fill=0)
    im.putalpha(Image.composite(im.getchannel("A"), Image.new("L", (W, H), 0), hole))
    # caption band
    by = y + h + 16
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((x, by + 8, x + 8, by + 100), 4, fill=SAFFRON)
    tt = text_img(title, font("disp", 44, "ExtraBold"), INK, pad=0)
    im.alpha_composite(tt, (x + 28, by + 4))
    native, script, eng = lang
    lx = x + 28 + tt.width + 26
    nf = font("body", 34, "Bold") if script in (None, "Devanagari") else font(script, 30, "Bold")
    lg = text_img(native, nf, SAFFRON, pad=0)
    im.alpha_composite(lg, (lx, by + 26 - lg.height // 2 + 4))
    if eng:
        e2 = text_img(" · " + eng, font("body", 34, "Bold"), SAFFRON, pad=0)
        im.alpha_composite(e2, (lx + lg.width, by + 26 - e2.height // 2 + 2))
    ln = text_img(line, font("body", 32, "Regular"), MUTED, pad=0)
    im.alpha_composite(ln, (x + 28, by + 62))
    # corner tag
    tag = text_img("Demo replicas — not government websites", font("body", 26, "Medium"), INK, pad=0)
    pw = tag.width + 40
    pill = rounded((pw, 50), 25, (255, 255, 255, 255), outline=RULE, width=2)
    pill.alpha_composite(tag, (20, (50 - tag.height) // 2 - 3))
    im.alpha_composite(pill, (x + w - pw, by + 30))
    return im


def run(cmd: list[str]):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(" ".join(cmd)[:2000], "\n", r.stderr[-3000:], file=sys.stderr)
        raise SystemExit(1)
    return r.stdout


def probe_dur(p: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]).strip())


def has_audio(p: Path) -> bool:
    out = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(p)])
    return bool(out.strip())


def is_silent(p: Path) -> bool:
    r = subprocess.run(["ffmpeg", "-i", str(p), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "max_volume" in line:
            return float(line.split(":")[1].split()[0]) < -50
    return True


def render_card(card: Card, out: Path):
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
         str(out)], stdin=subprocess.PIPE)
    n = int(round(card.duration * FPS))
    for i in range(n):
        proc.stdin.write(card.frame(i / FPS).tobytes())
    proc.stdin.close()
    proc.wait()
    if card.name == "b_title":
        card.frame(card.duration - 0.01).save(FINAL / "poster.png")


def kenburns_from_shots(shots: list[Path], dur: float, out: Path):
    """Fallback scene footage: slow zoom across screenshots, 1440x900."""
    per = dur / len(shots)
    imgs = [Image.open(s).convert("RGB") for s in shots]
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1440x900",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p", str(out)],
        stdin=subprocess.PIPE)
    n = int(round(dur * FPS))
    for i in range(n):
        t = i / FPS
        k = min(len(imgs) - 1, int(t / per))
        p = (t - k * per) / per
        im = imgs[k]
        sc = max(1440 / im.width, 900 / im.height)
        z = 1.0 + 0.08 * p
        cw, ch = 1440 / sc / z, 900 / sc / z
        cx, cy = im.width / 2, ch / 2 + (im.height - ch) * 0.15 * p
        fr = im.resize((1440, 900), Image.BILINEAR, box=(cx - cw / 2, max(0, cy - ch / 2), cx + cw / 2,
                                                          max(0, cy - ch / 2) + ch))
        # crossfade between shots
        if k + 1 < len(imgs) and p > 0.85:
            nxt = imgs[k + 1]
            sc2 = max(1440 / nxt.width, 900 / nxt.height)
            fr2 = nxt.resize((1440, 900), Image.BILINEAR, box=(0, 0, 1440 / sc2, 900 / sc2))
            fr = Image.blend(fr, fr2, (p - 0.85) / 0.15)
        proc.stdin.write(fr.tobytes())
    proc.stdin.close()
    proc.wait()


def render_scene(key: str, clip: Path, title: str, lang: str, line: str, out: Path, max_len: float,
                 min_len: float) -> tuple[float, Path | None]:
    """Compose a scene clip into the frame. Returns (duration, path-of-scene-audio or None)."""
    ov = BUILD / f"{key}_overlay.png"
    scene_overlay(title, lang, line).save(ov)
    src_dur = probe_dur(clip)
    speed = 1.0
    if src_dur > max_len:
        speed = src_dur / max_len
    dur = max(min_len, src_dur / speed)
    x, y, w, h = CLIP_BOX
    vf = (f"[0:v]setpts=PTS/{speed:.4f},fps={FPS},scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white,tpad=stop_mode=clone:stop_duration=30[c];"
          f"color=c=0xf6f7f2:s={W}x{H}:r={FPS}[bg];[bg][c]overlay={x}:{y}:shortest=1[b];"
          f"[1:v]format=rgba,fade=in:st=0:d=0.6:alpha=1[o];[b][o]overlay=0:0,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clip), "-loop", "1", "-i", str(ov),
         "-filter_complex", vf, "-map", "[v]", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", str(out)])
    aud = None
    if has_audio(clip) and not is_silent(clip) and speed < 1.05:
        aud = BUILD / f"{key}_scene.wav"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clip), "-vn", "-ac", "2", "-ar", "48000", str(aud)])
    return dur, aud


# ---------------------------------------------------------------- main
def nd(key: str) -> float:
    return probe_dur(NARR / f"{key}.wav")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene-max", type=float, default=22.0, help="speed up scene clips longer than this")
    ap.add_argument("--wait-report", action="store_true")
    args = ap.parse_args()
    BUILD.mkdir(exist_ok=True)
    FINAL.mkdir(parents=True, exist_ok=True)

    segs: list[tuple[str, Path, float]] = []  # name, video, duration
    narr: list[tuple[str, str, float]] = []  # wav key, segment name, offset within segment
    scene_audio: list[tuple[Path, str]] = []
    status = {}

    # (a) cold open
    cold_shot = ROOT / "data/demo_run/final/cold_open_replica.png"
    d = max(5.0, nd("a_cold") + 0.9)
    card = card_cold(d, cold_shot)
    render_card(card, BUILD / "a_cold.mp4"); segs.append(("a_cold", BUILD / "a_cold.mp4", d))
    narr.append(("a_cold", "a_cold", 0.4))

    d = max(5.0, nd("b_title") + 1.6)
    render_card(card_title(d), BUILD / "b_title.mp4"); segs.append(("b_title", BUILD / "b_title.mp4", d))
    narr.append(("b_title", "b_title", 0.6))

    d = nd("c_problem") + 1.6
    render_card(card_problem(d), BUILD / "c_problem.mp4"); segs.append(("c_problem", BUILD / "c_problem.mp4", d))
    narr.append(("c_problem", "c_problem", 0.4))

    # languages: schedule greetings back-to-back
    t = 0.3
    sched = {}
    narr.append(("d_lang", "d_lang", t)); t += nd("d_lang") + 0.5
    for key, *_ in GREETINGS:
        sched[key] = (t, t + nd(key))
        narr.append((key, "d_lang", t))
        t += nd(key) + 0.45
    d = t + 0.6
    render_card(card_langs(d, sched), BUILD / "d_lang.mp4"); segs.append(("d_lang", BUILD / "d_lang.mp4", d))

    # (e) scenes
    for key, folder, clip, title, lang, line in SCENES:
        p = DEMO / folder / clip
        min_len = nd(key) + 1.5
        if p.exists() and probe_dur(p) > 2:
            status[key] = "real clip"
            src = p
        else:
            shots = sorted(glob.glob(str(DEMO / folder / "0*_*.png"))) or \
                sorted(glob.glob(str(DEMO / folder / "screenshots" / "*.png")))
            shots = [Path(s) for s in shots][:6]
            if not shots:
                status[key] = "skipped (no clip or screenshots)"
                continue
            status[key] = f"Ken Burns from {len(shots)} screenshots"
            src = BUILD / f"{key}_kb.mp4"
            kenburns_from_shots(shots, max(min_len, 3.0 * len(shots)), src)
        out = BUILD / f"{key}.mp4"
        dur, aud = render_scene(key, src, title, lang, line, out, args.scene_max, min_len)
        segs.append((key, out, dur))
        narr.append((key, key, 0.7))
        if aud:
            scene_audio.append((aud, key))

    d = nd("f_charge") + 1.8
    render_card(card_charge(d), BUILD / "f_charge.mp4"); segs.append(("f_charge", BUILD / "f_charge.mp4", d))
    narr.append(("f_charge", "f_charge", 0.6))

    mshots = sorted(Path(p) for p in glob.glob(str(DEMO / "mobile/*.png")))
    if not mshots:
        mshots = [ROOT / ".agy/qa/ui_mobile.png", ROOT / ".agy/qa/ui_mobile2.png"]
    d = max(6.0, nd("g_phone") + 2.5)
    render_card(card_phone(d, mshots), BUILD / "g_phone.mp4"); segs.append(("g_phone", BUILD / "g_phone.mp4", d))
    narr.append(("g_phone", "g_phone", 0.5))

    d = nd("h_outro") + 2.8
    render_card(card_outro(d), BUILD / "h_outro.mp4"); segs.append(("h_outro", BUILD / "h_outro.mp4", d))
    narr.append(("h_outro", "h_outro", 0.6))

    # ---- join video with xfade
    starts = {}
    acc = 0.0
    for i, (name, _p, dur) in enumerate(segs):
        starts[name] = acc
        acc += dur - XF
    total = acc + XF
    inputs, fc = [], []
    for _n, p, _d in segs:
        inputs += ["-i", str(p)]
    prev = "0:v"
    off = 0.0
    for i in range(1, len(segs)):
        off += segs[i - 1][2] - XF
        tr = "fade" if segs[i][0].startswith("e_") or segs[i - 1][0].startswith("e_") else "smoothleft"
        if segs[i][0] in ("b_title", "h_outro"):
            tr = "fadeblack"
        lab = f"x{i}"
        fc.append(f"[{prev}]settb=AVTB,fps={FPS}[p{i}];[{i}:v]settb=AVTB,fps={FPS}[q{i}];"
                  f"[p{i}][q{i}]xfade=transition={tr}:duration={XF}:offset={off:.3f}[{lab}]")
        prev = lab
    fc.append(f"[{prev}]fade=out:st={total - 0.6:.3f}:d=0.6,format=yuv420p[vout]")

    # ---- audio mix
    a_inputs, a_fc, mix = [], [], []
    k = len(segs)
    for wav, seg, local in narr:
        a_inputs += ["-i", str(NARR / f"{wav}.wav")]
        ms = int((starts[seg] + local) * 1000)
        a_fc.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms},volume=1.0[n{k}]")
        mix.append(f"[n{k}]")
        k += 1
    for wav, seg in scene_audio:
        a_inputs += ["-i", str(wav)]
        ms = int(starts[seg] * 1000)
        segdur = dict((n, d) for n, _p, d in segs)[seg]
        a_fc.append(f"[{k}:a]aresample=48000,atrim=0:{segdur:.3f},afade=t=out:st={segdur - 0.6:.3f}:d=0.6,"
                    f"adelay={ms}|{ms},volume=-18dB[n{k}]")
        mix.append(f"[n{k}]")
        k += 1
    a_fc.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:dropout_transition=0,"
                f"apad,atrim=0:{total:.3f},loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[aout]")
    filt = ";".join(fc + a_fc)
    out = FINAL / "agastya_intro.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, *a_inputs, "-filter_complex", filt,
         "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "slow", "-crf", "21",
         "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
         "-t", f"{total:.3f}", str(out)])

    # 720p web version
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-vf", "scale=1280:720:flags=lanczos",
         "-c:v", "libx264", "-preset", "slow", "-crf", "25", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", str(FINAL / "agastya_intro_720p.mp4")])

    # 30 s teaser: title + languages excerpt + PAN scene excerpt + charge + outro
    teaser(starts, dict((n, d) for n, _p, d in segs), out)

    meta = {"segments": [(n, round(starts[n], 2), round(d, 2)) for n, _p, d in segs], "total": round(total, 2),
            "scenes": status}
    (BUILD / "timeline.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    print(json.dumps(meta, indent=1, ensure_ascii=False))


def teaser(starts: dict, durs: dict, full: Path):
    """Cut ~30 s from the finished film: title, languages, a scene, safety, outro."""
    want = [("b_title", 0, 4.5), ("d_lang", 2.0, 8.5)]
    scene = next((k for k in ("e_pan", "e_itr", "e_voter", "e_mca") if k in starts), None)
    if scene:
        want.append((scene, 0.2, 7.0))
    want += [("f_charge", 0.8, 5.0), ("h_outro", 0.0, 5.5)]
    parts = []
    for i, (seg, a, ln) in enumerate(want):
        ln = min(ln, durs[seg] - a)
        p = BUILD / f"teaser_{i}.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{starts[seg] + a:.3f}", "-i", str(full), "-t", f"{ln:.3f}",
             "-vf", "fade=in:st=0:d=0.25,fade=out:st=%.3f:d=0.25" % (ln - 0.25),
             "-af", "afade=t=in:st=0:d=0.15,afade=t=out:st=%.3f:d=0.25" % (ln - 0.25),
             "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-ar", "48000",
             "-ac", "2", "-b:a", "160k", str(p)])
        parts.append(p)
    lst = BUILD / "teaser.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
         "-movflags", "+faststart", str(FINAL / "agastya_teaser_30s.mp4")])


if __name__ == "__main__":
    main()
