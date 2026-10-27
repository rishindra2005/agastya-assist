# Agastya Assist — Build Plan

Voice-first AI assistant that helps Indian citizens use government websites
(Vikas hackathon). The user speaks in any Indian language; a Claude agent
browses the portal, fills forms from the user's saved profile, asks for any
missing details, and STOPS before final submit / OTP / login / captcha /
payment so the human takes over.

## Architecture

```
agastya/
  config.py            settings from .env (pydantic-settings)
  profile/store.py     SQLite user profile (key/value + source + timestamps)
  voice/sarvam.py      Sarvam STT (saarika) + TTS (bulbul), all languages, auto-detect
  browser/driver.py    Playwright async wrapper (navigate, snapshot, fill, click, screenshot)
  browser/guard.py     Safety guard: blocks submit/OTP/pay/login clicks
  agent/tools.py       In-process MCP tools for the Claude Agent SDK
  agent/session.py     Long-running agent loop: inbox queue <- interface, events -> interface
  portals/*.yaml       Playbooks: ITR, MCA, PAN, Elections (URLs, forms, field->profile map)
  server/app.py        FastAPI + WebSocket
  server/static/       Web UI (mic, chat, live browser screenshot, profile panel)
  cli.py               Terminal voice/text loop
tests/                 pytest; fixtures/forms/*.html are local copies of form shapes
```

Agent model: Claude Agent SDK (`claude-agent-sdk`), model `claude-sonnet-5-5`,
auth via the local Claude Code login.

## Chunks

1. Scaffold: pyproject, config, profile store, tests
2. Sarvam STT/TTS client + tests (mocked HTTP)
3. Browser driver + safety guard + fixture forms + tests
4. Agent core (Agent SDK loop, tools, event bus) + tests
5. Portal playbooks (ITR, MCA, PAN, Elections, all main forms) + live smoke tests (marked `live`)
6. FastAPI/WebSocket web UI + CLI voice loop
7. End-to-end tests, README, demo script
