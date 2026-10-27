"""Streamable-HTTP MCP server exposing Agastya agent tools."""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
from typing import TYPE_CHECKING, Any, Callable

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.tools.base import Tool
from mcp.server.mcpserver.utilities.func_metadata import func_metadata
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from agastya.agent.hooks import is_browser_tool
from agastya.browser.guard import check_click, check_navigation

if TYPE_CHECKING:
    from agastya.agent.session import AgentSession
    from agastya.agent.tools import ToolContext

logger = logging.getLogger(__name__)

TOOL_DESCRIPTIONS: dict[str, str] = {
    "get_profile": "Retrieve all saved profile fields for the user with sensitive fields masked. Also lists missing fields.",
    "get_profile_value": "Retrieve the real unmasked value of a single profile field to fill form fields. Never speak unmasked values to the user.",
    "save_profile_field": "Save or update a field value in the user profile after catalog validation. Emits profile_updated event.",
    "ask_user": "Ask the user a question for 1–3 profile fields and block until they respond. Automatically extracts and validates known field patterns and saves them to the profile.",
    "list_playbooks": "List all available portal playbooks (procedures, forms, URLs, and required fields).",
    "get_playbook": "Get details and instructions for a specific portal playbook by id.",
    "browser_goto": "Navigate browser to the given URL. Emits screenshot event and returns page snapshot.",
    "browser_snapshot": "Inspect the current browser page: returns URL, title, first 1500 chars of text, form fields, and actions.",
    "browser_fill": "Fill a form input or textarea identified by its ref ('fN') with a value.",
    "browser_fill_many": "Fill or select multiple form fields in ONE tool call. Items: [{ref, value, kind?: 'fill'|'select'|'check'}]. Returns per-item ok/error status.",
    "browser_select": "Select an option in a dropdown or radio element identified by its ref ('fN').",
    "browser_click": "Click an element identified by its ref ('aN'). Final submit/pay/login/OTP actions are blocked and request handoff.",
    "browser_screenshot": "Capture a screenshot of the current page and broadcast screenshot event.",
    "handoff_to_user": "Hand control back to the user when reaching a final/sensitive action or blocked action. Emits status event.",
    "list_documents": "List all uploaded documents for the user.",
    "read_document": "Read and parse an uploaded document by id.",
}


