"""Tests for Agastya Assist MCP tools."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any

import pytest

from agastya.agent.events import EventBus
from agastya.agent.tools import (
    TOOL_NAMES,
    BlockedActionError,
    ToolContext,
    build_handlers,
    build_mcp_server,
    format_snapshot,
)
from agastya.browser.driver import FieldInfo, PageSnapshot
from agastya.browser.guard import GuardDecision
from agastya.portals.loader import Playbook
from agastya.profile.store import ProfileStore


# Canned test models for browser snapshot testing
class FakeBrowser:
    """Fake BrowserDriver for tools unit testing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.canned_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfakeimage"
        self.current_url = "https://example.gov.in/form"
        self.current_title = "Government Portal Form"
        self.fields = [
            FieldInfo(
                ref="f1",
                tag="input",
                type="text",
                label="First Name",
                name="fname",
                id="fname",
                required=True,
                value="",
                options=[],
            ),
            FieldInfo(
                ref="f2",
                tag="select",
                type="select",
                label="State",
                name="state",
                id="state",
                required=False,
                value="DL",
                options=["DL", "MH", "KA"],
            ),
            FieldInfo(
                ref="f3",
                tag="input",
                type="text",
                label="Last Name",
                name="lname",
                id="lname",
                required=True,
                value="Smith",
                options=[],
            ),
        ]
        self.actions = [
            {"ref": "a1", "tag": "button", "text": "Save Draft"},
            {"ref": "a2", "tag": "button", "text": "Next"},
            {"ref": "a_submit", "tag": "button", "text": "Final Submit"},
        ]

    def _snapshot(self) -> PageSnapshot:
        return PageSnapshot(
            url=self.current_url,
            title=self.current_title,
            text="Welcome to the National Portal. Please fill in your application details accurately.",
            fields=self.fields,
            actions=self.actions,
        )

    async def goto(self, url: str) -> PageSnapshot:
        self.calls.append(("goto", url))
        self.current_url = url
        return self._snapshot()

    async def snapshot(self) -> PageSnapshot:
        self.calls.append(("snapshot", None))
        return self._snapshot()

    async def fill(self, ref: str, value: str) -> None:
        self.calls.append(("fill", (ref, value)))
        for f in self.fields:
            if f.ref == ref:
                f.value = value
                return
        raise ValueError(f"Unknown ref: '{ref}'. No element matches data-agastya-ref='{ref}'.")

    async def select(self, ref: str, option: str) -> None:
        self.calls.append(("select", (ref, option)))
        for f in self.fields:
            if f.ref == ref:
                f.value = option
                return
        raise ValueError(f"Unknown ref: '{ref}'. No element matches data-agastya-ref='{ref}'.")

    async def click(self, ref: str) -> PageSnapshot:
        self.calls.append(("click", ref))
        if ref == "a_submit":
            decision = GuardDecision(allowed=False, reason="final submit")
            raise BlockedActionError(decision)
        return self._snapshot()

    async def screenshot(self) -> bytes:
        self.calls.append(("screenshot", None))
        return self.canned_png


@pytest.fixture
def test_ctx(tmp_path: Path):
    db_file = tmp_path / "test_profile.db"
    profile = ProfileStore(user_id="user_test", db_path=db_file)
    browser = FakeBrowser()
    bus = EventBus()

    async def fake_ask_user(question: str, field: Any = None) -> str:
        if "bad" in question:
            return "bad_pan"
        if isinstance(field, list) and set(field) == {"mobile", "email"}:
            return "mera mobile 98765 43210 aur email a@b.in hai"
        if isinstance(field, list) and "date_of_birth" in field:
            return "नाम राहुल शर्मा और जन्म 5 मार्च 2001"
        first = field[0] if isinstance(field, list) and field else field
        if first == "full_name":
            return "Rishi Sharma"
        if first == "pan":
            return "ABCDE1234F"
        return "I confirm"

    playbooks = {
        "pan_49a": Playbook(
            id="pan_49a",
            portal="tin_nsdl",
            title_en="PAN Card Application (Form 49A)",
            title_hi="पैन कार्ड आवेदन (फॉर्म 49A)",
            live_url="https://onlineservices.tin.egov-nsdl.com/etaxnew/pan",
            fixture=None,
            requires_login=False,
            required_fields=["full_name", "date_of_birth", "mobile", "email"],
            optional_fields=["father_name"],
            field_hints={"full_name": ["name", "applicant_name"]},
            steps=["Navigate to portal", "Fill details", "Stop before payment"],
            stop_before=["Proceed to Pay", "Submit"],
            keywords=["pan", "form 49a", "पैन"],
        )
    }

    ctx = ToolContext(
        profile=profile,
        browser=browser,
        bus=bus,
        ask_user=fake_ask_user,
        playbooks=playbooks,
    )
    yield ctx
    profile.close()


