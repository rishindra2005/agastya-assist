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
