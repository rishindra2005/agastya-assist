"""Tests for agent backends (base protocol, Claude, agy stream-json parser, session integration)."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from agastya.agent.backends.agy_backend import AgyBackend, parse_stream_json_line
from agastya.agent.backends.base import AgentBackend, BackendEvent
from agastya.agent.backends.claude_backend import ClaudeBackend
from agastya.agent.events import EventBus
from agastya.agent.session import AgentSession
from agastya.config import get_settings


class FakeBrowser:
    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.agent_active_history: list[bool] = []

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def set_agent_active(self, active: bool) -> None:
        self.agent_active_history.append(active)


class FakeBackend:
    """Mock backend implementing AgentBackend protocol for session tests."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.turns_received: list[str] = []
        self.queued_events: list[list[BackendEvent]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def run_turn(self, text: str) -> AsyncIterator[BackendEvent]:
        self.turns_received.append(text)
        if self.queued_events:
            events = self.queued_events.pop(0)
            for ev in events:
                yield ev
        else:
            yield ("text", "नमस्ते, मैं आपकी क्या सहायता कर सकता हूँ?")
            yield (
                "done",
                {"subtype": "success", "result": "नमस्ते", "is_error": False},
            )


def test_agy_stream_json_parser_on_recorded_lines() -> None:
    """Verify parse_stream_json_line on recorded sample lines from Phase 1 experiments."""
    # 1. Init event -> empty
    init_line = (
        '{"event":"init","conversation_id":"3c93dc85-4b34-475c-b21b-9f5bd441b72d",'
        '"init":{"model":"gemini-3.8-flash-low","cwd":"/work","tools":["run_command"]}}'
    )
    assert parse_stream_json_line(init_line) == []

    # 2. User input ack -> empty
    user_ack = (
        '{"event":"step_update","step_update":{"conversation_id":"3c93dc85","step_index":0,'
        '"state":"DONE","step_type":"user_input"}}'
    )
    assert parse_stream_json_line(user_ack) == []

    # 3. Active text delta -> ("text", delta)
    text_delta = (
        '{"event":"step_update","step_update":{"conversation_id":"3c93dc85","step_index":1,'
        '"state":"ACTIVE","step_type":"agent_response","text_delta":"Hi! How can I help?"}}'
    )
    assert parse_stream_json_line(text_delta) == [("text", "Hi! How can I help?")]

    # 4. Tool call (state: ACTIVE) -> ("tool_call", {id, name, input})
    tool_call = (
        '{"event":"step_update","step_update":{"conversation_id":"80dba4bf","step_index":2,'
        '"state":"ACTIVE","step_type":"tool","tool_name":"browser_snapshot",'
        '"tool_info":{"name":"browser_snapshot","parameters":{"url":"https://example.com"}}}}'
    )
    events = parse_stream_json_line(tool_call)
    assert len(events) == 1
    assert events[0][0] == "tool_call"
    assert events[0][1]["id"] == "agy_tool_2"
    assert events[0][1]["name"] == "browser_snapshot"
    assert events[0][1]["input"] == {"url": "https://example.com"}

    # 5. Tool completion (state: DONE) -> empty (result is ingested by agy internally)
    tool_done = (
        '{"event":"step_update","step_update":{"conversation_id":"80dba4bf","step_index":2,'
        '"state":"DONE","step_type":"tool","tool_name":"browser_snapshot",'
        '"duration_seconds":0.08,"tool_info":{"name":"browser_snapshot","parameters":{},"output":"ok"}}}'
    )
    assert parse_stream_json_line(tool_done) == []

    # 6. Turn completion -> ("done", {result, is_error, ...})
    turn_result = (
        '{"event":"result","result":{"conversation_id":"7b358787","status":"SUCCESS",'
        '"response":"4\\n","duration_seconds":12.43,"num_turns":1}}'
    )
    assert parse_stream_json_line(turn_result) == [
        ("done", {"subtype": "success", "result": "4\n", "is_error": False, "error": None})
    ]

    # 7. Error result -> ("done", {is_error: True, ...})
    error_result = (
        '{"event":"result","result":{"conversation_id":"err1","status":"ERROR",'
        '"response":"","error":"stream input message is missing the \\"event\\" field"}}'
    )
    parsed_err = parse_stream_json_line(error_result)
    assert len(parsed_err) == 1
    assert parsed_err[0][0] == "done"
    assert parsed_err[0][1]["is_error"] is True
    assert "stream input message is missing" in parsed_err[0][1]["error"]


@pytest.mark.asyncio
async def test_session_with_fake_backend() -> None:
    """Verify AgentSession loop interacts with AgentBackend protocol cleanly."""
    fake_backend = FakeBackend()
    fake_browser = FakeBrowser()
    bus = EventBus()
    q = bus.subscribe()

    fake_backend.queued_events = [
        [
            ("text", "फॉर्म भर रहे हैं"),
            ("tool_call", {"id": "t1", "name": "browser_fill", "input": {"ref": "f1", "value": "Test"}}),
            ("done", {"subtype": "success", "result": "फॉर्म भर दिया", "is_error": False}),
        ]
    ]

    session = AgentSession(
        browser=fake_browser,
        bus=bus,
        backend=fake_backend,
    )
    await session.start()
    assert fake_backend.started is True

    ready_ev = await asyncio.wait_for(q.get(), timeout=2.0)
    assert ready_ev.type == "status" and ready_ev.data["status"] == "ready"

    try:
        await session.send_user_message("कृपया फॉर्म भरें")

        ev_thinking = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_thinking.type == "status" and ev_thinking.data["status"] == "thinking"

        ev_text = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_text.type == "assistant_text"
        assert ev_text.data["text"] == "फॉर्म भर रहे हैं"

        ev_tool = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_tool.type == "tool_call"
        assert ev_tool.data["id"] == "t1"
        assert ev_tool.data["name"] == "browser_fill"

        ev_done = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_done.type == "done"
        assert ev_done.data["result"] == "फॉर्म भर दिया"

        ev_metrics = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_metrics.type == "status" and ev_metrics.data["status"] == "metrics"
        assert ev_metrics.data["tools"] == 1
    finally:
        await session.stop()

    assert fake_backend.stopped is True
    assert fake_browser.closed is True


@pytest.mark.asyncio
async def test_backend_selection_by_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify settings.AGENT_BACKEND selects between Claude and Agy backends."""
    settings = get_settings()

    # 1. Claude backend (default)
    monkeypatch.setattr(settings, "AGENT_BACKEND", "claude")
    session_claude = AgentSession(browser=FakeBrowser(), client_factory=lambda opts: None)
    # Start and stop should initialize ClaudeBackend
    await session_claude.start()
    try:
        assert isinstance(session_claude.backend, ClaudeBackend)
    finally:
        await session_claude.stop()

    # 2. Agy backend
    monkeypatch.setattr(settings, "AGENT_BACKEND", "agy")
    session_agy = AgentSession(browser=FakeBrowser())

    # Mock start and stop on AgyBackend to avoid launching real agy CLI in unit test
    agy_started = False
    agy_stopped = False

    async def mock_start(self):
        nonlocal agy_started
        agy_started = True

    async def mock_stop(self):
        nonlocal agy_stopped
        agy_stopped = True

    monkeypatch.setattr(AgyBackend, "start", mock_start)
    monkeypatch.setattr(AgyBackend, "stop", mock_stop)

    await session_agy.start()
    try:
        assert isinstance(session_agy.backend, AgyBackend)
        assert agy_started is True
    finally:
        await session_agy.stop()
    assert agy_stopped is True


@pytest.mark.asyncio
async def test_claude_backend_run_turn_streaming() -> None:
    """Verify ClaudeBackend maps AssistantMessage and ResultMessage into BackendEvents."""
    class MockClient:
        async def connect(self): pass
        async def disconnect(self): pass
        async def query(self, text: str): pass
        async def receive_response(self):
            yield AssistantMessage(
                content=[
                    TextBlock(text="Step 1 response"),
                    ToolUseBlock(id="call_1", name="get_profile", input={"field": "full_name"}),
                ],
                model="claude-sonnet-5-5",
            )
            yield ResultMessage(
                subtype="success",
                duration_ms=25,
                duration_api_ms=25,
                is_error=False,
                num_turns=1,
                session_id="default",
                result="Turn completed",
            )

    session = AgentSession(browser=FakeBrowser())
    backend = ClaudeBackend(session=session, client_factory=lambda opts: MockClient())
    await backend.start()
    try:
        events: list[BackendEvent] = []
        async for ev in backend.run_turn("Test query"):
            events.append(ev)

        assert len(events) == 3
        assert events[0] == ("text", "Step 1 response")
        assert events[1] == (
            "tool_call",
            {"id": "call_1", "name": "get_profile", "input": {"field": "full_name"}},
        )
        assert events[2][0] == "done"
        assert events[2][1]["result"] == "Turn completed"
        assert events[2][1]["is_error"] is False
    finally:
        await backend.stop()


async def test_agy_backend_refuses_to_run_unsandboxed(monkeypatch):
    """agy's built-in shell/file tools can't be disabled, so it must not start unconfined by default."""
    monkeypatch.delenv("AGY_ALLOW_UNSANDBOXED", raising=False)
    get_settings.cache_clear()
    import types

    backend = AgyBackend(session=types.SimpleNamespace(user_id="t", profile=None, bus=None, browser=None))
    with pytest.raises(RuntimeError, match="not OS-sandboxed"):
        await backend._start_process()
