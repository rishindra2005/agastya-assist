"""Tests for agent event bus and event types."""

import asyncio
import time
import pytest
from agastya.agent.events import AgentEvent, EventBus, EventType


def test_agent_event_creation():
    event = AgentEvent(type="status", data={"message": "ok"}, ts=12345.0)
    assert event.type == "status"
    assert event.data == {"message": "ok"}
    assert event.ts == 12345.0

    json_dict = event.to_json()
    assert json_dict == {
        "type": "status",
        "data": {"message": "ok"},
        "ts": 12345.0,
    }


def test_agent_event_default_timestamp():
    before = time.time()
    event = AgentEvent(type="assistant_text", data={"text": "hello"})
    after = time.time()
    assert before <= event.ts <= after
    assert event.type == "assistant_text"


@pytest.mark.asyncio
async def test_event_bus_publish_and_subscribe():
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()

    event = AgentEvent(type="assistant_text", data={"text": "Namaste"})
    await bus.publish(event)

    ev1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    ev2 = await asyncio.wait_for(q2.get(), timeout=1.0)

    assert ev1 == event
    assert ev2 == event


@pytest.mark.asyncio
async def test_event_bus_unsubscribe():
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()

    bus.unsubscribe(q1)

    event = AgentEvent(type="done", data={"result": "ok"})
    await bus.publish(event)

    ev2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert ev2 == event
    assert q1.empty()


@pytest.mark.asyncio
async def test_event_bus_emit():
    bus = EventBus()
    q = bus.subscribe()

    bus.emit("ask_user", question_id="q123", question="What is your PAN?", field="pan", language_code="hi-IN")

    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "ask_user"
    assert ev.data["question_id"] == "q123"
    assert ev.data["question"] == "What is your PAN?"
    assert ev.data["field"] == "pan"
    assert ev.data["language_code"] == "hi-IN"


def test_event_bus_emit_sync_without_loop():
    bus = EventBus()
    q = bus.subscribe()

    bus.emit("status", status="idle")

    # In sync context without running loop, fallback put_nowait puts into q
    assert not q.empty()
    ev = q.get_nowait()
    assert ev.type == "status"
    assert ev.data == {"status": "idle"}
