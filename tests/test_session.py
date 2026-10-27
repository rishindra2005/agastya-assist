"""Tests for AgentSession long-running loop, event routing, and ask_user handling."""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any
import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from agastya.agent.events import EventBus
from agastya.agent.hooks import (
    PLAYWRIGHT_ALLOWED_TOOLS,
    PLAYWRIGHT_DISALLOWED_TOOLS,
)
from agastya.agent.prompts import SYSTEM_PROMPT
from agastya.agent.session import AgentSession
from agastya.config import get_settings


class FakeBrowser:
    """Mock browser driver implementing start, close, and CDP/agent active tracking."""

    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.cdp_endpoint = "http://127.0.0.1:9222"
        self.cdp_port = 9222
        self.agent_active_history: list[bool] = []

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def set_agent_active(self, active: bool) -> None:
        self.agent_active_history.append(active)


class FakeClaudeClient:
    """Mock ClaudeSDKClient that streams preconfigured messages."""

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.queries: list[str] = []
        self.connected = False
        self.disconnected = False
        self.responses: list[list[Any]] = []
        self.fail_next = False
        self.fail_exception = RuntimeError("API connection failure")

    async def connect(self) -> None:
        self.connected = True

    async def query(self, text: str) -> None:
        self.queries.append(text)
        if self.fail_next:
            self.fail_next = False
            raise self.fail_exception

    async def receive_response(self):
        if self.responses:
            messages = self.responses.pop(0)
            for msg in messages:
                yield msg
        else:
            yield AssistantMessage(
                content=[TextBlock(text="नमस्ते, Agastya आपकी क्या सेवा कर सकता है?")],
                model="claude-sonnet-5-5",
            )
            yield ResultMessage(
                subtype="success",
                duration_ms=50,
                duration_api_ms=50,
                is_error=False,
                num_turns=1,
                session_id="default",
                result="Turn completed",
            )

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture
def fake_browser() -> FakeBrowser:
    return FakeBrowser()