class BearerAuthMiddleware:
    """ASGI middleware enforcing Bearer token authentication."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode("utf-8").strip()
            expected = f"Bearer {self.token}"
            if not auth_header or auth_header != expected:
                resp = Response("Unauthorized", status_code=401)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


class CustomSchemaTool(Tool):
    """MCP Tool subclass supporting pre-defined arbitrary JSON Schema."""

    async def run(
        self,
        arguments: dict[str, Any],
        context: Any,
        convert_result: bool = False,
    ) -> Any:
        from mcp.types import CallToolResult, TextContent

        res = await self.fn(arguments)
        if isinstance(res, dict):
            raw_content = res.get("content", [])
            contents = []
            for item in raw_content:
                if isinstance(item, dict) and item.get("type") == "text":
                    contents.append(TextContent(type="text", text=str(item.get("text", ""))))
                elif isinstance(item, TextContent):
                    contents.append(item)
                elif isinstance(item, str):
                    contents.append(TextContent(type="text", text=item))
            is_err = bool(res.get("is_error", False))
            return CallToolResult(content=contents, is_error=is_err, result_type="complete")
        return res


def _get_blocked_reason(exc: BaseException) -> str:
    """Extract reason string from a BlockedActionError or generic exception."""
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


def wrap_tool_with_safety(
    name: str,
    handler: Callable[[dict[str, Any] | None], Any],
    ctx: ToolContext,
    session: AgentSession | None = None,
) -> Callable[[dict[str, Any] | None], Any]:
    """Wrap raw tool handler with safety guard and citizen-in-control checks from hooks.py."""

    async def guarded_handler(args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}

        # 1. Human control check: citizen is controlling the browser
        user_in_control = False
        if session is not None and getattr(session, "user_in_control", False):
            user_in_control = True
        elif hasattr(ctx, "is_user_in_control") and callable(ctx.is_user_in_control):
            try:
                user_in_control = bool(ctx.is_user_in_control())
            except Exception:
                user_in_control = False

        if is_browser_tool(name) and user_in_control:
            if ctx.bus is not None and hasattr(ctx.bus, "emit"):
                ctx.bus.emit("blocked", reason="user_in_control", tool=name)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "PAUSED: the citizen is controlling the browser. Wait for the user to release control.",
                    }
                ],
                "is_error": True,
            }

        # 2. Browser navigation safety check
        if name in ("browser_goto", "browser_navigate"):
            url = str(args.get("url") or args.get("href") or "")
            decision = check_navigation(url)
            if not decision.allowed:
                if ctx.bus is not None and hasattr(ctx.bus, "emit"):
                    ctx.bus.emit("blocked", reason=decision.reason, tool=name)
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Navigation blocked by safety guard ({decision.reason}). Call handoff_to_user to let the user proceed.",
                        }
                    ],
                    "is_error": False,
                }

        # 3. Browser click safety check (if element info provided)
        if name == "browser_click":
            element_text = (
                args.get("element")
                or args.get("text")
                or args.get("selector")
                or ""
            )
            if element_text or args.get("tag") or args.get("id") or args.get("name"):
                element_dict = {
                    "tag": str(args.get("tag", "")),
                    "type": str(args.get("type", "")),
                    "text": str(element_text),
                    "id": str(args.get("id", "")),
                    "name": str(args.get("name", "")),
                    "aria_label": str(args.get("aria_label") or args.get("aria-label") or ""),
                    "value": str(args.get("value", "")),
                    "href": str(args.get("href", "")),
                }
                decision = check_click(element_dict)
                if not decision.allowed:
                    if ctx.bus is not None and hasattr(ctx.bus, "emit"):
                        ctx.bus.emit("blocked", reason=decision.reason, tool=name)
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Click blocked by safety guard ({decision.reason}). Call handoff_to_user to let the user proceed.",
                            }
                        ],
                        "is_error": False,
                    }

        # 4. Invoke underlying handler and catch any BlockedActionError from driver
        try:
            return await handler(args)
        except Exception as exc:
            # Check if this is a BlockedActionError
            is_blocked = type(exc).__name__ == "BlockedActionError"
            if is_blocked:
                reason = _get_blocked_reason(exc)
                if ctx.bus is not None and hasattr(ctx.bus, "emit"):
                    ref = args.get("ref", "")
                    ctx.bus.emit("blocked", reason=reason, ref=ref, tool=name)
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"BLOCKED: {reason}. This is a final action. Tell the user what is filled and call handoff_to_user.",
                        }
                    ],
                    "is_error": False,
                }
            logger.exception("Error executing tool %s", name)
            return {
                "content": [{"type": "text", "text": f"Error executing tool {name}: {exc}"}],
                "is_error": True,
            }

    return guarded_handler


class McpHttpServer:
    """Streamable-HTTP MCP server running on 127.0.0.1 with Bearer token authentication."""

    def __init__(
        self,
        ctx: ToolContext,
        session: AgentSession | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.session = session
        self.host = host
        self.port = port
        self.token = token or secrets.token_hex(32)

        self._server_task: asyncio.Task[None] | None = None
        self._uvicorn_server: uvicorn.Server | None = None
        self._socket: socket.socket | None = None
        self._running = False

    @property
    def endpoint_url(self) -> str:
        """Full Streamable HTTP endpoint URL."""
        return f"http://{self.host}:{self.port}/mcp"

    def _build_app(self) -> ASGIApp:
        """Construct the MCPServer instance and wrap it in BearerAuthMiddleware."""
        import agastya.agent.tools as tools_mod

        handlers = tools_mod.build_handlers(self.ctx)
        server = MCPServer(name="agastya")
        dummy_meta = func_metadata(lambda: None)

        for tool_name, raw_handler in handlers.items():
            schema_attr = f"{tool_name.upper()}_SCHEMA"
            schema = getattr(tools_mod, schema_attr, {"type": "object", "properties": {}})
            desc = TOOL_DESCRIPTIONS.get(tool_name, getattr(raw_handler, "__doc__", "") or f"Agastya tool {tool_name}")

            guarded = wrap_tool_with_safety(tool_name, raw_handler, self.ctx, self.session)
            custom_tool = CustomSchemaTool(
                fn=guarded,
                name=tool_name,
                description=desc,
                parameters=schema,
                fn_metadata=dummy_meta,
                is_async=True,
            )
            server._tool_manager._tools[tool_name] = custom_tool

        starlette_app = server.streamable_http_app(streamable_http_path="/mcp")
        return BearerAuthMiddleware(starlette_app, self.token)

    async def start(self) -> None:
        """Allocate a socket, bind host/port, and start uvicorn in a background task."""
        if self._running:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(128)
        sock.setblocking(False)

        self.port = sock.getsockname()[1]
        self._socket = sock

        app = self._build_app()
        config = uvicorn.Config(
            app=app,
            log_level="warning",
            access_log=False,
            lifespan="on",
        )
        self._uvicorn_server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(
            self._uvicorn_server.serve(sockets=[sock])
        )

        # Wait until server is up and responsive
        for _ in range(50):
            if self._uvicorn_server.started:
                break
            await asyncio.sleep(0.05)

        self._running = True
        logger.info("McpHttpServer started at %s", self.endpoint_url)

    async def stop(self) -> None:
        """Signal uvicorn to shut down and wait for background task completion."""
        self._running = False
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True

        if self._server_task is not None and not self._server_task.done():
            try:
                await asyncio.wait_for(self._server_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._server_task.cancel()
            self._server_task = None

        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        self._uvicorn_server = None
        logger.info("McpHttpServer stopped.")
