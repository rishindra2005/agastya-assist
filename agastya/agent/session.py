"""Agent session loop and coordination for Agastya Assist."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from agastya.agent.events import EventBus
from agastya.agent.prompts import SYSTEM_PROMPT
from agastya.config import get_settings
from agastya.profile.store import ProfileStore

if TYPE_CHECKING:
    from agastya.browser.driver import BrowserDriver
    from agastya.portals import Playbook

logger = logging.getLogger(__name__)


class AgentSession:
    """Long-running agent session coordinating user input, Claude Agent SDK, and browser."""

    def __init__(
        self,
        user_id: str = "default",
        profile: ProfileStore | None = None,
        browser: BrowserDriver | None = None,
        bus: EventBus | None = None,
        client_factory: Callable[[ClaudeAgentOptions], Any] | None = None,
        playbooks: dict[str, Playbook] | None = None,
        ask_timeout: float = 600.0,
    ) -> None:
        settings = get_settings()
        self.user_id = user_id
        self.profile = profile if profile is not None else ProfileStore(user_id=user_id)
        self.browser = browser
        self.bus = bus if bus is not None else EventBus()
        self.client_factory = client_factory
        self.language_code = settings.DEFAULT_LANGUAGE
        self.ask_timeout = ask_timeout

        if playbooks is not None:
            self.playbooks = playbooks
        else:
            try:
                from agastya.portals import load_playbooks

                self.playbooks = load_playbooks()
            except Exception:
                self.playbooks = {}

        self.client: Any = None
        self._loop_task: asyncio.Task | None = None
        self._inbox: asyncio.Queue[str] = asyncio.Queue()
        self._pending_questions: dict[str, asyncio.Future[str]] = {}
        self._current_question_id: str | None = None
        self._stopped = False

    async def start(self) -> None:
        """Start browser, Claude SDK client, and the background run loop."""
        if self._loop_task is not None and not self._loop_task.done():
            return

        self._stopped = False

        if self.browser is None:
            try:
                from agastya.browser.driver import BrowserDriver

                self.browser = BrowserDriver()
            except ImportError:
                pass

        if self.browser is not None and hasattr(self.browser, "start") and callable(self.browser.start):
            await self.browser.start()

        settings = get_settings()

        from agastya.agent.tools import TOOL_NAMES, ToolContext, build_mcp_server

        ctx = ToolContext(
            profile=self.profile,
            browser=self.browser,
            bus=self.bus,
            ask_user=self.ask_user,
            playbooks=self.playbooks or {},
        )
        mcp_servers: dict[str, Any] = {"agastya": build_mcp_server(ctx)}
        allowed_tools: list[str] = list(TOOL_NAMES)

        options = ClaudeAgentOptions(
            model=settings.AGENT_MODEL,
            system_prompt=SYSTEM_PROMPT,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            tools=[],
            disallowed_tools=[
                "Bash",
                "Read",
                "Write",
                "Edit",
                "WebFetch",
                "WebSearch",
                "Glob",
                "Grep",
                "Task",
            ],
        )

        if self.client_factory is not None:
            self.client = self.client_factory(options)
        else:
            self.client = ClaudeSDKClient(options)

        if hasattr(self.client, "connect") and callable(self.client.connect):
            await self.client.connect()

        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Cancel run loop, disconnect client, and close browser."""
        self._stopped = True

        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._loop_task = None

        if self.client is not None:
            if hasattr(self.client, "disconnect") and callable(self.client.disconnect):
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            self.client = None

        if self.browser is not None:
            if hasattr(self.browser, "close") and callable(self.browser.close):
                try:
                    await self.browser.close()
                except Exception:
                    pass

        for fut in list(self._pending_questions.values()):
            if not fut.done():
                fut.cancel()
        self._pending_questions.clear()
        self._current_question_id = None

    async def send_user_message(self, text: str, language_code: str | None = None) -> None:
        """Enqueue user message or resolve a pending ask_user question."""
        if language_code:
            self.language_code = language_code

        if self._pending_questions:
            qid = self._current_question_id
            if not qid or qid not in self._pending_questions:
                qid = next(iter(self._pending_questions.keys()))
            fut = self._pending_questions[qid]
            if not fut.done():
                fut.set_result(text)
            return

        await self._inbox.put(text)

    async def answer_question(self, question_id: str, answer: str) -> None:
        """Resolve a pending question by its ID."""
        fut = self._pending_questions.get(question_id)
        if fut is not None and not fut.done():
            fut.set_result(answer)

    async def ask_user(self, question: str, field: str | None = None) -> str:
        """Ask user for missing information, awaiting response up to ask_timeout seconds."""
        question_id = uuid.uuid4().hex[:8]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_questions[question_id] = future
        self._current_question_id = question_id

        self.bus.emit(
            "ask_user",
            question_id=question_id,
            question=question,
            field=field,
            language_code=self.language_code,
        )

        try:
            answer = await asyncio.wait_for(future, timeout=self.ask_timeout)
            return answer
        except asyncio.TimeoutError:
            self.bus.emit("status", status="timeout", message="ask_user timed out")
            return ""
        finally:
            self._pending_questions.pop(question_id, None)
            if self._current_question_id == question_id:
                self._current_question_id = None

    async def _run_loop(self) -> None:
        """Long-running loop processing user messages."""
        while not self._stopped:
            try:
                text = await self._inbox.get()
            except asyncio.CancelledError:
                break

            try:
                self.bus.emit("status", status="thinking", message="thinking")
                prefix = f"[user_language={self.language_code}] "
                query_text = text if text.startswith("[user_language=") else f"{prefix}{text}"

                await self.client.query(query_text)

                done_emitted = False
                async for msg in self.client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in getattr(msg, "content", []):
                            if isinstance(block, TextBlock):
                                self.bus.emit(
                                    "assistant_text",
                                    text=block.text,
                                    language_code=self.language_code,
                                )
                            elif isinstance(block, ToolUseBlock):
                                self.bus.emit(
                                    "tool_call",
                                    id=block.id,
                                    name=block.name,
                                    input=block.input,
                                )
                    elif isinstance(msg, ResultMessage):
                        self.bus.emit(
                            "done",
                            subtype=getattr(msg, "subtype", "success"),
                            result=getattr(msg, "result", None),
                            is_error=getattr(msg, "is_error", False),
                        )
                        done_emitted = True

                if not done_emitted:
                    self.bus.emit("done", result=None)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error processing turn in AgentSession")
                self.bus.emit("error", error=str(exc), exc_type=type(exc).__name__)
            finally:
                self._inbox.task_done()
