"""Synthesize narration lines for the intro film with Sarvam TTS (one WAV per line)."""
import asyncio, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agastya.voice.tts import SarvamTTS  # noqa: E402

OUT = Path(__file__).parent / "narration"

LINES = {
    "a_cold": ("en-IN", "Government portals have to explain everything. So every form comes with pages of rules."),
    "b_title": ("en-IN", "Meet Agastya Assist. Speak what you need, and Agastya fills the form."),
    "c_problem": ("en-IN", "In our demo replicas, the PAN application alone is six pages and sixty-seven fields. "
                  "I T R one has seventy-seven. And more than five thousand words of instructions."),
    "d_lang": ("en-IN", "Agastya speaks eleven Indian languages."),
    "d_hi": ("hi-IN", "नमस्ते, मैं अगस्त्य हूँ। आज कौन सा फ़ॉर्म भरना है?"),
    "d_ta": ("ta-IN", "வணக்கம், நான் அகஸ்தியா. இன்று எந்தப் படிவத்தை நிரப்ப வேண்டும்?"),
    "d_bn": ("bn-IN", "নমস্কার, আমি অগস্ত্য। আজ কোন ফর্মটি পূরণ করবেন?"),
    "d_te": ("te-IN", "నమస్కారం, నేను అగస్త్య. ఈ రోజు ఏ ఫారం నింపాలి?"),
    "e_pan": ("en-IN", "A citizen asks, in Hindi, for a new PAN card. Agastya asks simple questions, one at a time, and fills every page of Form 49 A."),
    "e_mca": ("en-IN", "In English, a founder reserves a company name. Agastya fills the M C A form from a single request."),
    "e_itr": ("en-IN", "For income tax, just upload your Form 16. Agastya reads it, fills I T R one, and compares the old and new tax regimes."),
    "e_voter": ("en-IN", "And in Tamil, a first-time voter registers with Form 6."),
    "f_charge": ("en-IN", "You always stay in charge. Captcha, O T P, consent and the final submit are always yours. Three safety locks make sure Agastya never submits for you."),
    "g_phone": ("en-IN", "Agastya is also on your phone, as an installable app."),
    "h_outro": ("en-IN", "Agastya Assist. Eleven Indian languages. Voice in, voice out. And it never submits for you."),
}


async def main(only: list[str]):
    OUT.mkdir(exist_ok=True)
    tts = SarvamTTS()
    for key, (lang, text) in LINES.items():
        if only and key not in only:
            continue
        p = OUT / f"{key}.wav"
        if p.exists() and not only:
            continue
        p.write_bytes(await tts.synthesize(text, lang))
        print("ok", key, flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
