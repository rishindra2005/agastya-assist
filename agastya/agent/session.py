"""Agent session loop and coordination for Agastya Assist."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable

from agastya.agent.events import EventBus
from agastya.config import get_settings
from agastya.profile.store import ProfileStore

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions

    from agastya.agent.backends.base import AgentBackend
    from agastya.browser.driver import BrowserDriver
    from agastya.portals import Playbook

logger = logging.getLogger(__name__)

_INDIC_SCRIPT = re.compile(r"[\u0900-\u0DFF]")


def is_wrong_script(text: str, language_code: str) -> bool:
    """True for Latin-only text in a non-English session (agent working notes the TTS can't read)."""
    if not language_code or language_code.startswith("en"):
        return False
    return bool(re.search(r"[A-Za-z]{3}", text)) and not _INDIC_SCRIPT.search(text)


class AgentSession:
    """Long-running agent session coordinating user input, backend brain, and browser."""

    def __init__(
        self,
        user_id: str = "default",
        profile: ProfileStore | None = None,
        browser: BrowserDriver | None = None,
        bus: EventBus | None = None,
        client_factory: Callable[[ClaudeAgentOptions], Any] | None = None,
        playbooks: dict[str, Playbook] | None = None,
        ask_timeout: float = 600.0,
        backend: AgentBackend | None = None,
    ) -> None:
        settings = get_settings()
        self.user_id = user_id
        self.profile = profile if profile is not None else ProfileStore(user_id=user_id)
        self.browser = browser
        self.bus = bus if bus is not None else EventBus()
        self.client_factory = client_factory
        self.backend = backend
        self.language_code = settings.DEFAULT_LANGUAGE
        self.ask_timeout = ask_timeout
        self.user_in_control = False

        if playbooks is not None:
            self.playbooks = playbooks
        else:
            try:
                from agastya.portals import load_playbooks

                self.playbooks = load_playbooks()
            except Exception:
                self.playbooks = {}

        self.client: Any = None
        self._loop_task: asyncio.Task[None] | None = None
        self._inbox: asyncio.Queue[str] = asyncio.Queue()
        self._pending_questions: dict[str, asyncio.Future[str]] = {}
        self._current_question_id: str | None = None
        self._stopped = False

    async def _set_agent_active(self, active: bool) -> None:
        """Toggle in-page agent active flag via page_guard."""
        try:
            from agastya.browser.page_guard import set_agent_active

            context = getattr(self.browser, "context", None)
            if context is not None:
                await set_agent_active(context, active)
            elif hasattr(self.browser, "set_agent_active") and callable(self.browser.set_agent_active):
                await self.browser.set_agent_active(active)
        except Exception as exc:
            logger.debug("set_agent_active fallback/noop: %s", exc)

    async def take_control(self) -> bool:
        """Citizen takes control of the browser. Sets agent inactive and pauses agent actions."""
        self.user_in_control = True
        await self._set_agent_active(False)
        self.bus.emit("status", status="user_control")
        return True

    async def release_control(self, note: str = "") -> bool:
        """Citizen releases control of the browser. Notifies agent to continue."""
        self.user_in_control = False
        clean_note = note.strip()
        if clean_note:
            msg = f"[user finished on the page: {clean_note}] Continue from the current page."
        else:
            msg = "[user finished on the page] Continue from the current page."
        await self._inbox.put(msg)
        return False

    async def start(self) -> None:
        """Start browser, agent backend, and the background run loop."""
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

        if self.backend is None:
            # Pick backend: client_factory implies Claude SDK mock; otherwise use AGENT_BACKEND setting
            chosen_backend = getattr(settings, "AGENT_BACKEND", "claude")
            if self.client_factory is not None or chosen_backend == "claude":
                from agastya.agent.backends.claude_backend import ClaudeBackend

                self.backend = ClaudeBackend(session=self, client_factory=self.client_factory)
            elif chosen_backend == "agy":
                from agastya.agent.backends.agy_backend import AgyBackend

                self.backend = AgyBackend(session=self)
            else:
                from agastya.agent.backends.claude_backend import ClaudeBackend

                self.backend = ClaudeBackend(session=self, client_factory=self.client_factory)

        await self.backend.start()
        self.client = getattr(self.backend, "client", None)

        self._loop_task = asyncio.create_task(self._run_loop())
        self.bus.emit("status", status="ready", message="ready")

    async def stop(self) -> None:
        """Cancel run loop, stop backend, and close browser."""
        self._stopped = True

        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._loop_task = None

        if self.backend is not None:
            try:
                await self.backend.stop()
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

    async def ask_user(
        self,
        question: str,
        fields: list[str] | str | None = None,
        field: str | None = None,
    ) -> str:
        """Ask user for missing information, awaiting response up to ask_timeout seconds."""
        if isinstance(fields, str):
            fields_list = [fields]
            field_first: str | None = fields
        elif isinstance(fields, list):
            fields_list = list(fields)
            field_first = fields_list[0] if fields_list else None
        elif fields is None and field is not None:
            fields_list = [field]
            field_first = field
        else:
            fields_list = []
            field_first = None

        question_id = uuid.uuid4().hex[:8]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_questions[question_id] = future
        self._current_question_id = question_id

        self.bus.emit(
            "ask_user",
            question_id=question_id,
            question=question,
            field=field_first,
            fields=fields_list,
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

                turn_start = time.time()
                first_text_s: float | None = None
                tools_count = 0

                await self._set_agent_active(True)
                try:
                    done_emitted = False
                    async for event_type, payload in self.backend.run_turn(query_text):
                        if event_type == "text":
                            if first_text_s is None:
                                first_text_s = round(time.time() - turn_start, 3)
                            if is_wrong_script(payload, self.language_code):
                                # English working notes in a non-English session: show, don't speak
                                self.bus.emit("status", status="note", message=payload)
                            else:
                                self.bus.emit(
                                    "assistant_text",
                                    text=payload,
                                    language_code=self.language_code,
                                )
                        elif event_type == "tool_call":
                            tools_count += 1
                            self.bus.emit(
                                "tool_call",
                                id=payload.get("id"),
                                name=payload.get("name"),
                                input=payload.get("input"),
                            )
                        elif event_type == "done":
                            self.bus.emit(
                                "done",
                                subtype=payload.get("subtype", "success"),
                                result=payload.get("result"),
                                is_error=payload.get("is_error", False),
                            )
                            done_emitted = True

                    if not done_emitted:
                        self.bus.emit("done", result=None)

                    turn_s = round(time.time() - turn_start, 3)
                    self.bus.emit(
                        "status",
                        status="metrics",
                        first_text_s=first_text_s,
                        turn_s=turn_s,
                        tools=tools_count,
                    )
                finally:
                    await self._set_agent_active(False)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error processing turn in AgentSession")
                self.bus.emit("error", error=str(exc), exc_type=type(exc).__name__)
            finally:
                self._inbox.task_done()
