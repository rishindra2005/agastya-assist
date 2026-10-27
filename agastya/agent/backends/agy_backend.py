"""Antigravity CLI (agy) backend for Agastya Assist."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from agastya.agent.backends.base import BackendEvent
from agastya.agent.mcp_http import McpHttpServer
from agastya.agent.prompts import SYSTEM_PROMPT
from agastya.config import get_settings

if TYPE_CHECKING:
    from agastya.agent.session import AgentSession

logger = logging.getLogger(__name__)


def parse_stream_json_line(line_or_data: str | dict[str, Any]) -> list[BackendEvent]:
    """Parse a single agy stream-json NDJSON line or dict into BackendEvents."""
    if isinstance(line_or_data, str):
        line = line_or_data.strip()
        if not line or not line.startswith("{"):
            return []
        try:
            data = json.loads(line)
        except Exception:
            return []
    elif isinstance(line_or_data, dict):
        data = line_or_data
    else:
        return []

    event_type = data.get("event")
    events: list[BackendEvent] = []

    if event_type == "step_update":
        step_update = data.get("step_update", {})
        step_type = step_update.get("step_type")
        state = step_update.get("state")

        if step_type == "agent_response":
            text_delta = step_update.get("text_delta", "")
            if text_delta:
                events.append(("text", text_delta))

        elif step_type == "tool" and state == "ACTIVE":
            tool_info = step_update.get("tool_info", {})
            raw_tool_name = step_update.get("tool_name") or tool_info.get("name", "")
            parameters = tool_info.get("parameters", {})
            step_index = step_update.get("step_index", 0)

            # If agy invoked our MCP tool via call_mcp_tool, unpack real tool name & args
            if raw_tool_name == "call_mcp_tool":
                real_tool_name = (
                    parameters.get("ToolName")
                    or parameters.get("tool")
                    or raw_tool_name
                )
                real_input = (
                    parameters.get("Arguments")
                    if "Arguments" in parameters
                    else parameters
                )
            else:
                real_tool_name = raw_tool_name
                real_input = parameters

            events.append(
                (
                    "tool_call",
                    {
                        "id": f"agy_tool_{step_index}",
                        "name": real_tool_name,
                        "input": real_input,
                    },
                )
            )

    elif event_type == "result":
        result_info = data.get("result", {})
        status = result_info.get("status", "SUCCESS")
        is_error = status != "SUCCESS"
        response_text = result_info.get("response", "")
        error_msg = result_info.get("error")
        events.append(
            (
                "done",
                {
                    "subtype": "success" if not is_error else "error",
                    "result": response_text,
                    "is_error": is_error,
                    "error": error_msg,
                },
            )
        )

    return events


class AgyBackend:
    """Agent backend driven by Google Antigravity CLI (`agy`)."""

    def __init__(
        self,
        session: AgentSession,
        model: str | None = None,
        turn_timeout: float = 300.0,
    ) -> None:
        self.session = session
        settings = get_settings()
        self.model = model or getattr(settings, "AGY_MODEL", "gemini-3.8-flash-high")
        self.turn_timeout = turn_timeout

        self.mcp_server: McpHttpServer | None = None
        self.process: asyncio.subprocess.Process | None = None

        session_id = uuid.uuid4().hex[:8]
        base_dir = (
            Path(settings.DATA_DIR).resolve()
            / "agy_home"
            / f"sess_{session.user_id}_{session_id}"
        )
        self.home_dir = base_dir / "home"
        self.cwd_dir = base_dir / "cwd"
        self._stopped = False
        self._turn_index = 0

    async def _setup_directories(self) -> None:
        """Create isolated HOME and CWD directories with isolated MCP config."""
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.cwd_dir.mkdir(parents=True, exist_ok=True)

        config_dir = self.home_dir / ".gemini" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        cli_dir = self.home_dir / ".gemini" / "antigravity-cli"
        cli_dir.mkdir(parents=True, exist_ok=True)

        # Set toolPermission always-proceed in isolated home settings
        settings_file = cli_dir / "settings.json"
        settings_file.write_text(
            json.dumps({"toolPermission": "always-proceed"}, indent=2),
            encoding="utf-8",
        )

        # Write per-process isolated mcp_config.json
        if self.mcp_server is not None:
            mcp_config = {
                "mcpServers": {
                    "agastya": {
                        "disabled": False,
                        "headers": {
                            "Authorization": f"Bearer {self.mcp_server.token}"
                        },
                        "serverUrl": self.mcp_server.endpoint_url,
                    }
                }
            }
            config_file = config_dir / "mcp_config.json"
            config_file.write_text(json.dumps(mcp_config, indent=2), encoding="utf-8")

        # Write GEMINI.md in isolated CWD with system prompt & safety instructions
        gemini_md = self.cwd_dir / "GEMINI.md"
        rules_text = (
            f"# Agastya Citizen Agent Instructions\n\n"
            f"{SYSTEM_PROMPT}\n\n"
            f"## Tool Restrictions\n"
            f"- You MUST ONLY call tools provided by the `agastya` MCP server.\n"
            f"- You MUST NEVER execute built-in shell commands (`run_command`), view/edit local files (`view_file`, `write_to_file`, `read_file`), or browse pages outside the `agastya` MCP tools.\n"
        )
        gemini_md.write_text(rules_text, encoding="utf-8")

    async def _start_process(self) -> None:
        """Launch the long-lived agy stream-json process."""
        # agy's built-in shell/file tools cannot be disabled and run without approval here, while the
        # agent reads untrusted portal pages (prompt-injection risk). --sandbox does NOT confine them
        # (verified: it wrote to $HOME). Refuse to run unconfined unless explicitly accepted.
        if not getattr(get_settings(), "AGY_ALLOW_UNSANDBOXED", False):
            raise RuntimeError(
                "agy backend is disabled: its built-in shell/file tools are not OS-sandboxed yet. "
                "Use AGENT_BACKEND=claude, or set AGY_ALLOW_UNSANDBOXED=true to accept the risk."
            )
        cmd = [
            "agy",
            "--model",
            self.model,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
            "--print=",
        ]

        env = dict(os.environ)
        env["HOME"] = str(self.home_dir)

        logger.info("Spawning agy backend process: %s (cwd=%s)", " ".join(cmd), self.cwd_dir)
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd_dir),
            env=env,
        )

        # Wait for the init event line
        try:
            line_bytes = await asyncio.wait_for(self.process.stdout.readline(), timeout=15.0)
            line = line_bytes.decode("utf-8", errors="replace")
            logger.debug("agy init line received: %s", line[:120])
        except Exception as exc:
            logger.warning("Did not receive agy init line: %s", exc)

    async def _ensure_process(self) -> None:
        """Check if agy process is alive; restart if crashed."""
        if self.process is None or self.process.returncode is not None:
            logger.warning("agy backend process not running; restarting...")
            await self._start_process()

    async def start(self) -> None:
        """Start the MCP HTTP server, prepare directories, and spawn the agy process."""
        self._stopped = False
        logger.warning(
            "STARTUP WARNING: AGENT_BACKEND='agy' is active. In agy, built-in tools are constrained "
            "via prompt rules and GEMINI.md in the isolated workspace rather than OS-level capability disallow-lists."
        )

        from agastya.agent.tools import ToolContext

        ctx_kwargs: dict[str, Any] = {
            "profile": self.session.profile,
            "browser": self.session.browser,
            "bus": self.session.bus,
            "ask_user": self.session.ask_user,
            "playbooks": self.session.playbooks or {},
        }
        if hasattr(self.session, "user_in_control"):
            ctx_kwargs["is_user_in_control"] = lambda: self.session.user_in_control

        ctx = ToolContext(**ctx_kwargs)
        self.mcp_server = McpHttpServer(ctx=ctx, session=self.session)
        await self.mcp_server.start()

        await self._setup_directories()
        await self._start_process()

    async def run_turn(self, text: str) -> AsyncIterator[BackendEvent]:
        """Send a turn message to agy stdin and yield BackendEvents from stdout."""
        await self._ensure_process()
        assert self.process is not None and self.process.stdin is not None

        input_payload = {
            "event": "user",
            "message": {
                "role": "user",
                "content": text,
            },
        }
        input_line = json.dumps(input_payload, ensure_ascii=False) + "\n"
        self.process.stdin.write(input_line.encode("utf-8"))
        await self.process.stdin.drain()

        done_received = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.turn_timeout

        while not done_received and not self._stopped:
            remaining = max(0.1, deadline - loop.time())
            try:
                line_bytes = await asyncio.wait_for(
                    self.process.stdout.readline(),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                logger.error("Turn timed out in agy backend after %ss", self.turn_timeout)
                yield (
                    "done",
                    {
                        "subtype": "timeout",
                        "result": None,
                        "is_error": True,
                        "error": f"Turn timed out after {self.turn_timeout}s",
                    },
                )
                # Terminate and kill the hung process
                try:
                    self.process.kill()
                except Exception:
                    pass
                self.process = None
                return

            if not line_bytes:
                logger.warning("agy process stdout closed unexpectedly")
                yield (
                    "done",
                    {
                        "subtype": "crash",
                        "result": None,
                        "is_error": True,
                        "error": "agy backend process terminated unexpectedly",
                    },
                )
                self.process = None
                return

            line_str = line_bytes.decode("utf-8", errors="replace")
            events = parse_stream_json_line(line_str)
            for event in events:
                yield event
                if event[0] == "done":
                    done_received = True

    async def stop(self) -> None:
        """Stop the agy process, the MCP HTTP server, and cleanup."""
        self._stopped = True

        if self.process is not None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

        if self.mcp_server is not None:
            await self.mcp_server.stop()
            self.mcp_server = None

        # Clean up temporary session directories
        try:
            if self.home_dir.parent.exists():
                shutil.rmtree(self.home_dir.parent, ignore_errors=True)
        except Exception:
            pass
