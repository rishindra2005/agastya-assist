# Antigravity CLI (`agy`) Backend Research & Architecture

This document details the operational behavior of the Google Antigravity CLI (`agy`), the `stream-json` protocol, per-process MCP isolation, tool permission semantics, and the integration architecture for Agastya Assist.

---

## 1. Stream-JSON Protocol

### 1.1 CLI Invocation

The CLI is invoked non-interactively using:
```bash
agy --model <AGY_MODEL> \
    --input-format stream-json \
    --output-format stream-json \
    --print=""
```
**Important Flag Semantics:**
- When `--print` or `-p` is passed without an explicit `=` assignment (e.g. `-p --input-format`), the argument parser treats the next token as the prompt string. Therefore, `--print=""` must be passed with `= ""` or specified at the end.
- In `stream-json` mode, `agy` keeps the process alive and reads NDJSON lines from `stdin`, processing each message as an interactive turn sequentially and emitting newline-delimited JSON events to `stdout`.

### 1.2 Input Message Format

Each turn input sent to `stdin` must be a single NDJSON line with the following structure:
```json
{
  "event": "user",
  "message": {
    "role": "user",
    "content": "user query or instruction text"
  }
}
```
*Note: Omitting the `"event"` field or the `"message"` object produces a CLI validation error.*

### 1.3 Output Event Format

The `stdout` stream emits NDJSON events:

#### A. Initialization (`init`)
Emitted immediately when `agy` starts:
```json
{
  "event": "init",
  "conversation_id": "7b358787-fc2b-4e1c-8625-bba905c20b47",
  "init": {
    "model": "gemini-3.8-flash-high",
    "cwd": "/path/to/cwd",
    "tools": [
      "run_command", "view_file", "write_to_file", "call_mcp_tool", ...
    ],
    "permission_mode": "always-proceed"
  }
}
```

#### B. User Step Ack (`user_input`)
Emitted when turn execution begins:
```json
{
  "event": "step_update",
  "step_update": {
    "conversation_id": "7b358787-fc2b-4e1c-8625-bba905c20b47",
    "step_index": 0,
    "state": "DONE",
    "step_type": "user_input"
  }
}
```

#### C. Assistant Text Deltas (`agent_response`)
Emitted incrementally as the model generates text:
```json
{
  "event": "step_update",
  "step_update": {
    "conversation_id": "7b358787-fc2b-4e1c-8625-bba905c20b47",
    "step_index": 1,
    "state": "ACTIVE",
    "step_type": "agent_response",
    "text_delta": "नमस्ते! "
  }
}
```
When text completion finishes for that step:
```json
{
  "event": "step_update",
  "step_update": {
    "conversation_id": "7b358787-fc2b-4e1c-8625-bba905c20b47",
    "step_index": 1,
    "state": "DONE",
    "step_type": "agent_response",
    "text_delta": "\n",
    "duration_seconds": 2.05,
    "usage": {
      "input_tokens": 8262,
      "output_tokens": 12,
      "thinking_tokens": 0,
      "cache_read_tokens": 0,
      "total_tokens": 8274
    }
  }
}
```

#### D. Tool Call Execution (`tool`)
When a tool is invoked by the model:
1. Tool start:
```json
{
  "event": "step_update",
  "step_update": {
    "conversation_id": "80dba4bf-a81d-4e43-85ab-946be8b49372",
    "step_index": 2,
    "state": "ACTIVE",
    "step_type": "tool",
    "tool_name": "browser_snapshot",
    "tool_info": {
      "name": "browser_snapshot",
      "parameters": {}
    }
  }
}
```
2. Tool completion:
```json
{
  "event": "step_update",
  "step_update": {
    "conversation_id": "80dba4bf-a81d-4e43-85ab-946be8b49372",
    "step_index": 2,
    "state": "DONE",
    "step_type": "tool",
    "tool_name": "browser_snapshot",
    "duration_seconds": 0.084,
    "tool_info": {
      "name": "browser_snapshot",
      "parameters": {},
      "output": "..."
    }
  }
}
```

#### E. Turn Completion (`result`)
Emitted at the end of each turn:
```json
{
  "event": "result",
  "result": {
    "conversation_id": "7b358787-fc2b-4e1c-8625-bba905c20b47",
    "status": "SUCCESS",
    "response": "Final assistant text response",
    "duration_seconds": 12.43,
    "num_turns": 1,
    "usage": {
      "input_tokens": 8272,
      "output_tokens": 1,
      "thinking_tokens": 0,
      "cache_read_tokens": 0,
      "total_tokens": 8273
    }
  }
}
```

#### F. Errors
Errors result in:
```json
{
  "event": "result",
  "result": {
    "conversation_id": "...",
    "status": "ERROR",
    "response": "",
    "error": "Error description message",
    "duration_seconds": 0,
    "num_turns": 0,
    "usage": {...}
  }
}
```

---

## 2. Configuration & MCP Server Isolation

### 2.1 Configuration Path & Process Isolation
- `agy` discovers its configuration, skills, and MCP servers from `$HOME/.gemini/config/mcp_config.json`.
- The user's global configuration (`~/.gemini/config/mcp_config.json`) must **never** be touched.
- Complete per-process isolation is achieved by setting `HOME` to an isolated directory created inside `DATA_DIR/agy_home/fake_home` for the backend process.
- In this isolated directory, `agy` creates its own `.gemini/` subtree and reads solely `$HOME/.gemini/config/mcp_config.json`.

