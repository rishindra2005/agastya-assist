"""Playwright browser driver with safety guard enforcement and semantic snapshots.

Provides high-level browsing primitives (goto, snapshot, fill, select, click, screenshot)
anchored by stable element references ('f1', 'f2' for fields, 'a1', 'a2' for actions).
Safety guard checks block irreversible actions (submit, payment, OTP, login) before execution.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import logging
from pathlib import Path
import re
import socket
from typing import Any, Awaitable, Callable

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from agastya.browser.guard import GuardDecision, check_click, check_navigation
from agastya.browser.page_guard import install_page_guard
from agastya.config import get_settings

logger = logging.getLogger(__name__)

SNAPSHOT_JS_PATH = Path(__file__).parent / "snapshot.js"


def _find_free_port() -> int:
    """Find a free ephemeral TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class FieldInfo:
    """Descriptor for an interactive form field."""

    ref: str  # stable ref like "f1" usable by fill/select/click
    tag: str
    type: str
    label: str
    name: str
    id: str
    required: bool
    value: str
    options: list[str]  # options for <select>, radio groups, or checkbox groups


@dataclass
class PageSnapshot:
    """Semantic snapshot of a web page state."""

    url: str
    title: str
    text: str  # visible text, truncated to 6000 chars
    fields: list[FieldInfo]  # inputs/selects/textareas
    actions: list[dict[str, str]]  # buttons/links: {"ref", "tag", "text", "href"}


class BlockedActionError(Exception):
    """Raised when an automated action or navigation is blocked by the safety guard."""

    def __init__(self, decision: GuardDecision):
        self.decision = decision
        super().__init__(f"Action blocked by safety guard: {decision.reason}")


def _convert_date_format(val: str) -> str:
    """Normalize date inputs to YYYY-MM-DD format for HTML5 date inputs.

    Accepts DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD, YYYY/MM/DD.
    """
    cleaned = val.strip()
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", cleaned)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", cleaned)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    return cleaned


