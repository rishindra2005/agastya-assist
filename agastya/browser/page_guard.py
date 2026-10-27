"""In-page safety guard for browser automation (second safety layer).

Installs a client-side JavaScript guard script into all pages and frames via
context.add_init_script, tracks agent-active mode, and prevents client-side
submissions, clicks on blocked actions (submit, payment, login, OTP, e-verify),
and programmatic form submissions while the automated agent is active.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Frame, Page

_PAGE_GUARD_JS_PATH = Path(__file__).with_name("page_guard.js")
_PAGE_GUARD_JS = _PAGE_GUARD_JS_PATH.read_text(encoding="utf-8")


async def install_page_guard(context: BrowserContext) -> None:
    """Install the in-page safety guard into a Playwright BrowserContext.

    Registers page_guard.js via add_init_script so all new documents/frames
    automatically run the guard script. Applies the script to all currently
    open pages and frames. Registers event listeners on the context and pages
    to re-apply the agent-active state on navigation and new page events.
    """
    if getattr(context, "_agastya_page_guard_installed", False):
        return
    setattr(context, "_agastya_page_guard_installed", True)

    if not hasattr(context, "_agastya_agent_active"):
        setattr(context, "_agastya_agent_active", False)

    # 1. Add guard script to context (runs in all new frames/pages)
    await context.add_init_script(script=_PAGE_GUARD_JS)

    # 2. Register frame/page navigation listeners to re-apply agent-active state
    async def _on_frame_navigated(frame: Frame) -> None:
        active = getattr(context, "_agastya_agent_active", False)
        try:
            await frame.evaluate(
                "(val) => { window.__agastyaAgentActive = val; }",
                active,
            )
        except Exception:
            pass

    async def _on_page(page: Page) -> None:
        page.on("framenavigated", _on_frame_navigated)
        active = getattr(context, "_agastya_agent_active", False)
        for frame in page.frames:
            try:
                await frame.evaluate(
                    "(val) => { window.__agastyaAgentActive = val; }",
                    active,
                )
            except Exception:
                pass

    context.on("page", _on_page)
    for p in context.pages:
        p.on("framenavigated", _on_frame_navigated)

    # 3. Apply to all currently open pages and frames
    active = getattr(context, "_agastya_agent_active", False)
    for page in context.pages:
        for frame in page.frames:
            try:
                await frame.evaluate(_PAGE_GUARD_JS)
                await frame.evaluate(
                    "(val) => { window.__agastyaAgentActive = val; }",
                    active,
                )
            except Exception:
                pass


async def set_agent_active(context: BrowserContext, active: bool) -> None:
    """Set the agent-active flag on all pages and frames in a BrowserContext.

    When active is True, in-page actions (submit, pay, login, OTP...) are blocked.
    When active is False, human user is in control and actions are allowed.

    Stores the desired state on the context object, updates the init script for
    subsequent navigations, and evaluates the setting on all existing pages and frames.
    """
    flag = bool(active)
    setattr(context, "_agastya_agent_active", flag)

    # Ensure future documents/navigations inherit this state immediately
    await context.add_init_script(
        script=f"window.__agastyaAgentActive = {'true' if flag else 'false'};"
    )

    # Apply to all currently open pages and frames (with try/except for detached frames)
    for page in context.pages:
        for frame in page.frames:
            try:
                await frame.evaluate(
                    "(val) => { window.__agastyaAgentActive = val; }",
                    flag,
                )
            except Exception:
                pass


async def get_blocked_actions(page: Page) -> list[dict[str, Any]]:
    """Retrieve the list of blocked actions recorded on the page."""
    try:
        res = await page.evaluate("() => window.__agastyaBlocked || []")
        return list(res) if isinstance(res, list) else []
    except Exception:
        return []
