"""Safety and execution hooks for Claude Agent SDK."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import HookMatcher

from agastya.browser.guard import check_click, check_navigation

if TYPE_CHECKING:
    from agastya.agent.session import AgentSession

logger = logging.getLogger(__name__)

# Base names for disallowed playwright tools (without mcp__playwright__ prefix)
PLAYWRIGHT_DISALLOWED_BASE_NAMES: set[str] = {
    "browser_evaluate",
    "browser_run_code",
    "browser_file_upload",
    "browser_install",
    "browser_close",
    "browser_resize",
    "browser_console_messages",
    "browser_network_requests",
    "browser_drag",
}

# Full names for disallowed playwright tools
PLAYWRIGHT_DISALLOWED_TOOLS: list[str] = [
    f"mcp__playwright__{name}" for name in PLAYWRIGHT_DISALLOWED_BASE_NAMES
]

# Allowed playwright tools
PLAYWRIGHT_ALLOWED_TOOLS: list[str] = [
    "mcp__playwright__browser_snapshot",
    "mcp__playwright__browser_click",
    "mcp__playwright__browser_type",
    "mcp__playwright__browser_fill_form",
    "mcp__playwright__browser_select_option",
    "mcp__playwright__browser_hover",
    "mcp__playwright__browser_press_key",
    "mcp__playwright__browser_wait_for",
    "mcp__playwright__browser_navigate",
    "mcp__playwright__browser_navigate_back",
    "mcp__playwright__browser_tabs",
    "mcp__playwright__browser_take_screenshot",
]


def is_browser_tool(tool_name: str) -> bool:
    """Check if the tool is a browser automation tool (agastya or playwright)."""
    return (
        tool_name.startswith("mcp__playwright__")
        or tool_name.startswith("mcp__agastya__browser_")
        or tool_name.startswith("browser_")
    )


def extract_tool_info(input_data: Any) -> tuple[str, dict[str, Any]]:
    """Extract tool_name and tool_input from various input representations."""
    if isinstance(input_data, dict):
        tool_name = (
            input_data.get("tool_name")
            or input_data.get("name")
            or input_data.get("tool")
            or ""
        )
        tool_input = (
            input_data.get("tool_input")
            or input_data.get("input")
            or input_data.get("arguments")
            or {}
        )
    else:
        tool_name = (
            getattr(input_data, "tool_name", None)
            or getattr(input_data, "name", None)
            or getattr(input_data, "tool", None)
            or ""
        )
        tool_input = (
            getattr(input_data, "tool_input", None)
            or getattr(input_data, "input", None)
            or getattr(input_data, "arguments", None)
            or {}
        )
    if not isinstance(tool_input, dict):
        tool_input = {}
    return str(tool_name), tool_input


def make_pre_tool_use_hook(session: Any):
    """Create PreToolUse hook callback bound to the given agent session."""

    async def pre_tool_use_hook(
        input_data: Any,
        tool_use_id: str | None = None,
        context: Any = None,
    ) -> dict[str, Any]:
        tool_name, tool_input = extract_tool_info(input_data)

        # 1. Human control check: deny ANY browser tool if citizen is controlling the browser
        if is_browser_tool(tool_name) and getattr(session, "user_in_control", False):
            reason = "user_in_control"
            if hasattr(session, "bus") and session.bus is not None and hasattr(session.bus, "emit"):
                session.bus.emit("blocked", reason=reason, tool=tool_name)
            return {
                "decision": "block",
                "reason": "PAUSED: the citizen is controlling the browser. Wait.",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "PAUSED: the citizen is controlling the browser. Wait for the user to release control.",
                },
            }

        # Normalize base tool name
        base_name = tool_name
        if base_name.startswith("mcp__playwright__"):
            base_name = base_name[len("mcp__playwright__"):]
        elif base_name.startswith("mcp__agastya__"):
            base_name = base_name[len("mcp__agastya__"):]

        # 2. Check explicitly disallowed Playwright tools
        # Allow-list: any Playwright tool not explicitly approved is denied (tool names change between
        # MCP versions, e.g. browser_run_code -> browser_run_code_unsafe).
        if (
            base_name in PLAYWRIGHT_DISALLOWED_BASE_NAMES
            or tool_name in PLAYWRIGHT_DISALLOWED_TOOLS
            or (tool_name.startswith("mcp__playwright__") and tool_name not in PLAYWRIGHT_ALLOWED_TOOLS)
        ):
            reason = f"{base_name} is disallowed"
            if hasattr(session, "bus") and session.bus is not None and hasattr(session.bus, "emit"):
                session.bus.emit("blocked", reason=reason, tool=tool_name)
            return {
                "decision": "block",
                "reason": f"Tool '{tool_name}' is disallowed for safety. Call handoff_to_user.",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Tool '{tool_name}' is disallowed for safety. Call handoff_to_user to allow the user to complete this action.",
                },
            }

        # 3. Check browser click safety (guard against final submit, pay, login, OTP, etc.)
        if base_name == "browser_click":
            element_text = (
                tool_input.get("element")
                or tool_input.get("text")
                or tool_input.get("selector")
                or ""
            )
            element_dict = {
                "tag": str(tool_input.get("tag", "")),
                "type": str(tool_input.get("type", "")),
                "text": str(element_text),
                "id": str(tool_input.get("id", "")),
                "name": str(tool_input.get("name", "")),
                "aria_label": str(tool_input.get("aria_label") or tool_input.get("aria-label") or ""),
                "value": str(tool_input.get("value", "")),
                "href": str(tool_input.get("href", "")),
            }
            decision = check_click(element_dict)
            if not decision.allowed:
                reason = decision.reason
                if hasattr(session, "bus") and session.bus is not None and hasattr(session.bus, "emit"):
                    session.bus.emit("blocked", reason=reason, tool=tool_name)
                return {
                    "decision": "block",
                    "reason": f"Click blocked by safety guard ({reason}). Call handoff_to_user.",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Click blocked by safety guard ({reason}). Call handoff_to_user to let the user proceed.",
                    },
                }

        # 4. Check navigation safety (block payment gateways and logout URLs)
        if base_name in ("browser_navigate", "browser_goto"):
            url = str(tool_input.get("url") or tool_input.get("href") or "")
            decision = check_navigation(url)
            if not decision.allowed:
                reason = decision.reason
                if hasattr(session, "bus") and session.bus is not None and hasattr(session.bus, "emit"):
                    session.bus.emit("blocked", reason=reason, tool=tool_name)
                return {
                    "decision": "block",
                    "reason": f"Navigation blocked by safety guard ({reason}). Call handoff_to_user.",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Navigation blocked by safety guard ({reason}). Call handoff_to_user to let the user proceed.",
                    },
                }

        # 5. Check press_key "Enter" safety
        if base_name == "browser_press_key":
            key = str(tool_input.get("key", "")).strip()
            if key.lower() == "enter":
                element_str = str(tool_input.get("element", "")).lower()
                is_search = any(kw in element_str for kw in ("search", "find", "khoj", "query", "filter"))
                if not is_search:
                    reason = "enter outside search box"
                    if hasattr(session, "bus") and session.bus is not None and hasattr(session.bus, "emit"):
                        session.bus.emit("blocked", reason=reason, tool=tool_name)
                    return {
                        "decision": "block",
                        "reason": "Pressing Enter outside of a search box is blocked. Call handoff_to_user.",
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "Pressing Enter outside of a search box is blocked to avoid accidental submission. Call handoff_to_user.",
                        },
                    }

        # 6. browser_type with submit=true presses Enter after typing -> same rule as press_key Enter
        if base_name == "browser_type" and tool_input.get("submit") in (True, "true", "True"):
            reason = "type with submit"
            if hasattr(session, "bus") and session.bus is not None and hasattr(session.bus, "emit"):
                session.bus.emit("blocked", reason=reason, tool=tool_name)
            return {
                "decision": "block",
                "reason": "Typing with submit=true is blocked. Type without submit, then call handoff_to_user.",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "browser_type with submit=true can submit the form. Type without submit; the citizen submits.",
                },
            }

        # Default: tool execution allowed
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }

    return pre_tool_use_hook


def build_hooks(session: Any) -> dict[str, list[HookMatcher]]:
    """Build Claude Agent SDK hooks mapping for AgentSession."""
    hook_cb = make_pre_tool_use_hook(session)
    return {
        "PreToolUse": [
            HookMatcher(matcher="mcp__playwright__.*", hooks=[hook_cb]),
            HookMatcher(matcher="mcp__agastya__browser_.*", hooks=[hook_cb]),
        ]
    }
