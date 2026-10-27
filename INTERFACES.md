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
                 model: str = "saaras:v4"): ...
    async def transcribe(self, audio: bytes, filename: str = "audio.wav",
                         language_code: str = "unknown") -> Transcript: ...
    # POST https://api.sarvam.ai/speech-to-text (multipart: file, model, language_code)
    # header: api-subscription-key. "unknown" => auto-detect.
```

## agastya/voice/tts.py
```python
class SarvamTTS:
    def __init__(self, api_key: str | None = None, client: httpx.AsyncClient | None = None,
                 model: str = "bulbul:v3", speaker: str = "shubh"): ...
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

## agastya/agent/events.py
```python
EventType = Literal["status", "assistant_text", "ask_user", "tool_call", "tool_result",
                    "screenshot", "blocked", "profile_updated", "error", "done"]

@dataclass
class AgentEvent:
    type: EventType
    data: dict              # JSON-serialisable only (screenshot: {"png_b64": str, "url": str})
    ts: float               # time.time()
    def to_json(self) -> dict: ...

class EventBus:
    def subscribe(self) -> asyncio.Queue[AgentEvent]: ...      # each subscriber gets every event
    def unsubscribe(self, q: asyncio.Queue) -> None: ...
    async def publish(self, event: AgentEvent) -> None: ...
    def emit(self, type: EventType, **data) -> None: ...        # sync helper, schedules publish
```
`ask_user` event data: `{"question_id": str, "question": str, "field": str | None, "language_code": str}`.

## agastya/agent/tools.py
```python
@dataclass
class ToolContext:
    profile: ProfileStore
    browser: BrowserDriver
    bus: EventBus
    ask_user: Callable[[str, str | None], Awaitable[str]]   # (question, field) -> answer text
    playbooks: dict[str, Playbook]

def build_mcp_server(ctx: ToolContext):  # -> claude_agent_sdk.create_sdk_mcp_server(name="agastya", tools=[...])
TOOL_NAMES: list[str]   # fully-qualified "mcp__agastya__<name>" for allowed_tools
```
Tools (each returns {"content":[{"type":"text","text": <json or text>}]}, errors -> is_error):
- `get_profile()` -> all fields, sensitive ones MASKED
- `get_profile_value(field)` -> real value (used only to fill forms)
- `save_profile_field(field, value)` -> validates via ProfileStore.set; emits profile_updated
- `ask_user(question, field=None)` -> blocks until the user answers; if field given and answer valid, saves it
- `list_playbooks()` / `get_playbook(id)`
- `browser_goto(url)`, `browser_snapshot()`, `browser_fill(ref, value)`, `browser_select(ref, option)`,
  `browser_click(ref)` (BlockedActionError -> emits "blocked" + returns text telling the agent to hand over to the user),
  `browser_screenshot()` (emits screenshot event)
- `handoff_to_user(reason)` -> emits status {"handoff": true, "reason"}; agent must stop acting

## agastya/agent/session.py
```python
class AgentSession:
    def __init__(self, user_id: str = "default", profile: ProfileStore | None = None,
                 browser: BrowserDriver | None = None, bus: EventBus | None = None,
                 client_factory: Callable[[ClaudeAgentOptions], Any] | None = None): ...
    bus: EventBus
    language_code: str            # last detected user language, default settings.DEFAULT_LANGUAGE
    async def start(self) -> None: ...           # starts browser + ClaudeSDKClient + run loop task
    async def stop(self) -> None: ...
    async def send_user_message(self, text: str, language_code: str | None = None) -> None: ...  # enqueue
    async def answer_question(self, question_id: str, answer: str) -> None: ...  # resolves pending ask_user
```
Loop: inbox queue -> client.query(text) -> stream response -> AssistantMessage TextBlocks emitted as
`assistant_text {"text","language_code"}`; ToolUseBlocks as tool_call; ResultMessage -> `done`.
Messages arriving while a pending ask_user exists are treated as its answer.
Model = settings.AGENT_MODEL. Built-in Claude Code tools disabled; only TOOL_NAMES allowed.
System prompt in agastya/agent/prompts.py: SYSTEM_PROMPT (reply in the user's language, short spoken
sentences, use profile first, ask only for missing fields one at a time, never submit/OTP/login/pay —
hand over instead, read back filled values for confirmation, mask sensitive data when speaking).

## agastya/portals/
```python
@dataclass
class Playbook:
    id: str; portal: str; title_en: str; title_hi: str
    live_url: str               # real government URL
    fixture: str | None         # tests/fixtures/forms/<file>.html
    requires_login: bool
    required_fields: list[str]  # keys from agastya.profile.fields.FIELDS
    optional_fields: list[str]
    field_hints: dict[str, list[str]]   # profile key -> label/name/id substrings to match on the page
    steps: list[str]            # natural-language steps for the agent
    stop_before: list[str]      # button texts where the agent must hand over
    keywords: list[str]         # en + hi words for matching user intent

def load_playbooks(directory: Path | None = None) -> dict[str, Playbook]: ...   # *.yaml in agastya/portals/playbooks
def match_playbook(query: str, playbooks: dict[str, Playbook]) -> list[Playbook]: ...  # keyword score, best first
```