class BrowserDriver:
    """Asynchronous Playwright browser driver wrapped with Agastya safety guards."""

    def __init__(
        self,
        headless: bool | None = None,
        screenshot_dir: str | Path | None = None,
        cdp_port: int | None = None,
    ) -> None:
        settings = get_settings()
        self.headless = settings.HEADLESS if headless is None else headless
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self.screenshot_dir:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        self._configured_cdp_port: int = (
            cdp_port if cdp_port is not None else settings.CDP_PORT
        )
        self._cdp_port: int = self._configured_cdp_port
        self._cdp_endpoint: str = ""
        if self._cdp_port != 0:
            self._cdp_endpoint = f"http://127.0.0.1:{self._cdp_port}"

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._snapshot_js: str = SNAPSHOT_JS_PATH.read_text(encoding="utf-8")
        self._page_change_listeners: list[Callable[[Page], Awaitable[None] | None]] = []
        self._closing: bool = False

    @property
    def cdp_port(self) -> int:
        """Remote debugging CDP port allocated for the browser session."""
        if self._cdp_port == 0:
            self._cdp_port = _find_free_port()
            self._cdp_endpoint = f"http://127.0.0.1:{self._cdp_port}"
        return self._cdp_port

    @property
    def cdp_endpoint(self) -> str:
        """HTTP endpoint URL for Chrome remote debugging."""
        if not self._cdp_endpoint:
            self._cdp_endpoint = f"http://127.0.0.1:{self.cdp_port}"
        return self._cdp_endpoint

    @property
    def context(self) -> BrowserContext | None:
        """Access underlying Playwright BrowserContext instance."""
        return self._context

    @property
    def page(self) -> Page | None:
        """Access underlying Playwright Page instance (always the active tab)."""
        return self._page

    @property
    def url(self) -> str:
        """Current page URL, or '' before the first navigation."""
        return self._page.url if self._page is not None and not self._page.is_closed() else ""

    def add_page_change_listener(self, cb: Callable[[Page], Awaitable[None] | None]) -> None:
        """Register a callback invoked when the active page changes."""
        if cb not in self._page_change_listeners:
            self._page_change_listeners.append(cb)

    def remove_page_change_listener(self, cb: Callable[[Page], Awaitable[None] | None]) -> None:
        """Unregister a page change callback."""
        if cb in self._page_change_listeners:
            self._page_change_listeners.remove(cb)

    async def _notify_page_change(self, page: Page) -> None:
        """Notify all registered listeners about an active page change."""
        for cb in list(self._page_change_listeners):
            try:
                res = cb(page)
                if asyncio.iscoroutine(res) or inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.exception("Error in page change listener: %s", e)

    def _on_context_page(self, new_page: Page) -> None:
        """Handler for context 'page' event."""
        if self._closing:
            return
        asyncio.create_task(self._handle_new_page(new_page))

    async def _handle_new_page(self, new_page: Page) -> None:
        """Bring newly opened page to front and notify listeners."""
        if self._closing:
            return
        self._page = new_page
        new_page.on("close", lambda: asyncio.create_task(self._handle_page_closed(new_page)))
        try:
            await new_page.bring_to_front()
        except Exception:
            pass
        await self._notify_page_change(new_page)

    async def _handle_page_closed(self, closed_page: Page) -> None:
        """When active page closes, fall back to the last open page in context."""
        if self._closing:
            return
        if self._page == closed_page:
            remaining = [
                p
                for p in (self._context.pages if self._context else [])
                if not p.is_closed() and p != closed_page
            ]
            if remaining:
                self._page = remaining[-1]
                try:
                    await self._page.bring_to_front()
                except Exception:
                    pass
                await self._notify_page_change(self._page)
            else:
                self._page = None

    async def start(self) -> None:
        """Launch Playwright browser, context, and new page."""
        if self._page is not None and not self._page.is_closed():
            return
        self._closing = False
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if self._browser is None:
            channel = get_settings().BROWSER_CHANNEL or None
            port = self.cdp_port
            args = [f"--remote-debugging-port={port}"]
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                channel=channel,
                args=args,
            )
        if self._context is None:
            self._context = await self._browser.new_context()
            self._context.on("page", self._on_context_page)
            await install_page_guard(self._context)
        if self._page is None or self._page.is_closed():
            p = await self._context.new_page()
            if self._page is None:
                self._page = p
            await asyncio.sleep(0.05)

    async def close(self) -> None:
        """Close page, context, browser, and stop Playwright."""
        self._closing = True
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        if self._configured_cdp_port == 0:
            self._cdp_port = 0
            self._cdp_endpoint = ""

    async def __aenter__(self) -> BrowserDriver:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def goto(self, url: str) -> PageSnapshot:
        """Navigate to a URL, validating against the safety guard first.

        Raises:
            BlockedActionError: If navigation is to a payment gateway or logout URL.
        """
        decision = check_navigation(url)
        if not decision.allowed:
            raise BlockedActionError(decision)

        if self._page is None or self._page.is_closed():
            await self.start()
        assert self._page is not None

        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return await self.snapshot()

    async def snapshot(self) -> PageSnapshot:
        """Take a semantic snapshot of visible interactive elements on the page."""
        if self._page is None or self._page.is_closed():
            await self.start()
        assert self._page is not None

        raw = await self._page.evaluate(self._snapshot_js)
        fields = [
            FieldInfo(
                ref=f["ref"],
                tag=f["tag"],
                type=f["type"],
                label=f["label"],
                name=f["name"],
                id=f["id"],
                required=f["required"],
                value=f["value"],
                options=f["options"],
            )
            for f in raw.get("fields", [])
        ]
        return PageSnapshot(
            url=raw.get("url", self._page.url),
            title=raw.get("title", ""),
            text=raw.get("text", "")[:6000],
            fields=fields,
            actions=raw.get("actions", []),
        )

    async def fill(self, ref: str, value: str) -> None:
        """Fill a field identified by its ref.

        Handles text, date (normalizing DD/MM/YYYY or YYYY-MM-DD), email, tel,
        checkbox ('true'/'yes' checks it), and radio (fill by option label or value).

        Raises:
            ValueError: If ref is unknown or option not found in group.
        """
        if self._page is None or self._page.is_closed():
            raise ValueError(f"Browser is not open. Cannot fill ref '{ref}'.")

        loc = self._page.locator(f'[data-agastya-ref="{ref}"]')
        count = await loc.count()
        if count == 0:
            raise ValueError(f"Unknown ref: '{ref}'. No element matches data-agastya-ref='{ref}'.")

        elements_info = await loc.evaluate_all("""(elems) => elems.map(el => ({
            tag: (el.tagName || '').toLowerCase(),
            type: (el.getAttribute('type') || el.type || '').toLowerCase(),
            value: el.value || '',
            label: (el.closest('label') ? el.closest('label').innerText : (el.getAttribute('aria-label') || '')).trim(),
            option: el.getAttribute('data-agastya-option') || '',
        }))""")

        first_tag = elements_info[0]["tag"]
        first_type = elements_info[0]["type"]

        # Radio button group
        if first_type == "radio":
            val_clean = str(value).strip()
            val_lower = val_clean.lower()
            matched_idx = -1
            available = []

            for i, info in enumerate(elements_info):
                opt_val = info["value"].strip()
                opt_lbl = info["label"].strip()
                available.append(f"{opt_lbl} ({opt_val})" if opt_lbl != opt_val else opt_val)

                # Exact match
                if val_clean == opt_lbl or val_clean == opt_val:
                    matched_idx = i
                    break
                # Case-insensitive match
                if val_lower == opt_lbl.lower() or val_lower == opt_val.lower():
                    matched_idx = i
                    break
                # Substring match
                if val_lower in opt_lbl.lower() or (opt_val and val_lower in opt_val.lower()):
                    matched_idx = i
                    break

            if matched_idx == -1:
                raise ValueError(
                    f"Option '{value}' not found in radio group '{ref}'. Available options: {available}"
                )
            await loc.nth(matched_idx).check()
            return

        # Checkbox (single or group)
        if first_type == "checkbox":
            val_clean = str(value).strip()
            val_lower = val_clean.lower()

            if count == 1:
                if val_lower in ("true", "yes", "1", "on", "checked"):
                    await loc.first.check()
                elif val_lower in ("false", "no", "0", "off", "unchecked"):
                    await loc.first.uncheck()
                elif (
                    val_lower == elements_info[0]["value"].lower()
                    or val_lower == elements_info[0]["label"].lower()
                ):
                    await loc.first.check()
                else:
                    await loc.first.check()
                return
            else:
                matched_idx = -1
                available = []
                for i, info in enumerate(elements_info):
                    opt_val = info["value"].strip()
                    opt_lbl = info["label"].strip()
                    available.append(f"{opt_lbl} ({opt_val})" if opt_lbl != opt_val else opt_val)

                    if val_clean == opt_lbl or val_clean == opt_val:
                        matched_idx = i
                        break
                    if val_lower == opt_lbl.lower() or val_lower == opt_val.lower():
                        matched_idx = i
                        break
                    if val_lower in opt_lbl.lower() or (opt_val and val_lower in opt_val.lower()):
                        matched_idx = i
                        break

                if matched_idx != -1:
                    await loc.nth(matched_idx).check()
                    return
                elif val_lower in ("true", "yes", "1", "on"):
                    await loc.first.check()
                    return
                elif val_lower in ("false", "no", "0", "off"):
                    for i in range(count):
                        await loc.nth(i).uncheck()
                    return
                else:
                    raise ValueError(
                        f"Option '{value}' not found in checkbox group '{ref}'. Available options: {available}"
                    )

        # Forward select if fill called on <select>
        if first_tag == "select":
            await self.select(ref, value)
            return

        # Date input
        if first_type == "date":
            normalized_date = _convert_date_format(str(value))
            await loc.first.fill(normalized_date)
            return

        # Text, email, tel, password, textarea, etc.
        await loc.first.fill(str(value))

    async def select(self, ref: str, option: str) -> None:
        """Select an option in a <select> element by label or value.

        Matches by exact label, then exact value, then case-insensitive substring.

        Raises:
            ValueError: If ref is unknown, not a <select>, or option not found.
        """
        if self._page is None or self._page.is_closed():
            raise ValueError(f"Browser is not open. Cannot select on ref '{ref}'.")

        loc = self._page.locator(f'[data-agastya-ref="{ref}"]')
        count = await loc.count()
        if count == 0:
            raise ValueError(f"Unknown ref: '{ref}'. No element matches data-agastya-ref='{ref}'.")

        first_el = loc.first
        tag = await first_el.evaluate("(el) => (el.tagName || '').toLowerCase()")
        if tag != "select":
            raise ValueError(f"Element '{ref}' is a <{tag}>, not a <select>.")

        options_data = await first_el.evaluate("""(sel) => {
            return Array.from(sel.options).map((opt, idx) => ({
                index: idx,
                value: opt.value || '',
                text: (opt.text || '').trim(),
                disabled: opt.disabled,
            }));
        }""")

        target = option.strip()
        target_lower = target.lower()

        # 1. Exact label match
        for opt in options_data:
            if opt["text"] == target:
                await first_el.select_option(index=opt["index"])
                return

        # 2. Exact value match
        for opt in options_data:
            if opt["value"] == target:
                await first_el.select_option(index=opt["index"])
                return

        # 3. Exact combined match e.g. "Text (val)"
        for opt in options_data:
            combined = f"{opt['text']} ({opt['value']})"
            if combined == target:
                await first_el.select_option(index=opt["index"])
                return

        # 4. Case-insensitive exact label match
        for opt in options_data:
            if opt["text"].lower() == target_lower:
                await first_el.select_option(index=opt["index"])
                return

        # 5. Case-insensitive exact value match
        for opt in options_data:
            if opt["value"].lower() == target_lower:
                await first_el.select_option(index=opt["index"])
                return

        # 6. Case-insensitive exact combined match
        for opt in options_data:
            combined = f"{opt['text']} ({opt['value']})"
            if combined.lower() == target_lower:
                await first_el.select_option(index=opt["index"])
                return

        # 7. Case-insensitive substring match in label
        for opt in options_data:
            if target_lower in opt["text"].lower():
                await first_el.select_option(index=opt["index"])
                return

        # 8. Case-insensitive substring match in value
        for opt in options_data:
            if opt["value"] and target_lower in opt["value"].lower():
                await first_el.select_option(index=opt["index"])
                return

        available = [f"{o['text']} ({o['value']})" if o["value"] else o["text"] for o in options_data]
        raise ValueError(
            f"Option '{option}' not found in <select> '{ref}'. Available options: {available}"
        )

    async def click(self, ref: str) -> PageSnapshot:
        """Click an element identified by ref after validating against safety guard.

        Raises:
            BlockedActionError: If the click target triggers a final or sensitive action.
            ValueError: If ref is unknown.
        """
        if self._page is None or self._page.is_closed():
            raise ValueError(f"Browser is not open. Cannot click ref '{ref}'.")

        loc = self._page.locator(f'[data-agastya-ref="{ref}"]')
        count = await loc.count()
        if count == 0:
            raise ValueError(f"Unknown ref: '{ref}'. No element matches data-agastya-ref='{ref}'.")

        attrs = await loc.first.evaluate("""(el) => ({
            tag: (el.tagName || '').toLowerCase(),
            type: (el.getAttribute('type') || el.type || '').toLowerCase(),
            text: (el.innerText || el.textContent || el.value || '').trim(),
            id: el.id || '',
            name: el.getAttribute('name') || '',
            aria_label: el.getAttribute('aria-label') || el.getAttribute('aria_label') || '',
            value: el.value || '',
            href: el.getAttribute('href') || el.getAttribute('formaction') || '',
            url: window.location.href,
        })""")

        # Call guard check BEFORE clicking
        decision = check_click(attrs)
        if not decision.allowed:
            raise BlockedActionError(decision)

        # Allowed -> perform click
        await loc.first.click()

        # Wait for domcontentloaded state up to 10 seconds
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

        return await self.snapshot()

    async def screenshot(self) -> bytes:
        """Capture screenshot and return PNG bytes."""
        if self._page is None or self._page.is_closed():
            raise ValueError("Browser is not open. Cannot take screenshot.")

        png_bytes = await self._page.screenshot(type="png", full_page=False)

        if self.screenshot_dir:
            try:
                import time
                ts = int(time.time() * 1000)
                out_path = self.screenshot_dir / f"screenshot_{ts}.png"
                out_path.write_bytes(png_bytes)
            except Exception:
                pass

        return png_bytes
