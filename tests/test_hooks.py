"""Tests for PreToolUse hooks, safety guard integration, and user-in-control checks."""

from __future__ import annotations

import asyncio
from typing import Any
import pytest

from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import PreToolUseHookInput

from agastya.agent.events import EventBus
from agastya.agent.hooks import (
    PLAYWRIGHT_ALLOWED_TOOLS,
    PLAYWRIGHT_DISALLOWED_TOOLS,
    build_hooks,
    extract_tool_info,
    is_browser_tool,
    make_pre_tool_use_hook,
)


class DummySession:
    """Mock session for testing hook behavior."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.user_in_control = False


@pytest.fixture
def session() -> DummySession:
    return DummySession()


def test_build_hooks_returns_matcher(session: DummySession) -> None:
    hooks = build_hooks(session)
    assert "PreToolUse" in hooks
    matchers = hooks["PreToolUse"]
    assert len(matchers) >= 1
    # Verify playwright matcher exists
    assert any(m.matcher == "mcp__playwright__.*" for m in matchers)
    # Verify callbacks are present
    assert all(len(m.hooks) > 0 for m in matchers)


def test_is_browser_tool() -> None:
    assert is_browser_tool("mcp__playwright__browser_click") is True
    assert is_browser_tool("mcp__playwright__browser_snapshot") is True
    assert is_browser_tool("mcp__agastya__browser_goto") is True
    assert is_browser_tool("mcp__agastya__browser_click") is True
    assert is_browser_tool("browser_click") is True
    assert is_browser_tool("mcp__agastya__get_profile") is False
    assert is_browser_tool("mcp__agastya__ask_user") is False
    assert is_browser_tool("Bash") is False


def test_extract_tool_info() -> None:
    # Dict with tool_name and tool_input
    name, inp = extract_tool_info({
        "tool_name": "mcp__playwright__browser_click",
        "tool_input": {"element": "Submit"},
    })
    assert name == "mcp__playwright__browser_click"
    assert inp == {"element": "Submit"}

    # Dict with name and input
    name2, inp2 = extract_tool_info({
        "name": "browser_click",
        "input": {"text": "Click here"},
    })
    assert name2 == "browser_click"
    assert inp2 == {"text": "Click here"}


@pytest.mark.asyncio
async def test_hook_click_submit_denied(session: DummySession) -> None:
    hook = make_pre_tool_use_hook(session)
    q = session.bus.subscribe()

    # English "Submit"
    res = await hook({
        "tool_name": "mcp__playwright__browser_click",
        "tool_input": {"element": "Submit"},
    })
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "handoff_to_user" in res["hookSpecificOutput"]["permissionDecisionReason"]

    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "blocked"
    assert ev.data["tool"] == "mcp__playwright__browser_click"
    assert "submit" in ev.data["reason"].lower()

    # Hindi "जमा करें"
    res_hi = await hook({
        "tool_name": "mcp__playwright__browser_click",
        "tool_input": {"element": "जमा करें"},
    })
    assert res_hi["hookSpecificOutput"]["permissionDecision"] == "deny"

    # Payment button
    res_pay = await hook({
        "tool_name": "mcp__playwright__browser_click",
        "tool_input": {"element": "Proceed to Pay"},
    })
    assert res_pay["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_hook_click_next_allowed(session: DummySession) -> None:
    hook = make_pre_tool_use_hook(session)
    q = session.bus.subscribe()

    # "Next" button
    res = await hook({
        "tool_name": "mcp__playwright__browser_click",
        "tool_input": {"element": "Next"},
    })
    assert res["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert q.empty()

    # "Continue" button
    res2 = await hook({
        "tool_name": "mcp__playwright__browser_click",
        "tool_input": {"element": "Continue"},
    })
    assert res2["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert q.empty()


@pytest.mark.asyncio
async def test_hook_navigate_payment_gateway_denied(session: DummySession) -> None:
    hook = make_pre_tool_use_hook(session)
    q = session.bus.subscribe()

    # Billdesk gateway
    res = await hook({
        "tool_name": "mcp__playwright__browser_navigate",
        "tool_input": {"url": "https://www.billdesk.com/pgidsk/PGIMerchantPayment"},
    })
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "handoff_to_user" in res["hookSpecificOutput"]["permissionDecisionReason"]

    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "blocked"
    assert ev.data["tool"] == "mcp__playwright__browser_navigate"
    assert "billdesk" in ev.data["reason"].lower()

    # Razorpay
    res_rzp = await hook({
        "tool_name": "mcp__playwright__browser_navigate",
        "tool_input": {"url": "https://api.razorpay.com/checkout"},
    })
    assert res_rzp["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_hook_navigate_safe_allowed(session: DummySession) -> None:
    hook = make_pre_tool_use_hook(session)
    q = session.bus.subscribe()

    res = await hook({
        "tool_name": "mcp__playwright__browser_navigate",
        "tool_input": {"url": "https://voters.eci.gov.in/service/form6"},
    })
    assert res["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert q.empty()


@pytest.mark.asyncio
async def test_hook_evaluate_and_disallowed_tools_denied(session: DummySession) -> None:
    hook = make_pre_tool_use_hook(session)

    # browser_evaluate
    res = await hook({
        "tool_name": "mcp__playwright__browser_evaluate",
        "tool_input": {"script": "alert(1)"},
    })
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "disallowed" in res["hookSpecificOutput"]["permissionDecisionReason"].lower()

    # All disallowed tools in list
    for tool_name in PLAYWRIGHT_DISALLOWED_TOOLS:
        res_tool = await hook({
            "tool_name": tool_name,
            "tool_input": {},
        })
        assert res_tool["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_hook_press_key_enter_policy(session: DummySession) -> None:
    hook = make_pre_tool_use_hook(session)
    q = session.bus.subscribe()

    # Enter on generic element -> Denied
    res_deny = await hook({
        "tool_name": "mcp__playwright__browser_press_key",
        "tool_input": {"key": "Enter", "element": "submit_button"},
    })
    assert res_deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "blocked"
    assert "enter" in ev.data["reason"].lower()

    # Enter on search input -> Allowed
    res_search = await hook({
        "tool_name": "mcp__playwright__browser_press_key",
        "tool_input": {"key": "Enter", "element": "search_box"},
    })
    assert res_search["hookSpecificOutput"]["permissionDecision"] == "allow"

    # Tab key -> Allowed
    res_tab = await hook({
        "tool_name": "mcp__playwright__browser_press_key",
        "tool_input": {"key": "Tab", "element": "any_field"},
    })
    assert res_tab["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.asyncio
async def test_hook_any_browser_tool_denied_while_user_in_control(session: DummySession) -> None:
    hook = make_pre_tool_use_hook(session)
    q = session.bus.subscribe()

    session.user_in_control = True

    # Playwright tool denied
    res_pw = await hook({
        "tool_name": "mcp__playwright__browser_snapshot",
        "tool_input": {},
    })
    assert res_pw["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "controlling the browser" in res_pw["hookSpecificOutput"]["permissionDecisionReason"]
    ev1 = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev1.type == "blocked"
    assert ev1.data["reason"] == "user_in_control"
    assert ev1.data["tool"] == "mcp__playwright__browser_snapshot"

    # Agastya browser tool denied
    res_ag = await hook({
        "tool_name": "mcp__agastya__browser_click",
        "tool_input": {"ref": "f1"},
    })
    assert res_ag["hookSpecificOutput"]["permissionDecision"] == "deny"
    ev2 = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev2.type == "blocked"
    assert ev2.data["reason"] == "user_in_control"

    # Non-browser tool allowed even while user in control
    res_profile = await hook({
        "tool_name": "mcp__agastya__get_profile",
        "tool_input": {},
    })
    assert res_profile["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.asyncio
async def test_hook_accepts_pre_tool_use_hook_input_typed_dict(session: DummySession) -> None:
    hook = make_pre_tool_use_hook(session)
    inp = PreToolUseHookInput(
        hook_event_name="PreToolUse",
        tool_name="mcp__playwright__browser_click",
        tool_input={"element": "Next"},
        tool_use_id="tu1",
    )
    res = await hook(inp)
    assert res["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.parametrize("submit,decision", [(True, "deny"), (False, "allow"), (None, "allow")])
async def test_hook_type_with_submit(session: DummySession, submit, decision) -> None:
    hook = make_pre_tool_use_hook(session)
    tool_input = {"element": "Mobile number", "ref": "e12", "text": "9876543210"}
    if submit is not None:
        tool_input["submit"] = submit
    res = await hook({"tool_name": "mcp__playwright__browser_type", "tool_input": tool_input})
    assert res["hookSpecificOutput"]["permissionDecision"] == decision


@pytest.mark.parametrize("name", ["browser_run_code_unsafe", "browser_some_future_tool", "browser_evaluate"])
async def test_hook_denies_unlisted_playwright_tools(session: DummySession, name) -> None:
    hook = make_pre_tool_use_hook(session)
    res = await hook({"tool_name": f"mcp__playwright__{name}", "tool_input": {}})
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny"
