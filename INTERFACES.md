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

## Batch E — agent browser over CDP, Playwright MCP, live stream + human control

### Browser driver (agastya/browser/driver.py)
- Launch adds `--remote-debugging-port=<port>` (settings.CDP_PORT, 0 => find a free port with socket bind).
  Properties: `cdp_port: int`, `cdp_endpoint: str` ("http://127.0.0.1:<port>"), `context`, `page` (always the
  ACTIVE tab: when a new page/popup opens in the context, it becomes `page` and `on_page_change` callbacks fire).
  `def add_page_change_listener(cb: Callable[[Page], Awaitable[None] | None])`.
- On context creation call `await install_page_guard(context)` (agastya/browser/page_guard.py).

### In-page guard (agastya/browser/page_guard.py + page_guard.js) — second safety layer
```python
async def install_page_guard(context: BrowserContext) -> None   # add_init_script(page_guard.js) + apply to open pages
async def set_agent_active(context: BrowserContext, active: bool) -> None  # sets window.__agastyaAgentActive on all pages/frames
```
page_guard.js: when `window.__agastyaAgentActive === true` it blocks (capture phase, preventDefault +
stopImmediatePropagation) clicks on elements whose text/value/aria-label/id/name match the final-action rules
(port the regexes from guard.py: submit, pay, e-verify, otp, login, captcha…, Hindi), blocks `submit` events and
overrides HTMLFormElement.prototype.submit/requestSubmit to no-op while agent-active, and records blocked attempts
in `window.__agastyaBlocked` (array of {text, ts}). When the flag is false (HUMAN in control) nothing is blocked.

### Live stream (agastya/browser/live.py)
```python
class LiveView:
    def __init__(self, driver: BrowserDriver, quality: int | None = None, max_width: int | None = None): ...
    async def start(self, on_frame: Callable[[bytes, dict], Awaitable[None]]) -> None
        # CDP Page.startScreencast(jpeg) on driver.page; ack every frame; on_frame(jpeg_bytes, meta)
        # meta = {"width": css px, "height": css px, "url": str, "title": str}; restarts on page change
    async def stop(self) -> None
    async def mouse(self, kind: Literal["move","down","up","click","wheel"], x: float, y: float,
                    button: str = "left", delta_y: float = 0, click_count: int = 1) -> None   # CSS px in viewport
    async def key(self, kind: Literal["down","up","press"], key: str, code: str = "", text: str = "") -> None
    async def insert_text(self, text: str) -> None
    async def nav(self, action: Literal["back","forward","reload"]) -> None
```
Input goes through CDP Input.dispatchMouseEvent / dispatchKeyEvent / insertText (human actions are NOT guarded).

### Human control
`AgentSession.user_in_control: bool` (default False). `async def take_control()` -> True, set_agent_active(False),
emit status {"status":"user_control"}. `async def release_control(note: str = "")` -> False and sends the agent a
message "[user finished on the page: <note>] Continue from the current page." (in user language).
While the agent is running a turn: set_agent_active(True); after the turn: set_agent_active(False).
ToolContext gains `is_user_in_control: Callable[[], bool] = lambda: False`; every browser_* tool returns
"PAUSED: the citizen is controlling the browser. Wait." when it is True.

### Playwright MCP (agent gets direct DOM tools on the SAME Chrome)
session.py adds when settings.PLAYWRIGHT_MCP:
`mcp_servers["playwright"] = {"type": "stdio", "command": "<repo>/mcp/node_modules/.bin/playwright-mcp",
 "args": ["--cdp-endpoint", driver.cdp_endpoint]}`
Allowed: mcp__playwright__browser_snapshot, browser_click, browser_type, browser_fill_form, browser_select_option,
browser_hover, browser_press_key, browser_wait_for, browser_navigate, browser_navigate_back, browser_tabs,
browser_take_screenshot. Disallowed (deny in hook too): browser_evaluate, browser_run_code, browser_file_upload,
browser_install, browser_close, browser_resize, browser_console_messages, browser_network_requests, browser_drag.
PreToolUse hook (agastya/agent/hooks.py): for mcp__playwright__browser_click/press_key/navigate run guard.check_click
on {"text": input.element} / check_navigation(url); press_key "Enter" denied unless input.element is a search box;
any denied call -> emit "blocked" and return deny with reason telling the agent to call handoff_to_user.

