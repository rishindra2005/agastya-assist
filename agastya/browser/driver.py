"""Playwright browser driver with safety guard enforcement and semantic snapshots.

Provides high-level browsing primitives (goto, snapshot, fill, select, click, screenshot)
anchored by stable element references ('f1', 'f2' for fields, 'a1', 'a2' for actions).
Safety guard checks block irreversible actions (submit, payment, OTP, login) before execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from agastya.browser.guard import GuardDecision, check_click, check_navigation
from agastya.config import get_settings

SNAPSHOT_JS_PATH = Path(__file__).parent / "snapshot.js"


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
    ) -> None:
        settings = get_settings()
        self.headless = settings.HEADLESS if headless is None else headless
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self.screenshot_dir:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._snapshot_js: str = SNAPSHOT_JS_PATH.read_text(encoding="utf-8")

    @property
    def page(self) -> Page | None:
        """Access underlying Playwright Page instance."""
        return self._page

    @property
    def url(self) -> str:
        """Current page URL, or '' before the first navigation."""
        return self._page.url if self._page is not None and not self._page.is_closed() else ""

    async def start(self) -> None:
        """Launch Playwright browser, context, and new page."""
        if self._page is not None and not self._page.is_closed():
            return
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if self._browser is None:
            channel = get_settings().BROWSER_CHANNEL or None
            self._browser = await self._playwright.chromium.launch(headless=self.headless, channel=channel)
        if self._context is None:
            self._context = await self._browser.new_context()
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()

    async def close(self) -> None:
        """Close page, context, browser, and stop Playwright."""
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
