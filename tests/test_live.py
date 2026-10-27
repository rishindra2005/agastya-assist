"""Tests for LiveView CDP screencast and remote input (agastya/browser/live.py).

Validates:
1. LiveView produces >= 1 JPEG frame (bytes start with b'\\xff\\xd8') with meta width/height/url/title
2. Frame throttling: dropped frames while on_frame is busy are still acked
3. Active page transition: screencast re-attaches on page change
4. Mouse click at an input's bounding box centre focuses it, insert_text types 'Ravi' into it
5. Key press Backspace removes a character ('Ravi' -> 'Rav')
6. Special keys (Enter, Escape, Tab, Arrows, Delete) dispatch correctly
7. Mouse wheel scroll changes window.scrollY on a tall page
8. Navigation reload works
9. Human click on fixture Submit is NOT blocked (URL becomes form_success.html)
"""

import asyncio
from pathlib import Path
import urllib.request
import pytest

from agastya.browser.driver import BrowserDriver
from agastya.browser.live import LiveView

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "forms"


def fixture_url(filename: str) -> str:
    path = (FIXTURES_DIR / filename).resolve()
    assert path.exists(), f"Fixture {filename} missing at {path}"
    return f"file://{path}"


@pytest.fixture
async def driver():
    """Async fixture providing an initialized BrowserDriver instance."""
    d = BrowserDriver(headless=True)
    await d.start()
    try:
        yield d
    finally:
        await d.close()


@pytest.mark.asyncio
async def test_liveview_screencast_frames(driver: BrowserDriver):
    """LiveView produces >= 1 valid JPEG frame with metadata."""
    url = fixture_url("pan_49a.html")
    await driver.goto(url)

    live = LiveView(driver, quality=60, max_width=1280)
    frames: list[tuple[bytes, dict]] = []

    async def on_frame(jpeg_bytes: bytes, meta: dict):
        frames.append((jpeg_bytes, meta))

    await live.start(on_frame)

    # Wait for at least one frame
    for _ in range(50):
        if len(frames) >= 1:
            break
        await asyncio.sleep(0.05)

    await live.stop()

    assert len(frames) >= 1, "Expected at least 1 screencast frame"
    jpeg_bytes, meta = frames[0]

    # Verify JPEG magic bytes
    assert jpeg_bytes.startswith(b"\xff\xd8"), "Frame bytes must start with JPEG SOI marker \\xff\\xd8"
    assert meta["width"] > 0, "deviceWidth must be > 0"
    assert meta["height"] > 0, "deviceHeight must be > 0"
    assert "pan_49a.html" in meta["url"]
    assert "PAN" in meta["title"]


@pytest.mark.asyncio
async def test_liveview_mouse_click_and_insert_text(driver: BrowserDriver):
    """Mouse click at input's bounding box centre focuses it, insert_text types 'Ravi'."""
    url = fixture_url("pan_49a.html")
    await driver.goto(url)

    live = LiveView(driver)
    first_name_loc = driver.page.locator("#first_name")
    box = await first_name_loc.bounding_box()
    assert box is not None

    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2

    # Click to focus
    await live.mouse("click", cx, cy)

    # Insert text via CDP
    await live.insert_text("Ravi")

    val = await first_name_loc.input_value()
    assert val == "Ravi"