### Server live socket
`WS /ws/live?user_id=` — binary messages = JPEG frames; text JSON messages server->client:
{"type":"meta", width, height, url, title}, {"type":"control", "user_in_control": bool}.
client -> server JSON: {"type":"mouse", kind, x, y, button?, delta_y?, click_count?} (x,y in CSS px of the page),
{"type":"key", kind, key, code?, text?}, {"type":"text", text}, {"type":"nav", action},
{"type":"control", "take": bool, "note"?: str}. Input is ignored (error message) unless user_in_control is true.
The server keeps `app.state.sessions: dict[user_id, (AgentSession, VoicePipeline)]` created by /ws; /ws/live attaches
to it (4404 close if none). Frames dropped (not queued) if the socket is slow — keep only the latest.

## Batch F — demo replica portals, Form 16, installable mobile app
### Demo replica portals (demo_portals/, served by FastAPI at /mock/)
Purpose: realistic, information-dense, deliberately COMPLEX replicas of government flows so the demo shows how Agastya
turns a confusing portal into a conversation. They are fictional and must be unmistakably labelled:
- EVERY html page includes `<link rel="stylesheet" href="/mock/_shared/demo.css">` and `<script src="/mock/_shared/demo.js"></script>`
  (supervisor-owned: red "DEMO REPLICA — NOT A GOVERNMENT WEBSITE" bar, watermark, footer disclaimer, `[DEMO]` title prefix).
- NO real emblems, logos, seals, real department URLs in links, or real helpline numbers. Portal names end with "(Demo Replica)".
- Use `DemoPortal.captcha(el)` for captchas, `DemoPortal.sendOtp(label)` for OTPs (shown on an on-screen demo phone),
  `DemoPortal.fakeNumber(prefix, digits)` for acknowledgement numbers, `DemoPortal.save/load` to carry data between pages.
- Final submit / pay / e-verify only works after captcha + OTP are entered (by the CITIZEN via the live view); it leads to an
  acknowledgement page that echoes the entered data with a fake ack number. Mock payment page says "Demo payment — no money moves".
- Realism: dense instructions, info icons with long help text, notes in small print, "Important" boxes, tabs, accordions, required
  markers, inline validation messages in English + Hindi, session-timeout countdown banner, dependent dropdowns.
- Accessibility: real <label for>, ids/names, keyboard operable (the agent and the citizen both drive it).
Directory: demo_portals/{pan,itr,voter,mca}/index.html (+ more pages), demo_portals/index.html (list of replicas).
Playbooks: agastya/portals/playbooks/demo_*.yaml with live_url "http://127.0.0.1:8000/mock/<portal>/...", keywords
include the word "demo"/"डेमो" so real and demo playbooks are distinguishable; `portal` ends with "(Demo Replica)".

### Documents / Form 16 (agastya/documents/)
```python
@dataclass
class Document: id: str; user_id: str; filename: str; kind: str; path: str; uploaded_at: str; extracted: dict
class DocumentStore:  # files under DATA_DIR/documents/<user_id>/, index in SQLite table 'documents' (same profile.db file is fine)
    def __init__(self, user_id: str = "default", data_dir: Path | None = None)
    def add(self, filename: str, data: bytes) -> Document          # detects kind, runs extraction
    def get(self, doc_id: str) -> Document | None
    def list(self) -> list[Document]
def extract_form16(pdf_bytes: bytes) -> dict
# -> {"kind":"form16","assessment_year","employer_name","employer_tan","employee_pan","employee_name",
#     "period_from","period_to","gross_salary","salary_17_1","perquisites_17_2","profits_17_3",
#     "hra_exempt_10_13a","standard_deduction_16ia","professional_tax_16iii","income_chargeable_salaries",
#     "deduction_80c","deduction_80d","total_taxable_income","tds_total","quarters":[{"q","amount_paid","tds"}]}
# amounts as int rupees; missing -> None.
```
Agent tools (tools.py): `list_documents()`, `read_document(doc_id)` -> extracted JSON (PAN masked).
Server: `POST /api/documents?user_id=` multipart `file` -> {id, filename, kind, summary}; emits event
`document_uploaded {id, filename, kind}` on that user's session bus if connected, and sends the agent
"[user uploaded document <filename> id=<id> kind=<kind>]". `GET /api/documents?user_id=` -> list.
EventType gains "document_uploaded".

### Installable mobile app (PWA)
`/manifest.webmanifest`, `/sw.js` (cache app shell only, never API/WS), icons `static/icons/icon-192.png`,
`icon-512.png`, `maskable-512.png` (generated), `<meta name="theme-color">`, apple-touch-icon. Install prompt button
"📲 Install app / ऐप इंस्टॉल करें" when `beforeinstallprompt` fires.
