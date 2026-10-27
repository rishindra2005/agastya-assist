"""Agastya Assist MCP tools for Claude Agent SDK."""

from __future__ import annotations

import base64
import dataclasses
from dataclasses import asdict, dataclass, field, is_dataclass
import json
import logging
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from agastya.agent.events import EventBus
from agastya.documents.store import DocumentStore
from agastya.profile.extract import extract_values
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


PAUSED_MESSAGE = "PAUSED: the citizen is controlling the browser. Wait."


@dataclass
class ToolContext:
    profile: ProfileStore
    browser: BrowserDriver
    bus: EventBus
    ask_user: Callable[..., Awaitable[str]]  # (question, fields) -> answer text
    playbooks: dict[str, Playbook]
    is_user_in_control: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    documents: DocumentStore | None = None


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
    "mcp__agastya__browser_fill_many",
    "mcp__agastya__browser_select",
    "mcp__agastya__browser_click",
    "mcp__agastya__browser_screenshot",
    "mcp__agastya__handoff_to_user",
    "mcp__agastya__list_documents",
    "mcp__agastya__read_document",
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
        "fields": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
            "description": "List of up to 4 profile field keys being asked for (e.g. ['full_name', 'father_name', 'date_of_birth']).",
        },
        "field": {
            "type": "string",
            "description": "Legacy parameter: single profile field name being asked for. If both provided, fields takes precedence.",
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

BROWSER_FILL_MANY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "The stable element reference from the snapshot (e.g. 'f1', 'f3').",
                    },
                    "value": {
                        "type": "string",
                        "description": "The value to fill, option to select, or boolean string for checkbox ('true'/'false'/'yes'/'no').",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["fill", "select", "check"],
                        "description": "Action kind: 'fill' (default), 'select', or 'check'.",
                    },
                },
                "required": ["ref", "value"],
                "additionalProperties": False,
            },
            "description": "List of form elements to fill or select sequentially in ONE tool call.",
        }
    },
    "required": ["items"],
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

LIST_DOCUMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

READ_DOCUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_id": {
            "type": "string",
            "description": "Unique identifier of the document to read.",
        }
    },
    "required": ["doc_id"],
    "additionalProperties": False,
}


def _get_document_store(ctx: ToolContext) -> DocumentStore:
    """Return DocumentStore on ctx, lazily creating it from profile.user_id if None."""
    if getattr(ctx, "documents", None) is None:
        user_id = getattr(ctx.profile, "user_id", "default")
        profile_db = getattr(ctx.profile, "db_path", None)
        data_dir = Path(profile_db).parent if profile_db is not None else None
        ctx.documents = DocumentStore(user_id=user_id, data_dir=data_dir)
    return ctx.documents


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


def _is_user_in_control(ctx: ToolContext) -> bool:
    """Return True if citizen is currently controlling the browser."""
    fn = getattr(ctx, "is_user_in_control", None)
    if fn is None:
        return False
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            return False
    return bool(fn)


async def _capture_screenshot(ctx: ToolContext) -> tuple[bytes, str]:
    """Capture screenshot as JPEG quality 55 via page.screenshot if supported, else fallback to driver.screenshot() PNG."""
    page = getattr(ctx.browser, "page", None)
    if page is not None and hasattr(page, "screenshot") and callable(page.screenshot):
        try:
            jpeg_bytes = await page.screenshot(type="jpeg", quality=55)
            return jpeg_bytes, "image/jpeg"
        except Exception:
            pass
    if hasattr(ctx.browser, "screenshot") and callable(ctx.browser.screenshot):
        png_bytes = await ctx.browser.screenshot()
        return png_bytes, "image/png"
    raise RuntimeError("Browser does not support screenshot capture.")