def test_tool_names():
    assert len(TOOL_NAMES) == 16
    for name in TOOL_NAMES:
        assert name.startswith("mcp__agastya__")
    assert "mcp__agastya__get_profile" in TOOL_NAMES
    assert "mcp__agastya__browser_fill_many" in TOOL_NAMES
    assert "mcp__agastya__browser_click" in TOOL_NAMES
    assert "mcp__agastya__handoff_to_user" in TOOL_NAMES


def test_build_mcp_server(test_ctx: ToolContext):
    server = build_mcp_server(test_ctx)
    assert isinstance(server, dict)
    assert server["type"] == "sdk"
    assert server["name"] == "agastya"
    # The SDK serialises everything except "instance" to JSON for the CLI.
    assert "handlers" not in server
    json.dumps({k: v for k, v in server.items() if k != "instance"})
    assert hasattr(server, "handlers")
    assert len(server.handlers) == 16
    assert "get_profile" in server.handlers
    assert "browser_fill_many" in server.handlers
    assert "browser_click" in server.handlers


def test_format_snapshot():
    fields = [
        FieldInfo(
            ref="f1",
            tag="input",
            type="text",
            label="First Name",
            name="fname",
            id="fname",
            required=True,
            value="",
            options=[],
        ),
        FieldInfo(
            ref="f2",
            tag="input",
            type="text",
            label="Last Name",
            name="lname",
            id="lname",
            required=False,
            value="Patel",
            options=[],
        ),
    ]
    actions = [
        {"ref": "a1", "tag": "button", "text": "Save Draft"},
        {"ref": "a2", "tag": "button", "text": "Next"},
    ]
    snap = PageSnapshot(
        url="https://example.gov.in/test",
        title="Sample Portal",
        text="Sample heading text " + ("word " * 400),
        fields=fields,
        actions=actions,
    )
    formatted = format_snapshot(snap)

    assert "URL: https://example.gov.in/test" in formatted
    assert "Title: Sample Portal" in formatted
    assert "f1 | text | First Name* | value=''" in formatted
    assert "f2 | text | Last Name | value='Patel'" in formatted
    assert "a1 | button | Save Draft" in formatted
    assert "a2 | button | Next" in formatted
    # Check text truncation to <= 1500 chars
    text_section = formatted.split("Fields:")[0]
    assert len(text_section) <= 1700


@pytest.mark.asyncio
async def test_get_profile_and_masking(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)

    # Pre-populate sensitive and normal fields
    test_ctx.profile.set("full_name", "Rishi Kumar")
    test_ctx.profile.set("pan", "ABCDE1234F")
    test_ctx.profile.set("aadhaar", "123456789012")
    test_ctx.profile.set("bank_account_number", "1234567890")

    result = await handlers["get_profile"]()
    assert not result.get("is_error")
    data = json.loads(result["content"][0]["text"])

    # Sensitive fields masked
    assert data["profile"]["pan"] == "XXXXX1234X"
    assert data["profile"]["aadhaar"] == "XXXXXXXX9012"
    assert data["profile"]["bank_account_number"] == "XXXXXX7890"
    assert data["profile"]["full_name"] == "Rishi Kumar"

    # Also check direct access
    assert data["pan"] == "XXXXX1234X"

    # Missing catalog fields listed
    assert "missing_fields" in data
    assert "date_of_birth" in data["missing_fields"]
    assert "mobile" in data["missing_fields"]
    assert "pan" not in data["missing_fields"]


