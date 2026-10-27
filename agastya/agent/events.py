"""Agent event bus and event types for Agastya Assist."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal

EventType = Literal[
    "status",
    "assistant_text",
    "ask_user",
    "tool_call",
    "tool_result",
    "screenshot",
    "blocked",
    "profile_updated",
    "error",
    "done",
    "transcript",
    "tts",
    "document_uploaded",
]


@dataclass
class AgentEvent:
    """An event emitted during agent execution."""

    type: EventType
    data: dict
    ts: float = field(default_factory=time.time)

    def to_json(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "type": self.type,
            "data": self.data,
            "ts": self.ts,
        }


class EventBus:
    """In-process async event bus for agent lifecycle events."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[AgentEvent]] = set()
        self._tasks: set[asyncio.Task] = set()

    def subscribe(self) -> asyncio.Queue[AgentEvent]:
        """Subscribe to all agent events. Each subscriber gets every event."""
        q: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[AgentEvent]) -> None:
        """Unsubscribe a previously subscribed queue."""
        self._subscribers.discard(q)

    async def publish(self, event: AgentEvent) -> None:
        """Publish an event to all subscribers."""
        for q in list(self._subscribers):
            await q.put(event)

    def emit(self, type: EventType, **data) -> None:
        """Sync helper that creates an AgentEvent and schedules publish."""
        event = AgentEvent(type=type, data=data, ts=time.time())
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self.publish(event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except RuntimeError:
            for q in list(self._subscribers):
                q.put_nowait(event)
