"""Agastya Assist MCP tools for Claude Agent SDK."""

from __future__ import annotations

import base64
import dataclasses
from dataclasses import asdict, dataclass, is_dataclass
import json
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from agastya.agent.events import EventBus
from agastya.profile.fields import FIELDS, masked
from agastya.profile.store import ProfileStore

if TYPE_CHECKING:
    from agastya.browser.driver import BrowserDriver, PageSnapshot
    from agastya.portals.loader import Playbook

# Attempt import of BlockedActionError from browser.driver; provide fallback if not yet ready.
try:
    from agastya.browser.driver import BlockedActionError
except ImportError:
    class BlockedActionError(Exception):  # type: ignore[no-redef]
        def __init__(self, decision: Any = None, *args: Any):
            self.decision = decision
            super().__init__(str(decision) if decision else "", *args)


logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    profile: ProfileStore
    browser: BrowserDriver
    bus: EventBus
    ask_user: Callable[[str, str | None], Awaitable[str]]  # (question, field) -> answer text
    playbooks: dict[str, Playbook]


TOOL_NAMES: list[str] = [
    "mcp__agastya__get_profile",
    "mcp__agastya__get_profile_value",
    "mcp__agastya__save_profile_field",
    "mcp__agastya__ask_user",
    "mcp__agastya__list_playbooks",
    "mcp__agastya__get_playbook",
    "mcp__agastya__browser_goto",
    "mcp__agastya__browser_snapshot",
    "mcp__agastya__browser_fill",
    "mcp__agastya__browser_select",
    "mcp__agastya__browser_click",
    "mcp__agastya__browser_screenshot",
    "mcp__agastya__handoff_to_user",
]


# Input schemas with precise JSON Schema definitions and descriptions
GET_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

GET_PROFILE_VALUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "field": {
            "type": "string",
            "description": "Name of the profile field to retrieve (e.g. 'full_name', 'pan', 'aadhaar', 'date_of_birth'). Used only to fill form fields.",
        }
    },
    "required": ["field"],
    "additionalProperties": False,
}

SAVE_PROFILE_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "field": {
            "type": "string",
            "description": "Profile field name to save (e.g. 'full_name', 'pan', 'mobile', 'date_of_birth').",
        },
        "value": {
            "type": "string",
            "description": "The value to store for the field. Validated against the catalog validator.",
        },
    },
    "required": ["field", "value"],
    "additionalProperties": False,
}

ASK_USER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "The question to ask the user in their language.",
        },
        "field": {
            "type": "string",
            "description": "Optional name of the profile field being asked for (e.g. 'father_name', 'pan'). If valid, it is automatically saved to the profile.",
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}

LIST_PLAYBOOKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

GET_PLAYBOOK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "The unique identifier of the playbook (e.g. 'pan_49a', 'itr_login', 'voter_form6').",
        }
    },
    "required": ["id"],
    "additionalProperties": False,
}

BROWSER_GOTO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "The URL to navigate to.",
        }
    },
    "required": ["url"],
    "additionalProperties": False,
}

BROWSER_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

BROWSER_FILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ref": {
            "type": "string",
            "description": "The stable element reference from the snapshot (e.g. 'f1', 'f3').",
        },
        "value": {
            "type": "string",
            "description": "The text value to enter into the input or textarea.",
        },
    },
    "required": ["ref", "value"],
    "additionalProperties": False,
}

BROWSER_SELECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ref": {
            "type": "string",
            "description": "The stable element reference from the snapshot (e.g. 'f2').",
        },
        "option": {
            "type": "string",
            "description": "The visible option text or value to select in the dropdown or radio group.",
        },
    },
    "required": ["ref", "option"],
    "additionalProperties": False,
}

BROWSER_CLICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ref": {
            "type": "string",
            "description": "The stable action reference from the snapshot (e.g. 'a1', 'a2').",
        }
    },
    "required": ["ref"],
    "additionalProperties": False,
}

