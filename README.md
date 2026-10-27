# Agastya Assist (अगस्त्य सहायक)

Agastya Assist is a voice-first AI assistant built for Indian citizens navigating complex government portals. Powered by Sarvam AI speech-to-text and text-to-speech across 11 Indian languages, Agastya pairs with an autonomous Claude agent (`claude-sonnet-5-5` via the Claude Agent SDK) that browses official portals with Playwright, fills forms automatically from a secure local citizen profile, and asks aloud only for missing or required fields. Designed with strict safety boundaries, Agastya never clicks final submit buttons, never accesses payment gateways, never handles OTPs, and never auto-logs in—seamlessly handing control back to the citizen for final authorization.

## Features

- **Voice-First in 11 Indian Languages**: Real-time STT and TTS supporting Hindi, English, Bengali, Tamil, Telugu, Kannada, Malayalam, Marathi, Gujarati, Punjabi, and Odia via Sarvam AI.
- **Autonomous Form Automation**: Claude Agent SDK drives Playwright browser sessions using in-process MCP tools and structured YAML playbooks.
- **Smart Profile Management**: Persistent local SQLite profile store; pre-fills forms and groups missing questions intelligently.
- **Non-Negotiable Safety Guardrails**: Built-in browser guard intercepts submit, payment, OTP, login, and e-verify buttons in English and Hindi before execution.
- **Real-Time Live Web Interface**: Responsive 3-column UI featuring live browser screenshot streaming, interactive voice recording, chat log, and inline profile editing.
- **Terminal CLI Support**: Headed or headless CLI mode supporting push-to-talk voice loops (`arecord`/`paplay`) and text chat.

## Supported Portals & Forms

Agastya includes 15 ready-to-use playbooks across four key citizen service domains:

| Portal | Form / Service | Playbook ID | Live URL | Login Needed |
| :--- | :--- | :--- | :--- | :---: |
| **ECI Electoral Search** | Search Electoral Roll | `voter_search` | `https://electoralsearch.eci.gov.in/` | No |
| **ECI Voters Portal** | Digital Voter ID (e-EPIC) | `voter_epic_download` | `https://voters.eci.gov.in/download-epic` | Yes |
| **ECI Voters Portal** | New Voter Registration (Form 6) | `voter_form6` | `https://voters.eci.gov.in/form6` | No |
| **ECI Voters Portal** | Correction & Shifting (Form 8) | `voter_form8` | `https://voters.eci.gov.in/form8` | No |
| **Income Tax e-Filing** | File ITR-1 (Sahaj) Return | `itr1_prefill` | `https://eportal.incometax.gov.in/iec/foservices/#/dashboard/itr-filing` | Yes |
| **Income Tax e-Filing** | Refund & Return Status | `itr_refund_status` | `https://eportal.incometax.gov.in/iec/foservices/#/pre-login/itrStatus` | No |
| **Income Tax e-Filing** | New User Registration | `itr_register` | `https://eportal.incometax.gov.in/iec/foservices/#/pre-login/register` | No |
| **Income Tax e-Filing** | Link PAN with Aadhaar | `pan_aadhaar_link` | `https://eportal.incometax.gov.in/iec/foservices/#/pre-login/bl-link-aadhaar` | No |
| **Income Tax e-Filing** | Instant e-PAN via Aadhaar | `pan_instant_epan` | `https://eportal.incometax.gov.in/iec/foservices/#/pre-login/instant-e-pan` | No |
| **Income Tax (Fixture)** | ITR Login & Form (Test Fixture) | `itr_fixture` | `https://eportal.incometax.gov.in/iec/foservices/#/login` | Yes |
| **MCA Portal** | Company / LLP Master Data | `mca_company_master` | `https://www.mca.gov.in/content/mca/global/en/mca/master-data/MDS.html` | No |
| **MCA Portal** | Director DIN / DIR-3 KYC | `mca_din_dir3` | `https://www.mca.gov.in/mcafoportal/showDir3Kyc.do` | Yes |
| **MCA Portal** | SPICe+ Name Reservation | `mca_spice_name` | `https://www.mca.gov.in/mcafoportal/showSpicePlusA.do` | No |
| **Protean PAN (NSDL)** | New PAN Application (Form 49A) | `pan_new_49a` | `https://onlineservices.proteantech.in/paam/endUserRegisterContact.html` | No |
| **Protean PAN (NSDL)** | PAN Data Correction / Reprint | `pan_correction` | `https://onlineservices.proteantech.in/paam/endUserRegisterContact.html` | No |

## Quick Start

### 1. Installation

```bash
# Clone and enter repo (do not run git commands)
cd web_auto

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate
uv pip install -e '.[dev]'

# Install Chromium browser binaries for Playwright
playwright install chromium
```

### 2. Environment Configuration

Create a `.env` file in the project root:

```bash
# Sarvam AI API Key for STT and TTS
SARVAM_API_KEY=your_sarvam_api_key_here

# Claude Agent SDK model (defaults to claude-sonnet-5-5)
AGENT_MODEL=claude-sonnet-5-5

# Local SQLite directory and default language
DATA_DIR=./data
DEFAULT_LANGUAGE=hi-IN
HEADLESS=true
BROWSER_CHANNEL=          # "chrome" = use the installed Google Chrome
```

**Live portals:** some portals (Income Tax e-Filing, MCA) refuse headless browsers and show
"Permission Denied". For real use and demos run the browser visibly in the citizen's own Chrome:
`HEADLESS=false` and `BROWSER_CHANNEL=chrome` (or `python -m agastya chat --headed`).
Results of the live dummy-data runs are in [docs/live_runs.md](docs/live_runs.md).

Authenticate with Claude Code for Agent SDK access:
```bash
claude login
```

## Running the Application

### Web Interface

Start the FastAPI and WebSocket server:

```bash
python -m agastya serve --port 8000
```
Open **http://127.0.0.1:8000** in your browser. The UI provides a live Playwright mirror, voice recorder, chat stream, and interactive citizen profile.

### Command-Line Interface (CLI)

```bash
# Interactive text chat with live agent
python -m agastya chat

# Voice push-to-talk mode (press Enter to speak, Enter to stop)
python -m agastya chat --voice --lang hi-IN

# Headed browser mode (view browser automation live)
python -m agastya chat --headed --verbose
```

## Profile Management

Citizen profiles are stored locally in SQLite (`data/profile.db`). Manage them via CLI:

```bash
# Display profile with PII fields masked
python -m agastya profile show

# Set or update validated profile field
python -m agastya profile set full_name "Ramesh Chandra Verma"
python -m agastya profile set aadhaar "234567890123"
python -m agastya profile set pan "ABCDE1234F"
python -m agastya profile set mobile "9876543210"

# Delete a profile field
python -m agastya profile delete aadhaar
```

## Testing & Verification

```bash
# Run non-live unit tests (470+ tests, runs offline with mocks and fixtures)
.venv/bin/python -m pytest -q -m "not live and not e2e"

# Run end-to-end agent fixture tests (uses Claude Agent SDK + local HTML forms)
.venv/bin/python -m pytest -q -m e2e

# Run live network tests against external endpoints
.venv/bin/python -m pytest -q -m live

# Run live smoke script across all 4 primary portals (saves screenshots to data/smoke/)
python scripts/live_smoke.py
```

## Safety & Privacy Architecture

- **Deterministic Browser Guard**: Evaluates all element clicks and page navigations against safety rules before execution:
  - **Click Blocks**: Blocks buttons containing submit, final submit, pay now, proceed to pay, send OTP, verify, e-verify, sign in, or captcha refresh in both English and Hindi (`जमा करें`, `भुगतान`, `सत्यापित`, `ओटीपी`, `लॉगिन`).
  - **Navigation Blocks**: Intercepts redirections to known payment gateways (`paygov`, `billdesk`, `razorpay`, `payu`, `sbiepay`, `ccavenue`) and logout paths.
  - **Allowed Actions**: Explicitly permits safe traversal: `next`, `continue`, `save draft`, `back`, `search`, and `select`.
- **Local-First Citizen Data**: All citizen PII resides exclusively in a local SQLite database (`data/profile.db`). No profile details are transmitted to external servers.
- **PII Masking**: Sensitive fields are automatically masked in speech output, CLI displays, web UI, and the agent's `get_profile` tool:
  - **Aadhaar**: Displays only last 4 digits (`XXXXXXXX9012`).
  - **PAN**: Displays masked format (`XXXXX1234X`).
  - **Bank Account**: Displays only last 4 digits (`XXXXXXXX9012`).

## Project Layout

```
web_auto/
├── agastya/
│   ├── agent/             # Claude Agent SDK loop, prompts, MCP tools, and event bus
│   ├── browser/           # Playwright driver, DOM snapshotting, and safety guard
│   ├── portals/           # Playbook loader and 15 YAML government portal playbooks
│   │   └── playbooks/     # Form definitions, field hints, and step-by-step guides
│   ├── profile/           # SQLite profile store, field catalog, validation, masking
│   ├── server/            # FastAPI app, WebSocket dispatch, and web UI static assets
│   │   └── static/        # HTML5, CSS, JS frontend with Web Audio Worklet recorder
│   ├── voice/             # Sarvam STT/TTS clients, pipeline, audio I/O, WAV helpers
│   ├── cli.py             # CLI commands: chat, profile, serve
│   └── config.py          # Settings management via pydantic-settings
├── data/                  # Local SQLite database and smoke test output screenshots
├── docs/                  # Architecture and hackathon demo documentation
├── scripts/               # Live portal verification and smoke testing scripts
└── tests/                 # Unit, fixture, and end-to-end pytest suites
```