async def _emit_screenshot_throttled(ctx: ToolContext, url: str) -> None:
    """Emit screenshot event at most once every 2s, only after goto/click/fill_many."""
    now = time.monotonic()
    last = getattr(ctx, "_last_screenshot_time", 0.0)
    if now - last < 2.0:
        logger.debug("Screenshot skipped: throttled (%.2fs < 2.0s)", now - last)
        return
    ctx._last_screenshot_time = now
    try:
        img_bytes, mime = await _capture_screenshot(ctx)
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        ctx.bus.emit("screenshot", png_b64=b64, image_b64=b64, mime=mime, url=str(url or ""))
    except Exception as e:
        logger.warning(f"Failed to capture throttled screenshot: {e}")


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
        ctx.bus.emit("profile_updated", field=field, value=masked(field, saved_val or ""))
        return {
            "content": [{"type": "text", "text": f"Saved field '{field}' successfully."}],
        }

    async def ask_user(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        question = args.get("question", "")
        raw_fields = args.get("fields")
        raw_field = args.get("field")

        if raw_fields is not None:
            if isinstance(raw_fields, list):
                fields_list = [str(f) for f in raw_fields]
            else:
                fields_list = [str(raw_fields)]
        elif raw_field is not None:
            fields_list = [str(raw_field)]
        else:
            fields_list = []

        # Validate keys exist in FIELDS catalog
        unknown_keys = [f for f in fields_list if f not in FIELDS]
        if unknown_keys:
            valid_keys = ", ".join(sorted(FIELDS.keys()))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Unknown field key(s): {', '.join(unknown_keys)}. Valid keys are: {valid_keys}",
                    }
                ],
                "is_error": True,
            }

        try:
            try:
                answer = await ctx.ask_user(question, fields_list)
            except TypeError:
                legacy_arg = fields_list[0] if fields_list else None
                try:
                    answer = await ctx.ask_user(question, legacy_arg)
                except TypeError:
                    answer = await ctx.ask_user(question)
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error asking user: {e}"}],
                "is_error": True,
            }

        saved: dict[str, str] = {}
        errors: dict[str, str] = {}
        unparsed: list[str] = []

        if len(fields_list) == 1:
            f = fields_list[0]
            extracted = extract_values(answer, [f])
            val_to_save = extracted.get(f)
            if val_to_save is None and FIELDS.get(f, {}).get("validator"):
                # Pattern fields: the validator rejects anything that isn't a real value.
                val_to_save = str(answer).strip()
            try:
                if val_to_save is None:
                    # Free-text field ("skip karo", "baad me"…): let the agent interpret the answer.
                    raise ValueError("free-text answer; agent decides whether to save")
                ctx.profile.set(f, val_to_save)
                stored_val = ctx.profile.get(f)
                saved[f] = masked(f, stored_val if stored_val is not None else val_to_save)
                ctx.bus.emit("profile_updated", field=f, value=masked(f, stored_val or ""))
            except ValueError as e:
                errors[f] = str(e)
                unparsed.append(f)
            except Exception as e:
                errors[f] = str(e)
                unparsed.append(f)
        elif len(fields_list) > 1:
            extracted = extract_values(answer, fields_list)
            for f, val in extracted.items():
                try:
                    ctx.profile.set(f, val)
                    stored_val = ctx.profile.get(f)
                    saved[f] = masked(f, stored_val if stored_val is not None else val)
                    ctx.bus.emit("profile_updated", field=f, value=masked(f, stored_val or ""))
                except ValueError as e:
                    errors[f] = str(e)
                except Exception as e:
                    errors[f] = str(e)
            unparsed = [f for f in fields_list if f not in saved]

        response_payload = {
            "answer": str(answer),
            "saved": saved,
            "unparsed": unparsed,
            "errors": errors,
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(response_payload, ensure_ascii=False),
                }
            ],
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
        if _is_user_in_control(ctx):
            return {"content": [{"type": "text", "text": PAUSED_MESSAGE}]}
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

        # Page-changing browser action automatically emits screenshot event (throttled)
        current_url = getattr(snapshot, "url", url)
        await _emit_screenshot_throttled(ctx, str(current_url or ""))

        return {
            "content": [{"type": "text", "text": format_snapshot(snapshot)}],
        }

    async def browser_snapshot(args: dict[str, Any] | None = None) -> dict[str, Any]:
        if _is_user_in_control(ctx):
            return {"content": [{"type": "text", "text": PAUSED_MESSAGE}]}
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
        if _is_user_in_control(ctx):
            return {"content": [{"type": "text", "text": PAUSED_MESSAGE}]}
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

    async def browser_fill_many(args: dict[str, Any] | None = None) -> dict[str, Any]:
        if _is_user_in_control(ctx):
            return {"content": [{"type": "text", "text": PAUSED_MESSAGE}]}
        args = args or {}
        items = args.get("items", [])
        if not isinstance(items, list):
            return {
                "content": [{"type": "text", "text": "Argument 'items' must be a list."}],
                "is_error": True,
            }

        results: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                results.append({"ref": "unknown", "ok": False, "error": "Item must be a dictionary"})
                continue
            ref = str(item.get("ref", "")).strip()
            val = str(item.get("value", ""))
            kind = str(item.get("kind", "fill")).lower().strip()

            if not ref:
                results.append({"ref": "", "ok": False, "error": "Missing 'ref'"})
                continue

            try:
                if kind == "select":
                    await ctx.browser.select(ref, val)
                elif kind == "check":
                    if hasattr(ctx.browser, "check") and callable(getattr(ctx.browser, "check")):
                        await ctx.browser.check(ref, val)
                    else:
                        await ctx.browser.fill(ref, val)
                else:  # "fill" (default)
                    await ctx.browser.fill(ref, val)
                results.append({"ref": ref, "ok": True, "value": val, "kind": kind})
            except Exception as e:
                results.append({"ref": ref, "ok": False, "error": str(e), "kind": kind})

        url = getattr(ctx.browser, "url", "")
        if callable(url):
            url = url()
        await _emit_screenshot_throttled(ctx, str(url or ""))

        ok_count = sum(1 for r in results if r["ok"])
        total_count = len(results)
        summary = f"Processed {total_count} items: {ok_count} succeeded, {total_count - ok_count} failed."

        payload = {
            "summary": summary,
            "results": results,
            "ok_count": ok_count,
            "error_count": total_count - ok_count,
        }
        return {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}],
        }

    async def browser_select(args: dict[str, Any] | None = None) -> dict[str, Any]:
        if _is_user_in_control(ctx):
            return {"content": [{"type": "text", "text": PAUSED_MESSAGE}]}
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
        if _is_user_in_control(ctx):
            return {"content": [{"type": "text", "text": PAUSED_MESSAGE}]}
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

        # Page-changing browser action automatically emits screenshot event (throttled)
        current_url = getattr(snapshot, "url", "")
        await _emit_screenshot_throttled(ctx, str(current_url or ""))

        return {
            "content": [{"type": "text", "text": format_snapshot(snapshot)}],
        }

    async def browser_screenshot(args: dict[str, Any] | None = None) -> dict[str, Any]:
        if _is_user_in_control(ctx):
            return {"content": [{"type": "text", "text": PAUSED_MESSAGE}]}
        try:
            img_bytes, mime = await _capture_screenshot(ctx)
            ctx._last_screenshot_time = time.monotonic()
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            url = getattr(ctx.browser, "url", "")
            if callable(url):
                url = url()
            ctx.bus.emit("screenshot", png_b64=b64, image_b64=b64, mime=mime, url=str(url or ""))
            return {
                "content": [{"type": "text", "text": f"Screenshot captured ({len(img_bytes)} bytes)."}],
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

    async def list_documents(args: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            store = _get_document_store(ctx)
            docs = store.list()
            items = [
                {
                    "id": doc.id,
                    "filename": doc.filename,
                    "kind": doc.kind,
                    "uploaded_at": doc.uploaded_at,
                }
                for doc in docs
            ]
            return {
                "content": [{"type": "text", "text": json.dumps(items, indent=2)}],
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error listing documents: {e}"}],
                "is_error": True,
            }

    async def read_document(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        doc_id = args.get("doc_id")
        if not doc_id:
            return {
                "content": [{"type": "text", "text": "Missing required argument: doc_id"}],
                "is_error": True,
            }
        try:
            store = _get_document_store(ctx)
            doc = store.get(str(doc_id))
            if doc is None:
                return {
                    "content": [{"type": "text", "text": f"Document not found: {doc_id}"}],
                    "is_error": True,
                }
            import copy
            extracted = copy.deepcopy(doc.extracted) if doc.extracted else {}
            if "employee_pan" in extracted and extracted["employee_pan"]:
                extracted["employee_pan"] = masked("pan", extracted["employee_pan"])
            return {
                "content": [{"type": "text", "text": json.dumps(extracted, indent=2)}],
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error reading document: {e}"}],
                "is_error": True,
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
        "browser_fill_many": browser_fill_many,
        "browser_select": browser_select,
        "browser_click": browser_click,
        "browser_screenshot": browser_screenshot,
        "handoff_to_user": handoff_to_user,
        "list_documents": list_documents,
        "read_document": read_document,
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
            "Ask the user a question for 1–3 profile fields and block until they respond. Automatically extracts and validates known field patterns and saves them to the profile.",
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
            "browser_fill_many",
            "Fill or select multiple form fields in ONE tool call. Items: [{ref, value, kind?: 'fill'|'select'|'check'}]. Returns per-item ok/error status.",
            BROWSER_FILL_MANY_SCHEMA,
        )(handlers["browser_fill_many"]),
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
        tool(
            "list_documents",
            "List all uploaded user documents with id, filename, kind, and uploaded_at timestamp.",
            LIST_DOCUMENTS_SCHEMA,
        )(handlers["list_documents"]),
        tool(
            "read_document",
            "Read extracted structured data from an uploaded document by doc_id (sensitive fields like employee_pan are masked).",
            READ_DOCUMENT_SCHEMA,
        )(handlers["read_document"]),
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