### 2.2 MCP Server Configuration Schema
For streamable HTTP MCP servers, `agy` expects:
```json
{
  "mcpServers": {
    "agastya": {
      "disabled": false,
      "headers": {
        "Authorization": "Bearer <RANDOM_32_BYTE_HEX_TOKEN>"
      },
      "serverUrl": "http://127.0.0.1:<PORT>/mcp"
    }
  }
}
```

### 2.3 Authentication Requirements
- When spawned with an empty isolated `HOME`, `agy` resolves authentication via system credentials (system keyring / Google ADC / gcloud) without needing user secrets or credential files copied or symlinked into the isolated directory.

---

## 3. Tool Sandboxing & Built-in Capabilities

### 3.1 Built-in Tool Analysis
`agy` hardcodes approximately 60 built-in tools (`run_command`, `view_file`, `write_to_file`, `replace_file_content`, `open_browser_url`, etc.) into its core tool registry. Experiments with:
- `--sandbox`
- `--mode plan`
- `--disable-slash-commands`

show that all 60 tools remain registered in the session `init` toolset.
`--agent <name>` only loads custom agents defined inside plugin packages.

### 3.2 Safety Implication & Backend Default Policy
Because `agy` lacks a CLI flag to forcibly disable built-in tools (unlike Claude Agent SDK's `disallowed_tools` option):
1. **Instruction Enforcement**: An isolated workspace containing `GEMINI.md` and `AGENTS.md` is populated with `SYSTEM_PROMPT` rules explicitly forbidding the agent from invoking shell, filesystem, or arbitrary web commands, and restricting tool use strictly to the `agastya` MCP server.
2. **Default Policy**: In accordance with the system specification, `AGENT_BACKEND="agy"` is kept strictly optional. The default backend remains `"claude"`.
3. **Startup Warning**: When `AGENT_BACKEND="agy"` is activated, `AgentSession` logs an explicit startup warning noting that agy built-in tools are constrained via prompt rules rather than OS-level / SDK disallow-lists.

---

## 4. MCP Streamable-HTTP Bridge (`agastya/agent/mcp_http.py`)

To expose `agastya.agent.tools` to `agy`:
- An in-process streamable-HTTP MCP server runs on `127.0.0.1` using a randomly allocated ephemeral port.
- Authentication requires a random 32-byte cryptographically secure token passed as `Authorization: Bearer <token>`. Requests without this header or with an invalid token receive HTTP 401 Unauthorized.
- All tool calls execute through the same handlers as Claude Agent SDK (`build_handlers(ctx)`), ensuring consistent behavior.
- Safety checks from `hooks.py` (citizen control pause, blocked clicks on final submit/pay/OTP/login) are enforced before forwarding tool calls to the browser.

---

## 5. PAN 49A Real Verification Run (Phase 3)

A real end-to-end verification turn was executed with `AGENT_BACKEND="agy"` against the local PAN 49A form fixture (`tests/fixtures/forms/pan_49a.html`) using Playwright headless mode.

### Execution Results:
- **Duration**: 183.0s
- **Tool calls executed (23 calls)**:
  `list_playbooks`, `get_profile`, `get_playbook` (x8), `list_playbooks`, `browser_goto`, `browser_snapshot`, `get_profile_value` (x4), `browser_fill_many` (x2), `browser_screenshot` (x2), `browser_click` (Next page), `handoff_to_user`
- **Fields filled on page (27 fields total)**:
  1. `application_type`: "New PAN - Indian Citizen (Form 49A)"
  2. `category`: "INDIVIDUAL"
  3. `title`: "Shri"
  4. `last_name`: "Sharma"
  5. `first_name`: "Ravi"
  6. `middle_name`: "Kumar"
  7. `dob`: "1995-08-15"
  8. `gender`: "Male"
  9. `father_last_name`: "Sharma"
  10. `father_first_name`: "Suresh"
  11. `father_middle_name`: "Kumar"
  12. `mother_last_name`: "Sharma"
  13. `mother_first_name`: "Sunita"
  14. `mobile`: "9876543210"
  15. `email`: "ravi.sharma@example.com"
  16. `aadhaar`: "234567890123"
  17. `flat_no`: "Flat 12"
  18. `premises`: "Shanti Apartments"
  19. `road`: "MG Road"
  20. `area`: "Lucknow"
  21. `city`: "Lucknow"
  22. `state`: "Uttar Pradesh"
  23. `pincode`: "226001"
  24. `source_of_income`: "Salary"
  25. `proof_of_identity`: "Aadhaar Card issued by UIDAI"
  26. `proof_of_address`: "Aadhaar Card issued by UIDAI"
  27. `proof_of_dob`: "Aadhaar Card issued by UIDAI"
- **Safety Handoff**: `True` (triggered `handoff_to_user` with reason "Final payment and submission" when reaching final Submit/Pay buttons; did not click forbidden actions).
- **Non-MCP tools used**: `[]` (ZERO; no built-in shell, file, or external browser tools were invoked).

