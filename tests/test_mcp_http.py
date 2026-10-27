"""Tests for streamable-HTTP MCP server, auth rejection, tools listing, and safety."""

from __future__ import annotations

import json
from typing import Any
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import httpx2, streamable_http_client

from agastya.agent.events import EventBus
from agastya.agent.mcp_http import McpHttpServer
from agastya.agent.tools import ToolContext
from agastya.profile.store import ProfileStore


class DummyBrowser:
    """Mock browser for tool context."""

    def __init__(self) -> None:
        self.url = "http://localhost/test"
        self.clicks: list[str] = []

    async def snapshot(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": "Test Page",
            "text": "Hello world",
            "fields": [],
            "actions": [],
        }

    async def goto(self, url: str) -> dict[str, Any]:
        self.url = url
        return await self.snapshot()

    async def click(self, ref: str) -> dict[str, Any]:
        self.clicks.append(ref)
        return await self.snapshot()

    async def screenshot(self) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake_screenshot"


@pytest.fixture
def tool_context(tmp_path) -> ToolContext:
    db_path = tmp_path / "profile.db"
    profile = ProfileStore(user_id="test_user", db_path=db_path)
    profile.set("full_name", "Rahul Sharma")
    profile.set("aadhaar", "123456789012")
    browser = DummyBrowser()
    bus = EventBus()

    async def dummy_ask_user(question: str, fields: Any = None) -> str:
        return "Answer"

    return ToolContext(
        profile=profile,
        browser=browser,
        bus=bus,
        ask_user=dummy_ask_user,
        playbooks={},
    )


@pytest.mark.asyncio
async def test_mcp_http_token_rejection(tool_context: ToolContext) -> None:
    server = McpHttpServer(ctx=tool_context)
    await server.start()
    try:
        url = server.endpoint_url

        async with httpx.AsyncClient() as client:
            # 1. No auth header -> 401
            resp_none = await client.post(url, json={})
            assert resp_none.status_code == 401

            # 2. Invalid Bearer token -> 401
            resp_bad = await client.post(
                url, json={}, headers={"Authorization": "Bearer bad_token_123"}
            )
            assert resp_bad.status_code == 401

            # 3. Valid Bearer token -> Not 401
            resp_good = await client.post(
                url, json={}, headers={"Authorization": f"Bearer {server.token}"}
            )
            assert resp_good.status_code != 401
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_mcp_http_lists_tools_and_schemas(tool_context: ToolContext) -> None:
    server = McpHttpServer(ctx=tool_context)
    await server.start()
    try:
        headers = {"Authorization": f"Bearer {server.token}"}
        async with httpx2.AsyncClient(headers=headers) as http_client:
            async with streamable_http_client(server.endpoint_url, http_client=http_client) as (
                read,
                write,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tool_list = await session.list_tools()
                    names = {t.name for t in tool_list.tools}

                    # Verify expected core agastya tools are present
                    expected = {
                        "get_profile",
                        "get_profile_value",
                        "save_profile_field",
                        "ask_user",
                        "list_playbooks",
                        "get_playbook",
                        "browser_goto",
                        "browser_snapshot",
                        "browser_fill",
                        "browser_fill_many",
                        "browser_select",
                        "browser_click",
                        "browser_screenshot",
                        "handoff_to_user",
                    }
                    assert expected.issubset(names)

                    # Verify input schema on get_profile_value
                    tool_map = {t.name: t for t in tool_list.tools}
                    gpv_schema = tool_map["get_profile_value"].input_schema
                    assert "field" in gpv_schema.get("properties", {})
                    assert "field" in gpv_schema.get("required", [])
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_mcp_http_calls_get_profile(tool_context: ToolContext) -> None:
    server = McpHttpServer(ctx=tool_context)
    await server.start()
    try:
        headers = {"Authorization": f"Bearer {server.token}"}
        async with httpx2.AsyncClient(headers=headers) as http_client:
            async with streamable_http_client(server.endpoint_url, http_client=http_client) as (
                read,
                write,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    res = await session.call_tool("get_profile", {})
                    assert not res.is_error
                    assert len(res.content) > 0
                    text_content = res.content[0].text
                    data = json.loads(text_content)

                    # Profile should contain full_name and masked aadhaar
                    assert data["full_name"] == "Rahul Sharma"
                    assert "XXXXXXXX9012" in data["aadhaar"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_mcp_http_safety_checks_and_blocking(tool_context: ToolContext) -> None:
    class DummySession:
        def __init__(self) -> None:
            self.user_in_control = False

    session_mock = DummySession()
    server = McpHttpServer(ctx=tool_context, session=session_mock)
    await server.start()

    events_queue = tool_context.bus.subscribe()
    try:
        headers = {"Authorization": f"Bearer {server.token}"}
        async with httpx2.AsyncClient(headers=headers) as http_client:
            async with streamable_http_client(server.endpoint_url, http_client=http_client) as (
                read,
                write,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # 1. Navigation blocked on payment gateway
                    res_nav = await session.call_tool(
                        "browser_goto", {"url": "https://paygov.gov.in/payment"}
                    )
                    assert len(res_nav.content) > 0
                    assert "Navigation blocked by safety guard" in res_nav.content[0].text
                    ev_nav = await events_queue.get()
                    assert ev_nav.type == "blocked"
                    assert ev_nav.data["tool"] == "browser_goto"

                    # 2. Click blocked on final submit
                    res_click = await session.call_tool(
                        "browser_click", {"ref": "a1", "text": "Final Submit"}
                    )
                    assert len(res_click.content) > 0
                    assert "Click blocked by safety guard" in res_click.content[0].text
                    ev_click = await events_queue.get()
                    assert ev_click.type == "blocked"
                    assert ev_click.data["tool"] == "browser_click"

                    # 3. User in control pause
                    session_mock.user_in_control = True
                    res_pause = await session.call_tool("browser_snapshot", {})
                    assert "PAUSED" in res_pause.content[0].text
                    ev_pause = await events_queue.get()
                    assert ev_pause.type == "blocked"
                    assert ev_pause.data["reason"] == "user_in_control"
    finally:
        await server.stop()
