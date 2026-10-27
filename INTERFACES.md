# Interface contract (owned by supervisor — agy sessions read only)

All async code uses asyncio. All modules import settings via `from agastya.config import get_settings`.

## agastya/voice/types.py
```python
from dataclasses import dataclass

SUPPORTED_LANGUAGES: dict[str, str]  # code -> English name. Must include:
# hi-IN Hindi, en-IN English, bn-IN Bengali, ta-IN Tamil, te-IN Telugu, kn-IN Kannada,
# ml-IN Malayalam, mr-IN Marathi, gu-IN Gujarati, pa-IN Punjabi, od-IN Odia

@dataclass
class Transcript:
    text: str
    language_code: str          # detected, e.g. "hi-IN"
    raw: dict                   # raw API response

class SarvamError(Exception):
    status_code: int | None
```

## agastya/voice/stt.py
```python
class SarvamSTT:
    def __init__(self, api_key: str | None = None, client: httpx.AsyncClient | None = None,
                 model: str = "saarika:v2.5"): ...
    async def transcribe(self, audio: bytes, filename: str = "audio.wav",
                         language_code: str = "unknown") -> Transcript: ...
    # POST https://api.sarvam.ai/speech-to-text (multipart: file, model, language_code)
    # header: api-subscription-key. "unknown" => auto-detect.
```

## agastya/voice/tts.py
```python
class SarvamTTS:
    def __init__(self, api_key: str | None = None, client: httpx.AsyncClient | None = None,
                 model: str = "bulbul:v2", speaker: str = "anushka"): ...
    async def synthesize(self, text: str, language_code: str) -> bytes: ...   # returns WAV bytes
    # POST https://api.sarvam.ai/text-to-speech (json) ; response "audios": [base64 wav]
    # Texts longer than the API's per-request limit must be split on sentence
    # boundaries (। . ? !) and the WAVs concatenated into ONE valid WAV.
```

## agastya/browser/guard.py
```python
@dataclass
class GuardDecision:
    allowed: bool
    reason: str

def check_click(element: dict) -> GuardDecision: ...
# element = {"tag","type","text","id","name","aria_label","value","href","url"}
# BLOCK when the element is a final action: submit/final submit/file return/e-verify/
# verify/pay/payment/proceed to pay/send otp/validate otp/get otp/login/sign in/
# captcha refresh/register (final)/confirm/agree & submit, incl. Hindi
# (जमा करें, भुगतान, सत्यापित, लॉगिन, ओटीपी). ALLOW: next, continue, save draft,
# add, search, select, tabs, links to forms. Unknown -> allowed=True.
def check_navigation(url: str) -> GuardDecision: ...
# BLOCK payment gateways (e.g. paygov, billdesk, razorpay, payu, sbiepay) and logout URLs.
```

## agastya/browser/driver.py
```python
@dataclass
class FieldInfo:
    ref: str            # stable ref like "f12" usable by fill/select/click
    tag: str; type: str; label: str; name: str; id: str
    required: bool; value: str; options: list[str]   # options for <select>/radio

@dataclass
class PageSnapshot:
    url: str; title: str
    text: str                     # visible text, truncated to 6000 chars
    fields: list[FieldInfo]       # inputs/selects/textareas
    actions: list[dict]           # buttons/links: {"ref","tag","text","href"}

class BrowserDriver:
    def __init__(self, headless: bool | None = None, screenshot_dir: str | None = None): ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def goto(self, url: str) -> PageSnapshot: ...      # runs guard.check_navigation
    async def snapshot(self) -> PageSnapshot: ...
    async def fill(self, ref: str, value: str) -> None: ...
    async def select(self, ref: str, option: str) -> None: ...  # by label or value
    async def click(self, ref: str) -> PageSnapshot: ...     # runs guard.check_click; raises BlockedActionError
    async def screenshot(self) -> bytes: ...                 # PNG
class BlockedActionError(Exception):
    decision: GuardDecision
```
Refs are assigned by injecting `data-agastya-ref` attributes during snapshot.
