"""Agastya Agent package."""

from agastya.agent.events import AgentEvent, EventBus, EventType
from agastya.agent.prompts import SYSTEM_PROMPT
from agastya.agent.session import AgentSession

__all__ = [
    "AgentEvent",
    "AgentSession",
    "EventBus",
    "EventType",
    "SYSTEM_PROMPT",
]

try:
    from agastya.agent.tools import TOOL_NAMES, ToolContext, build_mcp_server

    __all__.extend(["TOOL_NAMES", "ToolContext", "build_mcp_server"])
except Exception:
    pass