BROWSER_SCREENSHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

HANDOFF_TO_USER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {
            "type": "string",
            "description": "The reason for handing control to the user (e.g. final submit, payment, OTP, captcha, credentials).",
        }
    },
    "required": ["reason"],
    "additionalProperties": False,
}


def _is_blocked_action_error(exc: BaseException) -> bool:
    """Check if exception is a BlockedActionError from any module."""
    try:
        from agastya.browser.driver import BlockedActionError as RealBlockedActionError
        if isinstance(exc, RealBlockedActionError):
            return True
    except (ImportError, AttributeError):
        pass
    if isinstance(exc, BlockedActionError):
        return True
    return type(exc).__name__ == "BlockedActionError"


def _get_blocked_reason(exc: BaseException) -> str:
    """Extract reason string from a BlockedActionError."""
    decision = getattr(exc, "decision", None)
    if decision is not None:
        if hasattr(decision, "reason"):
            return str(decision.reason)
        if isinstance(decision, dict) and "reason" in decision:
            return str(decision["reason"])
        return str(decision)
    if hasattr(exc, "reason"):
        return str(getattr(exc, "reason"))
    msg = str(exc).strip()
    return msg if msg else "Final action blocked"


def format_snapshot(snapshot: Any) -> str:
    """Format a PageSnapshot into compact text per specifications.

    Output format:
    - URL: <url>
    - Title: <title>
    - Text: first 1500 chars of page text
    - One line per field: f3 | text | Last Name* | value=''
    - One line per action: a2 | button | Next
    """
    url = getattr(snapshot, "url", None)
    if url is None and isinstance(snapshot, dict):
        url = snapshot.get("url", "")
    url = str(url or "")

    title = getattr(snapshot, "title", None)
    if title is None and isinstance(snapshot, dict):
        title = snapshot.get("title", "")
    title = str(title or "")

    raw_text = getattr(snapshot, "text", None)
    if raw_text is None and isinstance(snapshot, dict):
        raw_text = snapshot.get("text", "")
    text_snippet = str(raw_text or "")[:1500]

    fields = getattr(snapshot, "fields", None)
    if fields is None and isinstance(snapshot, dict):
        fields = snapshot.get("fields", [])
    fields = fields or []

    actions = getattr(snapshot, "actions", None)
    if actions is None and isinstance(snapshot, dict):
        actions = snapshot.get("actions", [])
    actions = actions or []

    lines = [
        f"URL: {url}",
        f"Title: {title}",
        f"Text:\n{text_snippet}\n",
        "Fields:",
    ]

    for f in fields:
        ref = getattr(f, "ref", None) if not isinstance(f, dict) else f.get("ref")
        ftype = getattr(f, "type", None) if not isinstance(f, dict) else f.get("type")
        tag = getattr(f, "tag", None) if not isinstance(f, dict) else f.get("tag")
        label = getattr(f, "label", None) if not isinstance(f, dict) else f.get("label")
        name = getattr(f, "name", None) if not isinstance(f, dict) else f.get("name")
        fid = getattr(f, "id", None) if not isinstance(f, dict) else f.get("id")
        required = getattr(f, "required", False) if not isinstance(f, dict) else f.get("required", False)
        val = getattr(f, "value", "") if not isinstance(f, dict) else f.get("value", "")

        ref_str = str(ref or "")
        type_str = str(ftype or tag or "text")
        label_str = str(label or name or fid or "").strip()
        if required and not label_str.endswith("*"):
            label_str = f"{label_str}*"
        val_str = str(val if val is not None else "")
        lines.append(f"{ref_str} | {type_str} | {label_str} | value='{val_str}'")

    lines.append("\nActions:")
    for a in actions:
        ref = getattr(a, "ref", None) if not isinstance(a, dict) else a.get("ref")
        tag = getattr(a, "tag", None) if not isinstance(a, dict) else a.get("tag")
        act_text = getattr(a, "text", None) if not isinstance(a, dict) else a.get("text")
        lines.append(f"{str(ref or '')} | {str(tag or 'button')} | {str(act_text or '').strip()}")

    return "\n".join(lines)


