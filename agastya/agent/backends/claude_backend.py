"""Claude Agent SDK backend for Agastya Assist."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from agastya.agent.backends.base import BackendEvent
from agastya.agent.hooks import (
    PLAYWRIGHT_ALLOWED_TOOLS,
    PLAYWRIGHT_DISALLOWED_TOOLS,
    build_hooks,
)
from agastya.agent.prompts import SYSTEM_PROMPT
from agastya.config import get_settings

if TYPE_CHECKING:
    from agastya.agent.session import AgentSession

logger = logging.getLogger(__name__)

BUILTIN_DISALLOWED_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "WebFetch",
    "WebSearch",
    "Glob",
    "Grep",
    "Task",
]


class ClaudeBackend:
    """Agent backend driven by Anthropic's Claude Agent SDK."""

    def __init__(
        self,
        session: AgentSession,
        client_factory: Callable[[ClaudeAgentOptions], Any] | None = None,
    ) -> None:
        self.session = session
        self.client_factory = client_factory
        self.client: Any = None
        self.options: ClaudeAgentOptions | None = None

    async def start(self) -> None:
        """Configure ClaudeAgentOptions and connect client."""
        settings = get_settings()
        from agastya.agent.tools import TOOL_NAMES, ToolContext, build_mcp_server

        ctx_kwargs: dict[str, Any] = {
            "profile": self.session.profile,
            "browser": self.session.browser,
            "bus": self.session.bus,
            "ask_user": self.session.ask_user,
            "playbooks": self.session.playbooks or {},
        }
        if dataclasses.is_dataclass(ToolContext):
            field_names = {f.name for f in dataclasses.fields(ToolContext)}
            if "is_user_in_control" in field_names:
                ctx_kwargs["is_user_in_control"] = lambda: self.session.user_in_control

        ctx = ToolContext(**ctx_kwargs)
        if not hasattr(ctx, "is_user_in_control"):
            try:
                setattr(ctx, "is_user_in_control", lambda: self.session.user_in_control)
            except Exception:
                pass

        mcp_servers: dict[str, Any] = {"agastya": build_mcp_server(ctx)}

        playwright_enabled = bool(getattr(settings, "PLAYWRIGHT_MCP", False))
        if playwright_enabled:
            # repo root: agastya/agent/backends/claude_backend.py -> 4 levels up
            repo_root = Path(__file__).resolve().parent.parent.parent.parent
            playwright_bin = repo_root / "mcp" / "node_modules" / ".bin" / "playwright-mcp"
            cdp_endpoint = getattr(self.session.browser, "cdp_endpoint", None)
            if not cdp_endpoint:
                cdp_port = (
                    getattr(self.session.browser, "cdp_port", 0)
                    or getattr(settings, "CDP_PORT", 0)
                    or 9222
                )
                cdp_endpoint = f"http://127.0.0.1:{cdp_port}"
            mcp_servers["playwright"] = {
                "type": "stdio",
                "command": str(playwright_bin),
                "args": ["--cdp-endpoint", cdp_endpoint],
            }
            allowed_tools = list(TOOL_NAMES) + list(PLAYWRIGHT_ALLOWED_TOOLS)
            disallowed_tools = list(BUILTIN_DISALLOWED_TOOLS) + list(PLAYWRIGHT_DISALLOWED_TOOLS)
        else:
            allowed_tools = list(TOOL_NAMES)
            disallowed_tools = list(BUILTIN_DISALLOWED_TOOLS)

        hooks = build_hooks(self.session)

        agent_cwd = Path(settings.DATA_DIR).resolve() / "agent_home"
        agent_cwd.mkdir(parents=True, exist_ok=True)

        self.options = ClaudeAgentOptions(
            model=settings.AGENT_MODEL,
            setting_sources=[],
            skills=[],
            cwd=str(agent_cwd),
            system_prompt=SYSTEM_PROMPT,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            tools=[],
            disallowed_tools=disallowed_tools,
            hooks=hooks,
            effort=getattr(settings, "AGENT_EFFORT", "low"),
        )

        if self.client_factory is not None:
            self.client = self.client_factory(self.options)
        else:
            self.client = ClaudeSDKClient(self.options)

        if hasattr(self.client, "connect") and callable(self.client.connect):
            await self.client.connect()

    async def run_turn(self, text: str) -> AsyncIterator[BackendEvent]:
        """Send prompt to Claude SDK and yield text, tool_call, and done events."""
        await self.client.query(text)
        done_emitted = False
        async for msg in self.client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in getattr(msg, "content", []):
                    if isinstance(block, TextBlock):
                        yield ("text", block.text)
                    elif isinstance(block, ToolUseBlock):
                        yield (
                            "tool_call",
                            {"id": block.id, "name": block.name, "input": block.input},
                        )
            elif isinstance(msg, ResultMessage):
                yield (
                    "done",
                    {
                        "subtype": getattr(msg, "subtype", "success"),
                        "result": getattr(msg, "result", None),
                        "is_error": getattr(msg, "is_error", False),
                    },
                )
                done_emitted = True

        if not done_emitted:
            yield ("done", {"result": None, "is_error": False})

    async def stop(self) -> None:
        """Disconnect client and cleanup."""
        if self.client is not None:
            if hasattr(self.client, "disconnect") and callable(self.client.disconnect):
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            self.client = None