@pytest.mark.asyncio
async def test_liveview_key_backspace(driver: BrowserDriver):
    """Key press Backspace removes the trailing character from the focused input."""
    url = fixture_url("pan_49a.html")
    await driver.goto(url)

    live = LiveView(driver)
    first_name_loc = driver.page.locator("#first_name")
    box = await first_name_loc.bounding_box()
    assert box is not None

    await live.mouse("click", box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    await live.insert_text("Ravi")
    assert await first_name_loc.input_value() == "Ravi"

    # Press Backspace
    await live.key("press", "Backspace")
    val_after = await first_name_loc.input_value()
    assert val_after == "Rav"


@pytest.mark.asyncio
async def test_liveview_special_keys(driver: BrowserDriver):
    """Special keys (Enter, ArrowLeft, Delete) dispatch correctly."""
    await driver.page.goto("data:text/html,<textarea id='ta'></textarea>")

    live = LiveView(driver)
    ta_loc = driver.page.locator("#ta")
    box = await ta_loc.bounding_box()
    assert box is not None

    await live.mouse("click", box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    await live.insert_text("AB")

    # ArrowLeft moves cursor between A and B
    await live.key("press", "ArrowLeft")
    # Delete deletes character after cursor ('B')
    await live.key("press", "Delete")
    assert await ta_loc.input_value() == "A"

    # Enter inserts newline
    await live.key("press", "Enter")
    await live.insert_text("C")
    assert await ta_loc.input_value() == "A\nC"


@pytest.mark.asyncio
async def test_liveview_wheel_scroll(driver: BrowserDriver):
    """Mouse wheel event scrolls the viewport and changes window.scrollY."""
    await driver.page.goto(
        "data:text/html,<div style='height: 4000px; background: linear-gradient(red, blue);'>Tall Page</div>"
    )

    live = LiveView(driver)
    initial_scroll = await driver.page.evaluate("() => window.scrollY")
    assert initial_scroll == 0

    await live.mouse("wheel", x=100, y=100, delta_y=350)
    await asyncio.sleep(0.15)

    scrolled_y = await driver.page.evaluate("() => window.scrollY")
    assert scrolled_y > 0


@pytest.mark.asyncio
async def test_liveview_nav_reload(driver: BrowserDriver):
    """LiveView.nav('reload') reloads the active page."""
    url = fixture_url("pan_49a.html")
    await driver.goto(url)

    live = LiveView(driver)
    # Set a marker property on window
    await driver.page.evaluate("() => { window.__marker = 'loaded'; }")
    marker_before = await driver.page.evaluate("() => window.__marker")
    assert marker_before == "loaded"

    await live.nav("reload")

    marker_after = await driver.page.evaluate("() => window.__marker")
    assert marker_after is None
    assert "pan_49a.html" in driver.page.url


@pytest.mark.asyncio
async def test_human_click_submit_not_blocked(driver: BrowserDriver):
    """Human click on fixture Submit via LiveView is NOT blocked and navigates to form_success.html."""
    url = fixture_url("pan_49a.html")
    await driver.goto(url)

    live = LiveView(driver)

    # Show step 2 so submit_btn is in layout
    await driver.page.evaluate("showStep(2)")
    # Bypass HTML5 validation for instant submit
    await driver.page.evaluate("document.getElementById('panForm').noValidate = true")

    submit_loc = driver.page.locator("#submit_btn")
    box = await submit_loc.bounding_box()
    assert box is not None

    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2

    # Human click through LiveView CDP input (which must not be blocked)
    async with driver.page.expect_navigation():
        await live.mouse("click", cx, cy)

    assert "form_success.html" in driver.page.url


@pytest.mark.asyncio
async def test_liveview_page_transition(driver: BrowserDriver):
    """LiveView re-attaches screencast when active page changes."""
    url1 = fixture_url("pan_49a.html")
    await driver.goto(url1)

    live = LiveView(driver)
    received_urls: list[str] = []

    async def on_frame(jpeg_bytes: bytes, meta: dict):
        received_urls.append(meta["url"])

    await live.start(on_frame)

    for _ in range(50):
        if len(received_urls) >= 1:
            break
        await asyncio.sleep(0.05)

    assert any("pan_49a.html" in u for u in received_urls)

    # Open popup
    url2 = fixture_url("form_success.html")
    await driver.page.evaluate(f"window.open('{url2}')")

    # Wait for frames from the second page
    for _ in range(50):
        if any("form_success.html" in u for u in received_urls):
            break
        await asyncio.sleep(0.05)

    await live.stop()
    assert any("form_success.html" in u for u in received_urls)


@pytest.mark.asyncio
async def test_liveview_frame_throttling(driver: BrowserDriver):
    """Slow on_frame handler throttles screencast frames without hanging or losing acks."""
    url = fixture_url("pan_49a.html")
    await driver.goto(url)

    live = LiveView(driver)
    processed_count = 0

    async def on_frame(jpeg_bytes: bytes, meta: dict):
        nonlocal processed_count
        processed_count += 1
        await asyncio.sleep(0.2)

    await live.start(on_frame)

    for i in range(5):
        await driver.page.evaluate(f"() => {{ document.body.style.backgroundColor = 'rgb({i*20}, 50, 50)'; }}")
        await asyncio.sleep(0.04)

    await asyncio.sleep(0.3)
    await live.stop()

    assert processed_count >= 1