class AgastyaMcpServerConfig(dict):
    """SDK server config dict; raw handlers kept as an attribute so they never reach the CLI JSON."""

    handlers: dict[str, Callable[[dict[str, Any] | None], Awaitable[dict[str, Any]]]]


def build_handlers(
    ctx: ToolContext,
) -> dict[str, Callable[[dict[str, Any] | None], Awaitable[dict[str, Any]]]]:
    """Build raw async handlers closing over ToolContext."""

    async def get_profile(args: dict[str, Any] | None = None) -> dict[str, Any]:
        stored = ctx.profile.all() if hasattr(ctx.profile, "all") else {}
        masked_data = {k: masked(k, v) for k, v in stored.items()}
        missing_fields = [f for f in FIELDS if f not in stored or not str(stored[f]).strip()]

        data = {
            "profile": masked_data,
            "missing_fields": missing_fields,
            **masked_data,
        }
        return {
            "content": [{"type": "text", "text": json.dumps(data, indent=2, ensure_ascii=False)}],
        }

    async def get_profile_value(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        field = args.get("field", "")
        val = ctx.profile.get(field)
        if val is None:
            return {
                "content": [{"type": "text", "text": f"Profile field '{field}' is not set."}],
                "is_error": True,
            }
        return {
            "content": [{"type": "text", "text": str(val)}],
        }

    async def save_profile_field(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        field = args.get("field", "")
        value = args.get("value", "")
        try:
            ctx.profile.set(field, value)
        except Exception as e:
            return {
                "content": [{"type": "text", "text": str(e)}],
                "is_error": True,
            }

        saved_val = ctx.profile.get(field)
        ctx.bus.emit("profile_updated", field=field, value=saved_val)
        return {
            "content": [{"type": "text", "text": f"Saved field '{field}' successfully."}],
        }

    async def ask_user(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        question = args.get("question", "")
        field = args.get("field")
        try:
            answer = await ctx.ask_user(question, field)
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error asking user: {e}"}],
                "is_error": True,
            }

        if field:
            try:
                ctx.profile.set(field, answer)
                saved_val = ctx.profile.get(field)
                ctx.bus.emit("profile_updated", field=field, value=saved_val)
            except Exception as e:
                logger.info(
                    f"Field '{field}' could not be validated/saved from user answer '{answer}': {e}"
                )

        return {
            "content": [{"type": "text", "text": str(answer)}],
        }

    async def list_playbooks(args: dict[str, Any] | None = None) -> dict[str, Any]:
        playbooks = ctx.playbooks or {}
        items = []
        for pid, pb in playbooks.items():
            if dataclasses.is_dataclass(pb):
                items.append(dataclasses.asdict(pb))
            elif isinstance(pb, dict):
                items.append(pb)
            else:
                items.append({
                    "id": getattr(pb, "id", pid),
                    "portal": getattr(pb, "portal", ""),
                    "title_en": getattr(pb, "title_en", ""),
                    "title_hi": getattr(pb, "title_hi", ""),
                    "live_url": getattr(pb, "live_url", ""),
                    "requires_login": getattr(pb, "requires_login", False),
                })
        return {
            "content": [{"type": "text", "text": json.dumps(items, indent=2, ensure_ascii=False)}],
        }

    async def get_playbook(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        pid = args.get("id", "")
        playbooks = ctx.playbooks or {}
        pb = playbooks.get(pid)
        if pb is None:
            return {
                "content": [{"type": "text", "text": f"Playbook '{pid}' not found."}],
                "is_error": True,
            }
        if dataclasses.is_dataclass(pb):
            data = dataclasses.asdict(pb)
        elif isinstance(pb, dict):
            data = pb
        else:
            data = {k: getattr(pb, k) for k in dir(pb) if not k.startswith("_")}
        return {
            "content": [{"type": "text", "text": json.dumps(data, indent=2, ensure_ascii=False)}],
        }

    async def browser_goto(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        url = args.get("url", "")
        try:
            snapshot = await ctx.browser.goto(url)
        except Exception as e:
            if _is_blocked_action_error(e):
                reason = _get_blocked_reason(e)
                ctx.bus.emit("blocked", reason=reason, ref=url)
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"BLOCKED: {reason}. This is a final action. Tell the user what is filled and call handoff_to_user.",
                        }
                    ],
                    "is_error": False,
                }
            return {
                "content": [{"type": "text", "text": f"Error navigating to {url}: {e}"}],
                "is_error": True,
            }

        # Page-changing browser action automatically emits screenshot event
        try:
            png_bytes = await ctx.browser.screenshot()
            png_b64 = base64.b64encode(png_bytes).decode("utf-8")
            current_url = getattr(snapshot, "url", url)
            ctx.bus.emit("screenshot", png_b64=png_b64, url=str(current_url or ""))
        except Exception as e:
            logger.warning(f"Failed to capture screenshot after goto: {e}")

        return {
            "content": [{"type": "text", "text": format_snapshot(snapshot)}],
        }

    async def browser_snapshot(args: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            snapshot = await ctx.browser.snapshot()
            return {
                "content": [{"type": "text", "text": format_snapshot(snapshot)}],
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error taking snapshot: {e}"}],
                "is_error": True,
            }

    async def browser_fill(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        ref = args.get("ref", "")
        value = args.get("value", "")
        try:
            await ctx.browser.fill(ref, value)
            return {
                "content": [{"type": "text", "text": f"Filled {ref}."}],
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error filling {ref}: {e}"}],
                "is_error": True,
            }

    async def browser_select(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        ref = args.get("ref", "")
        option = args.get("option", "")
        try:
            await ctx.browser.select(ref, option)
            return {
                "content": [{"type": "text", "text": f"Selected '{option}' on {ref}."}],
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error selecting '{option}' on {ref}: {e}"}],
                "is_error": True,
            }

    async def browser_click(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        ref = args.get("ref", "")
        try:
            snapshot = await ctx.browser.click(ref)
        except Exception as e:
            if _is_blocked_action_error(e):
                reason = _get_blocked_reason(e)
                ctx.bus.emit("blocked", reason=reason, ref=ref)
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"BLOCKED: {reason}. This is a final action. Tell the user what is filled and call handoff_to_user.",
                        }
                    ],
                    "is_error": False,
                }
            return {
                "content": [{"type": "text", "text": f"Error clicking {ref}: {e}"}],
                "is_error": True,
            }

        # Page-changing browser action automatically emits screenshot event
        try:
            png_bytes = await ctx.browser.screenshot()
            png_b64 = base64.b64encode(png_bytes).decode("utf-8")
            current_url = getattr(snapshot, "url", "")
            ctx.bus.emit("screenshot", png_b64=png_b64, url=str(current_url or ""))
        except Exception as e:
            logger.warning(f"Failed to capture screenshot after click: {e}")

        return {
            "content": [{"type": "text", "text": format_snapshot(snapshot)}],
        }

    async def browser_screenshot(args: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            png_bytes = await ctx.browser.screenshot()
            png_b64 = base64.b64encode(png_bytes).decode("utf-8")
            url = getattr(ctx.browser, "url", "")
            if callable(url):
                url = url()
            ctx.bus.emit("screenshot", png_b64=png_b64, url=str(url or ""))
            return {
                "content": [{"type": "text", "text": f"Screenshot captured ({len(png_bytes)} bytes)."}],
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error capturing screenshot: {e}"}],
                "is_error": True,
            }

    async def handoff_to_user(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        reason = args.get("reason", "")
        ctx.bus.emit("status", handoff=True, reason=reason)
        return {
            "content": [{"type": "text", "text": f"Handoff to user initiated: {reason}"}],
        }

    return {
        "get_profile": get_profile,
        "get_profile_value": get_profile_value,
        "save_profile_field": save_profile_field,
        "ask_user": ask_user,
        "list_playbooks": list_playbooks,
        "get_playbook": get_playbook,
        "browser_goto": browser_goto,
        "browser_snapshot": browser_snapshot,
        "browser_fill": browser_fill,
        "browser_select": browser_select,
        "browser_click": browser_click,
        "browser_screenshot": browser_screenshot,
        "handoff_to_user": handoff_to_user,
    }


def build_mcp_server(ctx: ToolContext) -> AgastyaMcpServerConfig:
    """Build in-process MCP server for Claude Agent SDK."""
    handlers = build_handlers(ctx)

    tools = [
        tool(
            "get_profile",
            "Retrieve all saved profile fields for the user with sensitive fields masked. Also lists missing fields.",
            GET_PROFILE_SCHEMA,
        )(handlers["get_profile"]),
        tool(
            "get_profile_value",
            "Retrieve the real unmasked value of a single profile field to fill form fields. Never speak unmasked values to the user.",
            GET_PROFILE_VALUE_SCHEMA,
        )(handlers["get_profile_value"]),
        tool(
            "save_profile_field",
            "Save or update a field value in the user profile after catalog validation. Emits profile_updated event.",
            SAVE_PROFILE_FIELD_SCHEMA,
        )(handlers["save_profile_field"]),
        tool(
            "ask_user",
            "Ask the user a question and block until they respond. If field is specified and answer is valid, saves it to profile.",
            ASK_USER_SCHEMA,
        )(handlers["ask_user"]),
        tool(
            "list_playbooks",
            "List all available portal playbooks (procedures, forms, URLs, and required fields).",
            LIST_PLAYBOOKS_SCHEMA,
        )(handlers["list_playbooks"]),
        tool(
            "get_playbook",
            "Get details and instructions for a specific portal playbook by id.",
            GET_PLAYBOOK_SCHEMA,
        )(handlers["get_playbook"]),
        tool(
            "browser_goto",
            "Navigate browser to the given URL. Emits screenshot event and returns page snapshot.",
            BROWSER_GOTO_SCHEMA,
        )(handlers["browser_goto"]),
        tool(
            "browser_snapshot",
            "Inspect the current browser page: returns URL, title, first 1500 chars of text, form fields, and actions.",
            BROWSER_SNAPSHOT_SCHEMA,
        )(handlers["browser_snapshot"]),
        tool(
            "browser_fill",
            "Fill a form input or textarea identified by its ref ('fN') with a value.",
            BROWSER_FILL_SCHEMA,
        )(handlers["browser_fill"]),
        tool(
            "browser_select",
            "Select an option in a dropdown or radio element identified by its ref ('fN').",
            BROWSER_SELECT_SCHEMA,
        )(handlers["browser_select"]),
        tool(
            "browser_click",
            "Click an element identified by its ref ('aN'). Final submit/pay/login/OTP actions are blocked and request handoff.",
            BROWSER_CLICK_SCHEMA,
        )(handlers["browser_click"]),
        tool(
            "browser_screenshot",
            "Capture a screenshot of the current page and broadcast screenshot event.",
            BROWSER_SCREENSHOT_SCHEMA,
        )(handlers["browser_screenshot"]),
        tool(
            "handoff_to_user",
            "Hand control back to the user when reaching a final/sensitive action or blocked action. Emits status event.",
            HANDOFF_TO_USER_SCHEMA,
        )(handlers["handoff_to_user"]),
    ]

    server_config = create_sdk_mcp_server(name="agastya", tools=tools)
    result = AgastyaMcpServerConfig(server_config)
    result.handlers = handlers
    return result


__all__ = [
    "BlockedActionError",
    "ToolContext",
    "TOOL_NAMES",
    "build_handlers",
    "build_mcp_server",
    "format_snapshot",
]