@pytest.mark.asyncio
async def test_session_options_configured_correctly(fake_browser: FakeBrowser) -> None:
    captured_options: list[ClaudeAgentOptions] = []

    def factory(opts: ClaudeAgentOptions) -> FakeClaudeClient:
        captured_options.append(opts)
        return FakeClaudeClient(opts)

    settings = get_settings()
    session = AgentSession(
        user_id="user123",
        browser=fake_browser,
        client_factory=factory,
    )
    await session.start()
    try:
        assert len(captured_options) == 1
        opts = captured_options[0]
        assert opts.model == settings.AGENT_MODEL
        assert opts.system_prompt == SYSTEM_PROMPT
        assert opts.tools == []
        assert "Bash" in opts.disallowed_tools
        assert "Read" in opts.disallowed_tools
        assert opts.permission_mode != "bypassPermissions"
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_session_mcp_servers_and_allowed_tools(
    fake_browser: FakeBrowser, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_options: list[ClaudeAgentOptions] = []

    def factory(opts: ClaudeAgentOptions) -> FakeClaudeClient:
        captured_options.append(opts)
        return FakeClaudeClient(opts)

    fake_tools_mod = types.ModuleType("agastya.agent.tools")
    fake_tools_mod.TOOL_NAMES = ["mcp__agastya__ask_user", "mcp__agastya__get_profile"]

    class DummyContext:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_tools_mod.ToolContext = DummyContext
    fake_tools_mod.build_mcp_server = lambda ctx: "fake_mcp_server"

    monkeypatch.setitem(sys.modules, "agastya.agent.tools", fake_tools_mod)

    # When PLAYWRIGHT_MCP is disabled, only agastya MCP is registered
    settings = get_settings()
    monkeypatch.setattr(settings, "PLAYWRIGHT_MCP", False)

    session = AgentSession(
        user_id="user123",
        browser=fake_browser,
        client_factory=factory,
    )
    await session.start()
    try:
        assert len(captured_options) == 1
        opts = captured_options[0]
        assert opts.mcp_servers == {"agastya": "fake_mcp_server"}
        assert opts.allowed_tools == ["mcp__agastya__ask_user", "mcp__agastya__get_profile"]
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_session_playwright_mcp_enabled_options(
    fake_browser: FakeBrowser, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_options: list[ClaudeAgentOptions] = []

    def factory(opts: ClaudeAgentOptions) -> FakeClaudeClient:
        captured_options.append(opts)
        return FakeClaudeClient(opts)

    fake_browser.cdp_endpoint = "http://127.0.0.1:9444"

    settings = get_settings()
    monkeypatch.setattr(settings, "PLAYWRIGHT_MCP", True)
    monkeypatch.setattr(settings, "AGENT_EFFORT", "low")

    session = AgentSession(
        user_id="user_pw",
        browser=fake_browser,
        client_factory=factory,
    )
    await session.start()
    try:
        assert len(captured_options) == 1
        opts = captured_options[0]
        assert "agastya" in opts.mcp_servers
        assert "playwright" in opts.mcp_servers
        pw_cfg = opts.mcp_servers["playwright"]
        assert pw_cfg["type"] == "stdio"
        assert pw_cfg["command"].endswith("playwright-mcp")
        assert pw_cfg["args"] == ["--cdp-endpoint", "http://127.0.0.1:9444"]

        for tool in PLAYWRIGHT_ALLOWED_TOOLS:
            assert tool in opts.allowed_tools

        for tool in PLAYWRIGHT_DISALLOWED_TOOLS:
            assert tool in opts.disallowed_tools

        assert opts.effort == "low"
        assert opts.hooks is not None
        assert "PreToolUse" in opts.hooks
        assert any(m.matcher == "mcp__playwright__.*" for m in opts.hooks["PreToolUse"])
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_human_control_take_and_release(fake_browser: FakeBrowser) -> None:
    bus = EventBus()
    q = bus.subscribe()

    session = AgentSession(
        user_id="user_ctrl",
        browser=fake_browser,
        bus=bus,
        client_factory=lambda opts: FakeClaudeClient(opts),
    )
    await session.start()
    ready = await asyncio.wait_for(q.get(), timeout=2.0)
    assert ready.type == "status" and ready.data["status"] == "ready"

    try:
        # Take control
        res_take = await session.take_control()
        assert res_take is True
        assert session.user_in_control is True
        ev_ctrl = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_ctrl.type == "status"
        assert ev_ctrl.data["status"] == "user_control"
        assert fake_browser.agent_active_history[-1] is False

        # Release control with note
        res_rel1 = await session.release_control("Captcha completed")
        assert res_rel1 is False
        assert session.user_in_control is False
        msg1 = await asyncio.wait_for(session._inbox.get(), timeout=2.0)
        assert msg1 == "[user finished on the page: Captcha completed] Continue from the current page."

        # Release control with empty note
        res_rel2 = await session.release_control("")
        assert res_rel2 is False
        msg2 = await asyncio.wait_for(session._inbox.get(), timeout=2.0)
        assert msg2 == "[user finished on the page] Continue from the current page."
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_timing_metrics_emitted(fake_browser: FakeBrowser) -> None:
    def factory(opts: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(opts)
        client.responses = [
            [
                AssistantMessage(
                    content=[
                        ToolUseBlock(
                            id="tu_1",
                            name="mcp__agastya__browser_snapshot",
                            input={},
                        ),
                        TextBlock(text="Snapshot captured"),
                    ],
                    model="claude-sonnet-5-5",
                ),
                ResultMessage(
                    subtype="success",
                    duration_ms=10,
                    duration_api_ms=10,
                    is_error=False,
                    num_turns=1,
                    session_id="default",
                ),
            ]
        ]
        return client

    bus = EventBus()
    q = bus.subscribe()

    session = AgentSession(browser=fake_browser, bus=bus, client_factory=factory)
    await session.start()
    ready = await asyncio.wait_for(q.get(), timeout=2.0)
    assert ready.type == "status" and ready.data["status"] == "ready"

    try:
        await session.send_user_message("Take snapshot", "en-IN")

        ev_thinking = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_thinking.type == "status" and ev_thinking.data["status"] == "thinking"

        ev_tool = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_tool.type == "tool_call"

        ev_text = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_text.type == "assistant_text"

        ev_done = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_done.type == "done"

        ev_metrics = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_metrics.type == "status"
        assert ev_metrics.data["status"] == "metrics"
        assert "first_text_s" in ev_metrics.data
        assert isinstance(ev_metrics.data["first_text_s"], float)
        assert "turn_s" in ev_metrics.data
        assert isinstance(ev_metrics.data["turn_s"], float)
        assert ev_metrics.data["tools"] == 1
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_turn_wrapper_set_agent_active(fake_browser: FakeBrowser) -> None:
    session = AgentSession(
        browser=fake_browser,
        client_factory=lambda opts: FakeClaudeClient(opts),
    )
    await session.start()
    try:
        await session.send_user_message("Hello")
        # Give run loop time to execute turn
        await asyncio.sleep(0.05)
        # Verify set_agent_active was called with True then False
        assert True in fake_browser.agent_active_history
        assert fake_browser.agent_active_history[-1] is False
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_events_order_status_assistant_text_done(fake_browser: FakeBrowser) -> None:
    fake_client: FakeClaudeClient | None = None

    def factory(opts: ClaudeAgentOptions) -> FakeClaudeClient:
        nonlocal fake_client
        fake_client = FakeClaudeClient(opts)
        fake_client.responses = [
            [
                AssistantMessage(
                    content=[TextBlock(text="PAN कार्ड के लिए फॉर्म भर रहे हैं।")],
                    model="claude-sonnet-5-5",
                ),
                ResultMessage(
                    subtype="success",
                    duration_ms=40,
                    duration_api_ms=40,
                    is_error=False,
                    num_turns=1,
                    session_id="default",
                    result="success",
                ),
            ]
        ]
        return fake_client

    bus = EventBus()
    q = bus.subscribe()

    session = AgentSession(
        user_id="default",
        browser=fake_browser,
        bus=bus,
        client_factory=factory,
    )
    await session.start()
    ready = await asyncio.wait_for(q.get(), timeout=2.0)
    assert (ready.type, ready.data["status"]) == ("status", "ready")
    try:
        await session.send_user_message("Apply for PAN")

        # Collect events in order
        ev1 = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev1.type == "status"
        assert ev1.data["status"] == "thinking"

        ev2 = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev2.type == "assistant_text"
        assert ev2.data["text"] == "PAN कार्ड के लिए फॉर्म भर रहे हैं।"
        assert ev2.data["language_code"] == session.language_code

        ev3 = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev3.type == "done"
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_tool_call_event_emitted(fake_browser: FakeBrowser) -> None:
    def factory(opts: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(opts)
        client.responses = [
            [
                AssistantMessage(
                    content=[
                        ToolUseBlock(
                            id="tool_1",
                            name="mcp__agastya__get_profile",
                            input={"field": "full_name"},
                        )
                    ],
                    model="claude-sonnet-5-5",
                ),
                ResultMessage(
                    subtype="success",
                    duration_ms=20,
                    duration_api_ms=20,
                    is_error=False,
                    num_turns=1,
                    session_id="default",
                ),
            ]
        ]
        return client

    bus = EventBus()
    q = bus.subscribe()

    session = AgentSession(browser=fake_browser, bus=bus, client_factory=factory)
    await session.start()
    ready = await asyncio.wait_for(q.get(), timeout=2.0)
    assert (ready.type, ready.data["status"]) == ("status", "ready")
    try:
        await session.send_user_message("Check my name")

        ev_status = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_status.type == "status"

        ev_tool = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_tool.type == "tool_call"
        assert ev_tool.data["id"] == "tool_1"
        assert ev_tool.data["name"] == "mcp__agastya__get_profile"
        assert ev_tool.data["input"] == {"field": "full_name"}

        ev_done = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_done.type == "done"
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_ask_user_round_trip_via_send_user_message(fake_browser: FakeBrowser) -> None:
    bus = EventBus()
    q = bus.subscribe()

    session = AgentSession(browser=fake_browser, bus=bus, client_factory=lambda opts: FakeClaudeClient(opts))
    await session.start()
    ready = await asyncio.wait_for(q.get(), timeout=2.0)
    assert (ready.type, ready.data["status"]) == ("status", "ready")
    try:
        ask_task = asyncio.create_task(
            session.ask_user("कृपया अपना पिता का नाम बताएं", field="father_name")
        )

        # Wait for ask_user event on bus
        ev = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev.type == "ask_user"
        assert ev.data["question"] == "कृपया अपना पिता का नाम बताएं"
        assert ev.data["field"] == "father_name"
        assert ev.data["fields"] == ["father_name"]
        assert "question_id" in ev.data
        assert ev.data["language_code"] == session.language_code

        # User replies via send_user_message while question is pending
        await session.send_user_message("Rajesh Sharma")

        answer = await asyncio.wait_for(ask_task, timeout=2.0)
        assert answer == "Rajesh Sharma"
        # Since question was pending, message was NOT enqueued to inbox
        assert session._inbox.empty()
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_answer_question_by_id(fake_browser: FakeBrowser) -> None:
    bus = EventBus()
    q = bus.subscribe()

    session = AgentSession(browser=fake_browser, bus=bus, client_factory=lambda opts: FakeClaudeClient(opts))
    await session.start()
    ready = await asyncio.wait_for(q.get(), timeout=2.0)
    assert (ready.type, ready.data["status"]) == ("status", "ready")
    try:
        ask_task = asyncio.create_task(session.ask_user("Enter your PAN", field="pan"))

        ev = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev.type == "ask_user"
        qid = ev.data["question_id"]

        # Directly answer question by id
        await session.answer_question(qid, "ABCDE1234F")

        answer = await asyncio.wait_for(ask_task, timeout=2.0)
        assert answer == "ABCDE1234F"
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_ask_user_timeout(fake_browser: FakeBrowser) -> None:
    bus = EventBus()
    q = bus.subscribe()

    # Small timeout for test
    session = AgentSession(
        browser=fake_browser,
        bus=bus,
        client_factory=lambda opts: FakeClaudeClient(opts),
        ask_timeout=0.05,
    )
    await session.start()
    ready = await asyncio.wait_for(q.get(), timeout=2.0)
    assert (ready.type, ready.data["status"]) == ("status", "ready")
    try:
        answer = await session.ask_user("Enter Aadhaar", field="aadhaar")
        assert answer == ""

        ev_ask = await asyncio.wait_for(q.get(), timeout=1.0)
        assert ev_ask.type == "ask_user"

        ev_timeout = await asyncio.wait_for(q.get(), timeout=1.0)
        assert ev_timeout.type == "status"
        assert ev_timeout.data["status"] == "timeout"
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_exception_in_one_turn_survives_and_loop_continues(fake_browser: FakeBrowser) -> None:
    fake_client: FakeClaudeClient | None = None

    def factory(opts: ClaudeAgentOptions) -> FakeClaudeClient:
        nonlocal fake_client
        fake_client = FakeClaudeClient(opts)
        fake_client.fail_next = True  # First turn fails
        fake_client.fail_exception = RuntimeError("Simulated crash")
        return fake_client

    bus = EventBus()
    q = bus.subscribe()

    session = AgentSession(browser=fake_browser, bus=bus, client_factory=factory)
    await session.start()
    ready = await asyncio.wait_for(q.get(), timeout=2.0)
    assert (ready.type, ready.data["status"]) == ("status", "ready")
    try:
        # Turn 1: Fails
        await session.send_user_message("Turn 1 text")

        ev_status1 = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_status1.type == "status"

        ev_err = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_err.type == "error"
        assert "Simulated crash" in ev_err.data["error"]
        assert ev_err.data["exc_type"] == "RuntimeError"

        # Turn 2: Succeeds - verifies loop survived
        await session.send_user_message("Turn 2 text")

        ev_status2 = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_status2.type == "status"

        ev_text = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_text.type == "assistant_text"

        ev_done = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_done.type == "done"
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_language_code_propagation(fake_browser: FakeBrowser) -> None:
    fake_client: FakeClaudeClient | None = None

    def factory(opts: ClaudeAgentOptions) -> FakeClaudeClient:
        nonlocal fake_client
        fake_client = FakeClaudeClient(opts)
        return fake_client

    bus = EventBus()
    q = bus.subscribe()

    session = AgentSession(browser=fake_browser, bus=bus, client_factory=factory)
    await session.start()
    ready = await asyncio.wait_for(q.get(), timeout=2.0)
    assert (ready.type, ready.data["status"]) == ("status", "ready")
    try:
        # Send user message with language_code="ta-IN" (Tamil)
        await session.send_user_message("வணக்கம்", language_code="ta-IN")

        assert session.language_code == "ta-IN"

        # Drain events
        ev_status = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_status.type == "status"

        ev_text = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_text.type == "assistant_text"
        assert ev_text.data["language_code"] == "ta-IN"

        ev_done = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev_done.type == "done"

        # Verify query prefix sent to Claude client
        assert fake_client is not None
        assert len(fake_client.queries) == 1
        assert fake_client.queries[0] == "[user_language=ta-IN] வணக்கம்"
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_stop_cancels_loop_disconnects_client_closes_browser(fake_browser: FakeBrowser) -> None:
    fake_client: FakeClaudeClient | None = None

    def factory(opts: ClaudeAgentOptions) -> FakeClaudeClient:
        nonlocal fake_client
        fake_client = FakeClaudeClient(opts)
        return fake_client

    session = AgentSession(browser=fake_browser, client_factory=factory)
    await session.start()

    assert fake_browser.started is True
    assert fake_client is not None
    assert fake_client.connected is True
    assert session._loop_task is not None
    assert not session._loop_task.done()

    await session.stop()

    assert fake_browser.closed is True
    assert fake_client.disconnected is True
    assert session._loop_task is None
    assert session._stopped is True


def test_lazy_profile_initialization() -> None:
    session = AgentSession(user_id="citizen_42")
    assert session.profile is not None
    assert session.user_id == "citizen_42"
    assert session.browser is None


@pytest.mark.asyncio
async def test_ask_user_grouped_fields_and_legacy_signature(fake_browser: FakeBrowser) -> None:
    bus = EventBus()
    q = bus.subscribe()
    session = AgentSession(browser=fake_browser, bus=bus, client_factory=lambda opts: FakeClaudeClient(opts))
    await session.start()
    ready = await asyncio.wait_for(q.get(), timeout=2.0)
    assert (ready.type, ready.data["status"]) == ("status", "ready")
    try:
        # 1. Grouped fields list
        task1 = asyncio.create_task(
            session.ask_user("Enter name and DOB", fields=["full_name", "date_of_birth"])
        )
        ev1 = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev1.type == "ask_user"
        assert ev1.data["field"] == "full_name"
        assert ev1.data["fields"] == ["full_name", "date_of_birth"]
        await session.answer_question(ev1.data["question_id"], "Rahul Sharma, 5 March 2001")
        ans1 = await asyncio.wait_for(task1, timeout=2.0)
        assert ans1 == "Rahul Sharma, 5 March 2001"

        # 2. Legacy positional string
        task2 = asyncio.create_task(session.ask_user("Enter mobile", "mobile"))
        ev2 = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev2.type == "ask_user"
        assert ev2.data["field"] == "mobile"
        assert ev2.data["fields"] == ["mobile"]
        await session.answer_question(ev2.data["question_id"], "9876543210")
        ans2 = await asyncio.wait_for(task2, timeout=2.0)
        assert ans2 == "9876543210"

        # 3. No field specified
        task3 = asyncio.create_task(session.ask_user("Do you confirm?"))
        ev3 = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev3.type == "ask_user"
        assert ev3.data["field"] is None
        assert ev3.data["fields"] == []
        await session.answer_question(ev3.data["question_id"], "Yes")
        ans3 = await asyncio.wait_for(task3, timeout=2.0)
        assert ans3 == "Yes"
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_set_agent_active_integration_with_page_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, bool]] = []

    async def mock_set_agent_active(ctx: Any, active: bool) -> None:
        calls.append((ctx, active))

    try:
        import agastya.browser.page_guard as pg
        monkeypatch.setattr(pg, "set_agent_active", mock_set_agent_active)
    except Exception:
        pass

    class BrowserWithContext:
        def __init__(self):
            self.context = "fake_browser_context"
        async def start(self): pass
        async def close(self): pass

    browser = BrowserWithContext()
    session = AgentSession(
        browser=browser,
        client_factory=lambda opts: FakeClaudeClient(opts),
    )
    await session.start()
    try:
        await session.take_control()
        assert ("fake_browser_context", False) in calls

        calls.clear()
        await session.send_user_message("Hello")
        await asyncio.sleep(0.05)
        assert ("fake_browser_context", True) in calls
        assert ("fake_browser_context", False) in calls
    finally:
        await session.stop()



@pytest.mark.parametrize(
    "text,lang,expected",
    [
        ("Only Aadhaar is missing for the 49A form.", "hi-IN", True),
        ("आपका आधार नंबर बताइए।", "hi-IN", False),
        ("PAN 49A फॉर्म भर दिया है।", "hi-IN", False),
        ("உங்கள் பெயர் என்ன?", "ta-IN", False),
        ("Filling the form now.", "en-IN", False),
        ("OK", "hi-IN", False),
    ],
)
def test_is_wrong_script(text, lang, expected):
    from agastya.agent.session import is_wrong_script

    assert is_wrong_script(text, lang) is expected
