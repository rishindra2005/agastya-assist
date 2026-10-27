"""Base interface and types for agent brain backends."""

from __future__ import annotations

from typing import Any, AsyncIterator, Literal, Protocol, Tuple, Union, runtime_checkable

# BackendEvent = ("text", str) | ("tool_call", {id, name, input}) | ("done", {result, is_error})
BackendEvent = Union[
    Tuple[Literal["text"], str],
    Tuple[Literal["tool_call"], dict[str, Any]],
    Tuple[Literal["done"], dict[str, Any]],
]


@runtime_checkable
class AgentBackend(Protocol):
    """Protocol for an agent backend running multi-turn loops."""

    async def start(self) -> None:
        """Start the backend (spawning child processes, connections, or MCP servers)."""
        ...

    async def stop(self) -> None:
        """Stop the backend and clean up all resources."""
        ...

    def run_turn(self, text: str) -> AsyncIterator[BackendEvent]:
        """Execute a turn with the given user query and stream backend events."""
        ...