@pytest.mark.asyncio
async def test_get_profile_value(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    test_ctx.profile.set("pan", "ABCDE1234F")

    # Real unmasked value returned for valid field
    result = await handlers["get_profile_value"]({"field": "pan"})
    assert not result.get("is_error")
    assert result["content"][0]["text"] == "ABCDE1234F"

    # Non-existent field returns is_error
    missing_result = await handlers["get_profile_value"]({"field": "nonexistent_field"})
    assert missing_result.get("is_error") is True
    assert "not set" in missing_result["content"][0]["text"]


@pytest.mark.asyncio
async def test_save_profile_field_valid_and_invalid(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    q = test_ctx.bus.subscribe()

    # Valid field save
    result = await handlers["save_profile_field"]({"field": "full_name", "value": "Aarav Sharma"})
    assert not result.get("is_error")
    assert test_ctx.profile.get("full_name") == "Aarav Sharma"

    # Verify profile_updated event emitted
    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "profile_updated"
    assert ev.data["field"] == "full_name"
    assert ev.data["value"] == "Aarav Sharma"

    # Invalid field save (invalid PAN pattern)
    bad_result = await handlers["save_profile_field"]({"field": "pan", "value": "bad_pan_123"})
    assert bad_result.get("is_error") is True
    assert "Invalid value for field 'pan'" in bad_result["content"][0]["text"]


@pytest.mark.asyncio
async def test_ask_user_saving_field(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    q = test_ctx.bus.subscribe()

    # ask_user with legacy field="full_name" -> fake_ask_user returns "Rishi Sharma"
    res = await handlers["ask_user"]({"question": "What is your name?", "field": "full_name"})
    assert not res.get("is_error")
    data = json.loads(res["content"][0]["text"])
    assert data["answer"] == "Rishi Sharma"
    # Free-text field: returned to the agent (it saves via save_profile_field), not auto-saved
    assert data["saved"] == {}
    assert data["unparsed"] == ["full_name"]
    assert test_ctx.profile.get("full_name") is None
    assert q.empty()

    # ask_user with field=None -> not saved
    res_no_field = await handlers["ask_user"]({"question": "Proceed?"})
    assert not res_no_field.get("is_error")
    data_no_field = json.loads(res_no_field["content"][0]["text"])
    assert data_no_field["answer"] == "I confirm"
    assert data_no_field["saved"] == {}
    assert data_no_field["unparsed"] == []
    assert data_no_field["errors"] == {}

    # ask_user with field="pan" but invalid answer -> fake_ask_user returns "bad_pan" which fails validation
    res_bad = await handlers["ask_user"]({"question": "bad pan?", "field": "pan"})
    assert not res_bad.get("is_error")
    data_bad = json.loads(res_bad["content"][0]["text"])
    assert data_bad["answer"] == "bad_pan"
    assert data_bad["saved"] == {}
    assert data_bad["unparsed"] == ["pan"]
    assert "pan" in data_bad["errors"]
    assert "Invalid value for field 'pan'" in data_bad["errors"]["pan"]
    # Not saved in profile
    assert test_ctx.profile.get("pan") is None


@pytest.mark.asyncio
async def test_ask_user_grouped_flow(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    q = test_ctx.bus.subscribe()

    # Ask 2 fields: mobile + email
    res = await handlers["ask_user"]({
        "question": "कृपया अपना मोबाइल नंबर और ईमेल बताएं",
        "fields": ["mobile", "email"],
    })
    assert not res.get("is_error")
    data = json.loads(res["content"][0]["text"])
    assert data["answer"] == "mera mobile 98765 43210 aur email a@b.in hai"
    assert data["saved"] == {"mobile": "9876543210", "email": "a@b.in"}
    assert data["unparsed"] == []
    assert data["errors"] == {}

    # Check store
    assert test_ctx.profile.get("mobile") == "9876543210"
    assert test_ctx.profile.get("email") == "a@b.in"

    # Two profile_updated events emitted
    events = []
    events.append(await asyncio.wait_for(q.get(), timeout=1.0))
    events.append(await asyncio.wait_for(q.get(), timeout=1.0))
    fields_emitted = {e.data["field"] for e in events}
    assert fields_emitted == {"mobile", "email"}


@pytest.mark.asyncio
async def test_ask_user_grouped_partial_extraction(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)

    # Ask full_name and date_of_birth
    res = await handlers["ask_user"]({
        "question": "नाम और जन्म तिथि बताएं",
        "fields": ["full_name", "date_of_birth"],
    })
    assert not res.get("is_error")
    data = json.loads(res["content"][0]["text"])
    # date_of_birth was extracted and saved, full_name remains unparsed
    assert data["saved"] == {"date_of_birth": "05/03/2001"}
    assert data["unparsed"] == ["full_name"]
    assert data["errors"] == {}
    assert test_ctx.profile.get("date_of_birth") == "05/03/2001"


@pytest.mark.asyncio
async def test_ask_user_unknown_field_error(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)

    # Unknown key in fields list
    res_fields = await handlers["ask_user"]({
        "question": "Tell me foo",
        "fields": ["non_existent_key_123"],
    })
    assert res_fields.get("is_error") is True
    err_text = res_fields["content"][0]["text"]
    assert "Unknown field key(s): non_existent_key_123" in err_text
    assert "Valid keys are:" in err_text
    assert "full_name" in err_text

    # Unknown key in legacy field param
    res_field = await handlers["ask_user"]({
        "question": "Tell me bar",
        "field": "invalid_single_field",
    })
    assert res_field.get("is_error") is True
    assert "Unknown field key(s): invalid_single_field" in res_field["content"][0]["text"]


@pytest.mark.asyncio
async def test_ask_user_sensitive_masking_in_saved(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)

    # PAN should be masked in saved dict
    res = await handlers["ask_user"]({"question": "PAN number?", "fields": ["pan"]})
    assert not res.get("is_error")
    data = json.loads(res["content"][0]["text"])
    assert data["saved"] == {"pan": "XXXXX1234X"}
    # But unmasked in profile store
    assert test_ctx.profile.get("pan") == "ABCDE1234F"


@pytest.mark.asyncio
async def test_playbooks_handlers(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)

    list_res = await handlers["list_playbooks"]()
    assert not list_res.get("is_error")
    playbooks = json.loads(list_res["content"][0]["text"])
    assert len(playbooks) == 1
    assert playbooks[0]["id"] == "pan_49a"

    get_res = await handlers["get_playbook"]({"id": "pan_49a"})
    assert not get_res.get("is_error")
    pb_data = json.loads(get_res["content"][0]["text"])
    assert pb_data["portal"] == "tin_nsdl"
    assert "full_name" in pb_data["required_fields"]

    bad_pb = await handlers["get_playbook"]({"id": "nonexistent"})
    assert bad_pb.get("is_error") is True
    assert "not found" in bad_pb["content"][0]["text"]


@pytest.mark.asyncio
async def test_browser_goto_emits_screenshot(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    q = test_ctx.bus.subscribe()

    target_url = "https://services.india.gov.in"
    res = await handlers["browser_goto"]({"url": target_url})
    assert not res.get("is_error")
    assert "URL: https://services.india.gov.in" in res["content"][0]["text"]

    # Verify screenshot event emitted automatically
    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "screenshot"
    assert "png_b64" in ev.data
    assert ev.data["url"] == target_url
    assert base64.b64decode(ev.data["png_b64"]) == test_ctx.browser.canned_png


@pytest.mark.asyncio
async def test_browser_snapshot(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    res = await handlers["browser_snapshot"]()
    assert not res.get("is_error")
    text = res["content"][0]["text"]
    assert "URL: https://example.gov.in/form" in text
    assert "f1 | text | First Name* | value=''" in text
    assert "a1 | button | Save Draft" in text


@pytest.mark.asyncio
async def test_browser_fill_and_select(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)

    fill_res = await handlers["browser_fill"]({"ref": "f1", "value": "Aarav"})
    assert not fill_res.get("is_error")
    assert ("fill", ("f1", "Aarav")) in test_ctx.browser.calls

    select_res = await handlers["browser_select"]({"ref": "f2", "option": "MH"})
    assert not select_res.get("is_error")
    assert ("select", ("f2", "MH")) in test_ctx.browser.calls


@pytest.mark.asyncio
async def test_browser_click_allowed_emits_screenshot(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    q = test_ctx.bus.subscribe()

    res = await handlers["browser_click"]({"ref": "a2"})
    assert not res.get("is_error")

    # Screenshot event emitted on allowed click
    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "screenshot"
    assert "png_b64" in ev.data


@pytest.mark.asyncio
async def test_browser_click_blocked_emits_blocked_event(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    q = test_ctx.bus.subscribe()

    res = await handlers["browser_click"]({"ref": "a_submit"})

    # Must NOT be is_error
    assert res.get("is_error") is False
    assert (
        res["content"][0]["text"]
        == "BLOCKED: final submit. This is a final action. Tell the user what is filled and call handoff_to_user."
    )

    # Blocked event emitted
    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "blocked"
    assert ev.data["reason"] == "final submit"
    assert ev.data["ref"] == "a_submit"


@pytest.mark.asyncio
async def test_browser_screenshot_tool(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    q = test_ctx.bus.subscribe()

    res = await handlers["browser_screenshot"]()
    assert not res.get("is_error")
    assert "Screenshot captured" in res["content"][0]["text"]

    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "screenshot"
    assert base64.b64decode(ev.data["png_b64"]) == test_ctx.browser.canned_png


@pytest.mark.asyncio
async def test_handoff_to_user(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    q = test_ctx.bus.subscribe()

    res = await handlers["handoff_to_user"]({"reason": "Aadhaar OTP required"})
    assert not res.get("is_error")
    assert "Aadhaar OTP required" in res["content"][0]["text"]

    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "status"
    assert ev.data["handoff"] is True
    assert ev.data["reason"] == "Aadhaar OTP required"


@pytest.mark.asyncio
async def test_error_handling_in_handlers(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)

    # Browser error during fill
    async def failing_fill(ref: str, value: str):
        raise RuntimeError("Element not found")

    test_ctx.browser.fill = failing_fill
    res_fill = await handlers["browser_fill"]({"ref": "f99", "value": "val"})
    assert res_fill.get("is_error") is True
    assert "Element not found" in res_fill["content"][0]["text"]

    # Browser error during select
    async def failing_select(ref: str, option: str):
        raise RuntimeError("Dropdown disabled")

    test_ctx.browser.select = failing_select
    res_sel = await handlers["browser_select"]({"ref": "f99", "option": "opt"})
    assert res_sel.get("is_error") is True
    assert "Dropdown disabled" in res_sel["content"][0]["text"]

    # Browser error during snapshot
    async def failing_snapshot():
        raise RuntimeError("Target crashed")

    test_ctx.browser.snapshot = failing_snapshot
    res_snap = await handlers["browser_snapshot"]()
    assert res_snap.get("is_error") is True
    assert "Target crashed" in res_snap["content"][0]["text"]

    # Navigation error during goto
    async def failing_goto(url: str):
        raise RuntimeError("DNS resolution failed")

    test_ctx.browser.goto = failing_goto
    res_goto = await handlers["browser_goto"]({"url": "https://invalid.url"})
    assert res_goto.get("is_error") is True
    assert "DNS resolution failed" in res_goto["content"][0]["text"]

    # Blocked goto
    async def blocked_goto(url: str):
        decision = GuardDecision(allowed=False, reason="payment gateway")
        raise BlockedActionError(decision)

    test_ctx.browser.goto = blocked_goto
    q = test_ctx.bus.subscribe()
    res_bgoto = await handlers["browser_goto"]({"url": "https://paygov.gov.in"})
    assert res_bgoto.get("is_error") is False
    assert "BLOCKED: payment gateway" in res_bgoto["content"][0]["text"]
    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.type == "blocked"
    assert ev.data["reason"] == "payment gateway"

    # Ask user failure
    async def failing_ask(question: str, field: str | None = None):
        raise RuntimeError("Channel closed")

    test_ctx.ask_user = failing_ask
    handlers_fail = build_handlers(test_ctx)
    res_ask = await handlers_fail["ask_user"]({"question": "hello"})
    assert res_ask.get("is_error") is True
    assert "Channel closed" in res_ask["content"][0]["text"]



async def test_ask_user_single_free_text_field_is_not_auto_saved(test_ctx):
    async def fake_ask(question, fields=None):
        return "skip karo, baad me dungi"

    test_ctx.ask_user = fake_ask
    res = await build_handlers(test_ctx)["ask_user"]({"question": "ling?", "fields": ["gender"]})
    data = json.loads(res["content"][0]["text"])
    assert data["unparsed"] == ["gender"]
    assert test_ctx.profile.get("gender") is None


async def test_profile_updated_event_is_masked(test_ctx):
    q = test_ctx.bus.subscribe()
    await build_handlers(test_ctx)["save_profile_field"]({"field": "aadhaar", "value": "2345 6789 0123"})
    ev = await asyncio.wait_for(q.get(), 1)
    assert ev.type == "profile_updated"
    assert "234567890123" not in json.dumps(ev.data)


@pytest.mark.asyncio
async def test_browser_fill_many_success(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    items = [
        {"ref": "f1", "value": "Jane", "kind": "fill"},
        {"ref": "f2", "value": "MH", "kind": "select"},
        {"ref": "f3", "value": "Doe", "kind": "fill"},
    ]
    res = await handlers["browser_fill_many"]({"items": items})
    assert not res.get("is_error")
    data = json.loads(res["content"][0]["text"])
    assert data["ok_count"] == 3
    assert data["error_count"] == 0
    assert len(data["results"]) == 3
    assert all(r["ok"] is True for r in data["results"])

    # Verify browser fields state
    field_map = {f.ref: f.value for f in test_ctx.browser.fields}
    assert field_map["f1"] == "Jane"
    assert field_map["f2"] == "MH"
    assert field_map["f3"] == "Doe"


@pytest.mark.asyncio
async def test_browser_fill_many_partial_failure(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    items = [
        {"ref": "f1", "value": "Alice", "kind": "fill"},
        {"ref": "f_invalid", "value": "Bob", "kind": "fill"},
    ]
    res = await handlers["browser_fill_many"]({"items": items})
    assert not res.get("is_error")
    data = json.loads(res["content"][0]["text"])
    assert data["ok_count"] == 1
    assert data["error_count"] == 1
    assert data["results"][0]["ref"] == "f1"
    assert data["results"][0]["ok"] is True
    assert data["results"][1]["ref"] == "f_invalid"
    assert data["results"][1]["ok"] is False
    assert "Unknown ref" in data["results"][1]["error"]


@pytest.mark.asyncio
async def test_browser_tools_paused_when_user_in_control(test_ctx: ToolContext):
    test_ctx.is_user_in_control = lambda: True
    handlers = build_handlers(test_ctx)

    expected_pause = "PAUSED: the citizen is controlling the browser. Wait."

    res_goto = await handlers["browser_goto"]({"url": "https://example.gov.in"})
    assert res_goto["content"][0]["text"] == expected_pause

    res_snap = await handlers["browser_snapshot"]()
    assert res_snap["content"][0]["text"] == expected_pause

    res_fill = await handlers["browser_fill"]({"ref": "f1", "value": "test"})
    assert res_fill["content"][0]["text"] == expected_pause

    res_fill_many = await handlers["browser_fill_many"]({"items": [{"ref": "f1", "value": "test"}]})
    assert res_fill_many["content"][0]["text"] == expected_pause

    res_select = await handlers["browser_select"]({"ref": "f2", "option": "MH"})
    assert res_select["content"][0]["text"] == expected_pause

    res_click = await handlers["browser_click"]({"ref": "a1"})
    assert res_click["content"][0]["text"] == expected_pause

    res_shot = await handlers["browser_screenshot"]()
    assert res_shot["content"][0]["text"] == expected_pause


@pytest.mark.asyncio
async def test_screenshot_throttling(test_ctx: ToolContext):
    handlers = build_handlers(test_ctx)
    q = test_ctx.bus.subscribe()

    # Initial goto emits screenshot
    await handlers["browser_goto"]({"url": "https://example.gov.in/start"})
    ev1 = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev1.type == "screenshot"

    # Immediate fill_many (<2s) should be throttled and NOT emit a new screenshot
    await handlers["browser_fill_many"]({"items": [{"ref": "f1", "value": "test"}]})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.1)

    # Advance the throttle timestamp by simulating elapsed time (>2s)
    test_ctx._last_screenshot_time = time.monotonic() - 3.0

    # Next action (click a1) should now emit screenshot
    await handlers["browser_click"]({"ref": "a1"})
    ev2 = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev2.type == "screenshot"



@pytest.mark.asyncio
async def test_list_documents_tool(test_ctx: ToolContext):
    """Test list_documents tool handler."""
    handlers = build_handlers(test_ctx)

    # Empty store returns empty list
    res = await handlers["list_documents"]()
    assert res.get("is_error") is not True
    items = json.loads(res["content"][0]["text"])
    assert items == []

    # Add a document to the user's document store
    from scripts.make_sample_form16 import build_form16_pdf

    store = getattr(test_ctx, "documents", None)
    if store is None:
        from agastya.documents.store import DocumentStore

        test_ctx.documents = DocumentStore(
            user_id=test_ctx.profile.user_id,
            data_dir=test_ctx.profile.db_path.parent,
        )
        store = test_ctx.documents

    doc = store.add("sample_form16.pdf", build_form16_pdf())

    # Now list_documents returns the document with required fields
    res2 = await handlers["list_documents"]()
    assert res2.get("is_error") is not True
    items2 = json.loads(res2["content"][0]["text"])
    assert len(items2) == 1
    assert items2[0]["id"] == doc.id
    assert items2[0]["filename"] == "sample_form16.pdf"
    assert items2[0]["kind"] == "form16"
    assert "uploaded_at" in items2[0]


@pytest.mark.asyncio
async def test_read_document_tool(test_ctx: ToolContext):
    """Test read_document tool handler with PAN masking and error handling."""
    handlers = build_handlers(test_ctx)

    # Missing doc_id argument returns error
    res_err1 = await handlers["read_document"]({})
    assert res_err1.get("is_error") is True
    assert "Missing required argument" in res_err1["content"][0]["text"]

    # Non-existent doc_id returns error
    res_err2 = await handlers["read_document"]({"doc_id": "non_existent_123"})
    assert res_err2.get("is_error") is True
    assert "Document not found" in res_err2["content"][0]["text"]

    # Add specimen Form 16 document
    from scripts.make_sample_form16 import build_form16_pdf

    if getattr(test_ctx, "documents", None) is None:
        from agastya.documents.store import DocumentStore

        test_ctx.documents = DocumentStore(
            user_id=test_ctx.profile.user_id,
            data_dir=test_ctx.profile.db_path.parent,
        )
    store = test_ctx.documents
    doc = store.add("test_form16.pdf", build_form16_pdf())

    # Read existing document
    res_ok = await handlers["read_document"]({"doc_id": doc.id})
    assert res_ok.get("is_error") is not True
    extracted = json.loads(res_ok["content"][0]["text"])

    assert extracted["kind"] == "form16"
    # Verify employee PAN is masked via agastya.profile.fields.masked (ABCDE1234F -> XXXXX1234X)
    assert extracted["employee_pan"] == "XXXXX1234X"
    # Unmasked numerical values are intact
    assert extracted["salary_17_1"] == 1250000
    assert extracted["gross_salary"] == 1275000
    assert extracted["total_taxable_income"] == 1197500
    assert extracted["tds_total"] == 82810


@pytest.mark.asyncio
async def test_document_tools_lazy_store_initialization(test_ctx: ToolContext):
    """Test lazy DocumentStore initialization when test_ctx.documents is None."""
    test_ctx.documents = None
    handlers = build_handlers(test_ctx)

    # Calling list_documents lazily creates DocumentStore
    res = await handlers["list_documents"]()
    assert res.get("is_error") is not True
    assert test_ctx.documents is not None
    assert test_ctx.documents.user_id == test_ctx.profile.user_id
