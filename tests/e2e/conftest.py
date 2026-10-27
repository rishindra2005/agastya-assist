"""Pytest configuration and fixtures for end-to-end tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
import time
from typing import Any
import pytest

from agastya.agent.events import AgentEvent
from agastya.agent.session import AgentSession
from agastya.profile.store import ProfileStore

FORMS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "forms"


@pytest.fixture
def forms_dir() -> Path:
    """Path to local HTML form fixtures."""
    return FORMS_DIR


@pytest.fixture
def pan_fixture_uri() -> str:
    """File URI for pan_49a.html form fixture."""
    return (FORMS_DIR / "pan_49a.html").resolve().as_uri()


@pytest.fixture
def voter_fixture_uri() -> str:
    """File URI for voter_form6.html form fixture."""
    return (FORMS_DIR / "voter_form6.html").resolve().as_uri()


@pytest.fixture
def itr_fixture_uri() -> str:
    """File URI for itr_login_and_form.html form fixture."""
    return (FORMS_DIR / "itr_login_and_form.html").resolve().as_uri()


@pytest.fixture
def mca_fixture_uri() -> str:
    """File URI for mca_din.html form fixture."""
    return (FORMS_DIR / "mca_din.html").resolve().as_uri()


@pytest.fixture
def form_success_uri() -> str:
    """File URI for form_success.html."""
    return (FORMS_DIR / "form_success.html").resolve().as_uri()


@pytest.fixture
def tmp_profile(tmp_path: Path) -> ProfileStore:
    """Isolated ProfileStore using a temporary SQLite database."""
    db_file = tmp_path / "test_e2e_profile.db"
    if db_file.exists():
        db_file.unlink()
    return ProfileStore(db_path=db_file)


async def collect_session_events(
    session: AgentSession,
    user_message: str,
    language_code: str = "hi-IN",
    timeout: float = 300.0,
    answer_callback: Callable[[AgentEvent, AgentSession], Any] | None = None,
) -> list[AgentEvent]:
    """Send user message to AgentSession and collect all events until completion or timeout."""
    event_queue = session.bus.subscribe()
    await session.send_user_message(user_message, language_code)

    events: list[AgentEvent] = []
    start_time = time.time()

    while time.time() - start_time < timeout:
        remaining = timeout - (time.time() - start_time)
        if remaining <= 0:
            break
        try:
            ev = await asyncio.wait_for(event_queue.get(), timeout=min(remaining, 5.0))
        except asyncio.TimeoutError:
            # Check if session loop has finished
            if session._loop_task is not None and session._loop_task.done():
                break
            continue

        events.append(ev)

        if ev.type == "ask_user":
            if answer_callback is not None:
                ans = answer_callback(ev, session)
                if asyncio.iscoroutine(ans):
                    await ans
            else:
                # Default auto-answer
                d = ev.data
                qid = d.get("question_id", "")
                field = d.get("field")
                ans = "हाँ, बिल्कुल सही है"
                await session.answer_question(qid, ans)

        elif ev.type == "done":
            break

    session.bus.unsubscribe(event_queue)
    return events
