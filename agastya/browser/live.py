"""Live browser view via Chrome DevTools Protocol (CDP) screencast and remote input.

Provides LiveView:
- Page.startScreencast / Page.screencastFrameAck stream of JPEG frames
- Frame metadata: width/height (CSS px from deviceWidth/deviceHeight), url, title
- Dropped-frame throttling while on_frame is actively processing
- Automatic re-attachment to active page on page changes
- Remote user input dispatching: mouse, key, insert_text, nav
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
from typing import Awaitable, Callable, Literal

from playwright.async_api import CDPSession, Page

from agastya.browser.driver import BrowserDriver
from agastya.config import get_settings

logger = logging.getLogger(__name__)

# Canonical mapping for special keys that require windowsVirtualKeyCode in CDP
# (canonical_key, windowsVirtualKeyCode, default_code)
SPECIAL_KEY_MAP: dict[str, tuple[str, int, str]] = {
    "backspace": ("Backspace", 8, "Backspace"),
    "tab": ("Tab", 9, "Tab"),
    "enter": ("Enter", 13, "Enter"),
    "return": ("Enter", 13, "Enter"),
    "escape": ("Escape", 27, "Escape"),
    "esc": ("Escape", 27, "Escape"),
    "delete": ("Delete", 46, "Delete"),
    "del": ("Delete", 46, "Delete"),
    "arrowleft": ("ArrowLeft", 37, "ArrowLeft"),
    "left": ("ArrowLeft", 37, "ArrowLeft"),
    "arrowup": ("ArrowUp", 38, "ArrowUp"),
    "up": ("ArrowUp", 38, "ArrowUp"),
    "arrowright": ("ArrowRight", 39, "ArrowRight"),
    "right": ("ArrowRight", 39, "ArrowRight"),
    "arrowdown": ("ArrowDown", 40, "ArrowDown"),
    "down": ("ArrowDown", 40, "ArrowDown"),
}


class LiveView:
    """Manages CDP screencast and user input on the active browser page."""

    def __init__(
        self,
        driver: BrowserDriver,
        quality: int | None = None,
        max_width: int | None = None,
    ) -> None:
        self.driver = driver
        settings = get_settings()
        self.quality = quality if quality is not None else settings.LIVE_QUALITY
        self.max_width = max_width if max_width is not None else settings.LIVE_MAX_WIDTH

        self._on_frame: Callable[[bytes, dict], Awaitable[None]] | None = None
        self._cdp: CDPSession | None = None
        self._cdp_page: Page | None = None
        self._frame_busy: bool = False
        self._started: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def is_streaming(self) -> bool:
        """True if the live screencast is currently active."""
        return self._started

    async def start(self, on_frame: Callable[[bytes, dict], Awaitable[None]]) -> None:
        """Start CDP screencast on driver.page and listen for page changes."""
        async with self._lock:
            if self._started:
                return
            self._started = True
            self._on_frame = on_frame

            if self.driver.page is None or self.driver.page.is_closed():
                await self.driver.start()

            self.driver.add_page_change_listener(self._on_page_change)

            if self.driver.page is not None and not self.driver.page.is_closed():
                await self._attach_page_cdp(self.driver.page)

    async def stop(self) -> None:
        """Stop screencast and detach CDP session."""
        async with self._lock:
            if not self._started:
                return
            self._started = False
            self.driver.remove_page_change_listener(self._on_page_change)
            await self._detach_cdp()
            self._on_frame = None

    async def _attach_page_cdp(self, page: Page) -> None:
        """Establish CDP session on page and start screencast."""
        await self._detach_cdp()
        if self.driver.context is None or page.is_closed():
            return

        try:
            cdp = await self.driver.context.new_cdp_session(page)
        except Exception as e:
            logger.warning(f"Failed to create CDP session for page: {e}")
            return

        self._cdp = cdp
        self._cdp_page = page

        async def _screencast_handler(event: dict) -> None:
            await self._handle_frame(cdp, event)

        cdp.on("Page.screencastFrame", lambda e: asyncio.create_task(_screencast_handler(e)))

        try:
            await cdp.send(
                "Page.startScreencast",
                {
                    "format": "jpeg",
                    "quality": int(self.quality),
                    "maxWidth": int(self.max_width),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to start screencast on CDP session: {e}")

    async def _detach_cdp(self) -> None:
        """Stop screencast on existing session and detach."""
        cdp = self._cdp
        self._cdp = None
        self._cdp_page = None
        if cdp is not None:
            try:
                await cdp.send("Page.stopScreencast")
            except Exception:
                pass
            try:
                await cdp.detach()
            except Exception:
                pass

    async def _on_page_change(self, new_page: Page) -> None:
        """Handle active page changes by restarting screencast on the new page."""
        async with self._lock:
            if not self._started:
                return
            if self._cdp_page == new_page and self._cdp is not None:
                return
            await self._attach_page_cdp(new_page)

    async def _handle_frame(self, cdp: CDPSession, event: dict) -> None:
        """Handle incoming screencastFrame event from CDP."""
        session_id = event.get("sessionId")
        if session_id is None:
            return

        # Throttle: if on_frame is still running or LiveView stopped, drop frame but still ack
        if self._frame_busy or not self._started or self._cdp is not cdp:
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:
                pass
            return

        self._frame_busy = True
        try:
            # Always acknowledge frame immediately so Chromium continues rendering
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:
                pass

            data_b64 = event.get("data", "")
            jpeg_bytes = base64.b64decode(data_b64)
            metadata = event.get("metadata", {})
            width = metadata.get("deviceWidth", 0)
            height = metadata.get("deviceHeight", 0)

            current_page = self.driver.page
            url = current_page.url if current_page and not current_page.is_closed() else ""
            title = ""
            if current_page and not current_page.is_closed():
                try:
                    title = await current_page.title()
                except Exception:
                    title = ""

            meta = {
                "width": width,
                "height": height,
                "url": url,
                "title": title,
            }

            if self._on_frame is not None and self._started:
                res = self._on_frame(jpeg_bytes, meta)
                if inspect.isawaitable(res):
                    await res
        except Exception as e:
            logger.exception(f"Error handling screencast frame: {e}")
        finally:
            self._frame_busy = False

    async def _ensure_cdp(self) -> CDPSession:
        """Ensure active CDP session exists for the current page."""
        if self.driver.page is None or self.driver.page.is_closed():
            await self.driver.start()
        assert self.driver.page is not None
        assert self.driver.context is not None

        if self._cdp is None or self._cdp_page != self.driver.page:
            if self._cdp is not None:
                await self._detach_cdp()
            self._cdp = await self.driver.context.new_cdp_session(self.driver.page)
            self._cdp_page = self.driver.page
        return self._cdp

    async def mouse(
        self,
        kind: Literal["move", "down", "up", "click", "wheel"],
        x: float,
        y: float,
        button: str = "left",
        delta_y: float = 0,
        click_count: int = 1,
    ) -> None:
        """Dispatch remote mouse event over CDP."""
        cdp = await self._ensure_cdp()
        btn = button.lower() if button else "left"
        if btn not in ("left", "middle", "right", "none"):
            btn = "left"

        if kind == "move":
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "x": float(x),
                    "y": float(y),
                },
            )
        elif kind == "down":
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": float(x),
                    "y": float(y),
                    "button": btn,
                    "clickCount": int(click_count),
                },
            )
        elif kind == "up":
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": float(x),
                    "y": float(y),
                    "button": btn,
                    "clickCount": int(click_count),
                },
            )
        elif kind == "click":
            # Click = pressed + released with clickCount
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": float(x),
                    "y": float(y),
                    "button": btn,
                    "clickCount": int(click_count),
                },
            )
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": float(x),
                    "y": float(y),
                    "button": btn,
                    "clickCount": int(click_count),
                },
            )
        elif kind == "wheel":
            # Wheel = mouseWheel with deltaY
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": float(x),
                    "y": float(y),
                    "deltaX": 0,
                    "deltaY": float(delta_y),
                },
            )
        else:
            raise ValueError(f"Unknown mouse event kind: {kind}")

    async def key(
        self,
        kind: Literal["down", "up", "press"],
        key: str,
        code: str = "",
        text: str = "",
    ) -> None:
        """Dispatch remote keyboard event over CDP."""
        cdp = await self._ensure_cdp()
        k_lower = key.lower()

        if k_lower in SPECIAL_KEY_MAP:
            canonical_key, vk_code, default_code = SPECIAL_KEY_MAP[k_lower]
            code = code or default_code
            # Enter generates \r on printable keyDown
            txt = text if text else ("\r" if canonical_key == "Enter" and kind in ("down", "press") else "")
        else:
            canonical_key = key
            vk_code = None
            txt = text if text else (key if len(key) == 1 else "")
            if not code:
                if key.isalpha() and len(key) == 1:
                    code = f"Key{key.upper()}"
                elif key.isdigit() and len(key) == 1:
                    code = f"Digit{key}"
                elif key == " ":
                    code = "Space"
                else:
                    code = key

        if kind == "press":
            # 'press' = keyDown(with text for printable) + keyUp
            down_params: dict[str, Any] = {
                "type": "keyDown",
                "key": canonical_key,
                "code": code,
            }
            if vk_code is not None:
                down_params["windowsVirtualKeyCode"] = vk_code
            if txt:
                down_params["text"] = txt

            up_params: dict[str, Any] = {
                "type": "keyUp",
                "key": canonical_key,
                "code": code,
            }
            if vk_code is not None:
                up_params["windowsVirtualKeyCode"] = vk_code

            await cdp.send("Input.dispatchKeyEvent", down_params)
            await cdp.send("Input.dispatchKeyEvent", up_params)
        elif kind == "down":
            down_params = {
                "type": "keyDown",
                "key": canonical_key,
                "code": code,
            }
            if vk_code is not None:
                down_params["windowsVirtualKeyCode"] = vk_code
            if txt:
                down_params["text"] = txt
            await cdp.send("Input.dispatchKeyEvent", down_params)
        elif kind == "up":
            up_params = {
                "type": "keyUp",
                "key": canonical_key,
                "code": code,
            }
            if vk_code is not None:
                up_params["windowsVirtualKeyCode"] = vk_code
            await cdp.send("Input.dispatchKeyEvent", up_params)
        else:
            raise ValueError(f"Unknown key event kind: {kind}")

    async def insert_text(self, text: str) -> None:
        """Insert arbitrary text via CDP Input.insertText."""
        cdp = await self._ensure_cdp()
        await cdp.send("Input.insertText", {"text": text})

    async def nav(self, action: Literal["back", "forward", "reload"]) -> None:
        """Trigger navigation action on the active page."""
        page = self.driver.page
        if page is None or page.is_closed():
            raise ValueError("No active page available for navigation.")

        if action == "back":
            await page.go_back(wait_until="domcontentloaded")
        elif action == "forward":
            await page.go_forward(wait_until="domcontentloaded")
        elif action == "reload":
            await page.reload(wait_until="domcontentloaded")
        else:
            raise ValueError(f"Unknown nav action: {action}")