## agastya/voice/pipeline.py
```python
class VoicePipeline:
    """Glue between audio and an AgentSession. Used by BOTH the web server and the CLI."""
    def __init__(self, session: AgentSession, stt: SarvamSTT | None = None, tts: SarvamTTS | None = None): ...
    async def handle_audio(self, audio: bytes, filename: str = "audio.wav") -> Transcript:
        # STT (auto-detect) -> emits event "transcript" {"text","language_code"} on session.bus
        # -> session.send_user_message(text, language_code). Empty transcript -> emit status {"status":"no_speech"} and do not send.
    async def handle_text(self, text: str, language_code: str | None = None) -> None
    async def speak(self, text: str, language_code: str) -> bytes   # TTS WAV; strips markdown (* # _ ` bullets) first
```
New EventType values (add to events.py Literal): "transcript", "tts".
`tts` event data: `{"audio_b64": str, "mime": "audio/wav", "text": str, "language_code": str}`.
`auto_speak`: pipeline subscribes to session.bus and for every `assistant_text` and `ask_user` event
calls speak() and emits a `tts` event (in order, one at a time; TTS failure -> `error` event, never crash).

## agastya/server/app.py  (FastAPI)
```python
def create_app(session_factory: Callable[[str], AgentSession] | None = None,
               pipeline_factory: Callable[[AgentSession], VoicePipeline] | None = None) -> FastAPI
app = create_app()        # for `uvicorn agastya.server.app:app`
```
- `GET /` -> static/index.html ; `/static/*` -> agastya/server/static
- `GET /api/health` -> {"ok": true}
- `GET /api/languages` -> SUPPORTED_LANGUAGES
- `GET /api/playbooks` -> [{id, portal, title_en, title_hi, live_url, requires_login}]
- `GET /api/profile?user_id=default` -> {field: {value (MASKED if sensitive), label_en, label_hi, source, updated_at}} for ALL FIELDS (value null if unset)
- `PUT /api/profile/{field}?user_id=` body {"value": str} -> 200 saved (masked) | 422 {"detail": validator message}
- `DELETE /api/profile/{field}?user_id=`
- `WS /ws?user_id=default` — one AgentSession + VoicePipeline per connection, started on connect, stopped on disconnect.
  client -> server JSON:
    {"type":"text","text":str,"language_code":str|null}
    {"type":"audio","audio_b64":str,"mime":"audio/wav"}         # 16 kHz mono WAV recorded in-browser
    {"type":"answer","question_id":str,"answer":str}
    {"type":"set_language","language_code":str}
    {"type":"tts","enabled":bool}                                  # toggle auto_speak
  server -> client: every AgentEvent.to_json() i.e. {"type","data","ts"}
```

## agastya/cli.py  (`python -m agastya`)
```
python -m agastya chat  [--user default] [--lang hi-IN] [--voice] [--headed]
python -m agastya profile show|set <field> <value>|delete <field> [--user]
python -m agastya serve [--host 127.0.0.1] [--port 8000]
```
--voice: push-to-talk: Enter starts recording via `arecord -q -f S16_LE -r 16000 -c 1 -t wav`, Enter stops;
replies played with `paplay` (fallback `aplay`). Text mode prints assistant_text / ask_user / blocked / handoff
nicely (rich-free, plain ANSI) and reads the next line from stdin.

## Batch D changes
### Grouped questions (ask_user)
Tool `ask_user(question, fields: list[str] = [])` (the old single `field` arg stays accepted for compatibility and
is treated as `fields=[field]`). Ask 2–3 RELATED missing fields per question (e.g. name + father's name + DOB;
address block; mobile + email). After the answer the TOOL extracts values:
  - 1 field: whole answer -> save (normalise/validate via ProfileStore.set).
  - >1 fields: the tool returns the raw answer plus `{"fields": [...], "saved": {...}, "unparsed": [...]}`;
    deterministic extraction for pattern fields (pan, aadhaar, mobile, email, pincode, ifsc, epic, dates);
    the AGENT then calls save_profile_field for remaining fields it understood from the answer.
`AgentSession.ask_user(question, fields: list[str] | None)` — keep accepting a str for the old `field` param.
`ask_user` event data: `{"question_id", "question", "field": first-or-None, "fields": [...], "language_code"}`.

### Faster speech (VoicePipeline)
`assistant_text` / `ask_user` text is split into sentences (strip_markdown first; split on । . ? ! and newlines,
merging pieces < 25 chars). Synthesize sentence 1 immediately and emit it as its own `tts` event, then the next.
TTS of sentence N+1 may start while N is being emitted (prefetch 1), but emission order is strict.
`tts` data gains `"seq": int` (per source event) and `"final": bool`.

### Warm start
`AgentSession.start()` must emit `status {"status":"ready"}` when the client is connected and the browser started.
Server: session started on WS connect (already) — client shows "तैयार हो रहा है… / Warming up…" until `ready`.
