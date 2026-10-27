"""Tests for in-page safety guard (agastya.browser.page_guard).

Validates client-side safety layer:
- In agent-active mode:
  - JS el.click() on Submit is blocked, stays on page, records 1 entry in __agastyaBlocked
  - form.submit() and form.requestSubmit() are blocked as no-ops
  - Enter key in text fields does not submit/navigate the form
  - Hindi action buttons ('जमा करें', 'ओटीपी', 'भुगतान') are blocked
  - Payment gateway links and formactions are blocked
  - Safe actions ('Next', 'Back', 'Save Draft') are allowed and execute
  - CustomEvent 'agastya-blocked' is dispatched on window with {text, reason, ts}
- In human mode (inactive):
  - Submitting navigates to form_success.html
  - form.submit() navigates to form_success.html
- Context lifecycle:
  - Agent-active state persists across page navigations to other fixtures
  - In-page guard runs inside child iframes
  - Idempotent installation on context
"""

from __future__ import annotations

from pathlib import Path
import pytest
from playwright.async_api import BrowserContext, Page, async_playwright

from agastya.browser.page_guard import (
    get_blocked_actions,
    install_page_guard,
    set_agent_active,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "forms"


def fixture_url(filename: str) -> str:
    path = (FIXTURES_DIR / filename).resolve()
    assert path.exists(), f"Fixture {filename} missing at {path}"
    return f"file://{path}"


@pytest.fixture
async def browser_context():
    """Async fixture providing an initialized BrowserContext with page guard."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await install_page_guard(context)
        try:
            yield context
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_agent_active_submit_click_blocked_on_pan_49a(browser_context: BrowserContext):
    """agent-active + JS el.click() on Submit -> stays on page and __agastyaBlocked has 1 entry."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()
    url = fixture_url("pan_49a.html")
    await page.goto(url)

    # Click submit button via JavaScript
    await page.evaluate("document.querySelector('#submit_btn').click()")

    # Must stay on the same page
    assert page.url == url
    assert "form_success.html" not in page.url

    # Check __agastyaBlocked
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 1
    assert blocked[0]["text"] == "Submit"
    assert blocked[0]["reason"] == "submit"
    assert isinstance(blocked[0]["ts"], (int, float))


@pytest.mark.asyncio
async def test_agent_active_submit_click_blocked_on_voter_form6(browser_context: BrowserContext):
    """agent-active + JS el.click() on Submit on voter_form6 -> blocked, stays on page."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()
    url = fixture_url("voter_form6.html")
    await page.goto(url)

    await page.evaluate("document.querySelector('#submit_btn').click()")

    assert page.url == url
    assert "form_success.html" not in page.url

    blocked = await get_blocked_actions(page)
    assert len(blocked) == 1
    assert blocked[0]["text"] == "Submit"
    assert blocked[0]["reason"] == "submit"


@pytest.mark.asyncio
async def test_agent_active_form_submit_blocked(browser_context: BrowserContext):
    """agent-active + form.submit() -> blocked as a no-op."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()
    url = fixture_url("pan_49a.html")
    await page.goto(url)

    await page.evaluate("document.querySelector('#panForm').submit()")

    assert page.url == url
    assert "form_success.html" not in page.url

    blocked = await get_blocked_actions(page)
    assert len(blocked) == 1
    assert "submit" in blocked[0]["reason"]


@pytest.mark.asyncio
async def test_agent_active_form_request_submit_blocked(browser_context: BrowserContext):
    """agent-active + form.requestSubmit() -> blocked as a no-op."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()
    url = fixture_url("voter_form6.html")
    await page.goto(url)

    await page.evaluate("document.querySelector('#voterForm6').requestSubmit()")

    assert page.url == url
    assert "form_success.html" not in page.url

    blocked = await get_blocked_actions(page)
    assert len(blocked) == 1
    assert "submit" in blocked[0]["reason"]


@pytest.mark.asyncio
async def test_agent_active_enter_in_text_field_does_not_navigate(browser_context: BrowserContext):
    """Enter key in a text field of a form -> no navigation while active."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()
    url = fixture_url("pan_49a.html")
    await page.goto(url)

    # Focus text input and press Enter
    await page.locator("#last_name").focus()
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(300)

    # Must stay on the form
    assert page.url == url
    assert "form_success.html" not in page.url

    # A block event was recorded for the attempted form submission
    blocked = await get_blocked_actions(page)
    assert len(blocked) >= 1
    assert any("submit" in b["reason"] for b in blocked)


@pytest.mark.asyncio
async def test_agent_active_next_and_save_draft_works(browser_context: BrowserContext):
    """agent-active click on 'Next'/'Save Draft' works without blocking."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()
    url = fixture_url("pan_49a.html")
    await page.goto(url)

    # Verify initial multi-step state: step1 visible, step2 hidden
    assert await page.evaluate("window.getComputedStyle(document.getElementById('step1')).display !== 'none'")
    assert await page.evaluate("window.getComputedStyle(document.getElementById('step2')).display === 'none'")

    # Click 'Next' button
    await page.evaluate("document.querySelector('#next_btn').click()")

    # Step 2 must be displayed now
    assert await page.evaluate("window.getComputedStyle(document.getElementById('step2')).display === 'block'")

    # Click 'Back' button
    await page.evaluate("document.querySelector('#back_btn').click()")
    assert await page.evaluate("window.getComputedStyle(document.getElementById('step1')).display === 'block'")

    # Neither Next nor Back should be recorded in __agastyaBlocked
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 0

    # Test Save Draft button on dynamic content
    await page.goto(
        "data:text/html;charset=utf-8,"
        "<button id='draft_btn'>Save Draft</button>"
        "<span id='out'></span>"
        "<script>document.getElementById('draft_btn').onclick=()=>document.getElementById('out').textContent='saved';</script>"
    )
    await page.evaluate("document.querySelector('#draft_btn').click()")
    assert await page.evaluate("document.getElementById('out').textContent") == "saved"

    blocked = await get_blocked_actions(page)
    assert len(blocked) == 0


@pytest.mark.asyncio
async def test_human_mode_submit_navigates_to_form_success(browser_context: BrowserContext):
    """human mode (inactive) Submit navigates to form_success.html."""
    await set_agent_active(browser_context, False)
    page = await browser_context.new_page()
    url = fixture_url("voter_form6.html")
    await page.goto(url)

    # Active flag must be false in human mode
    assert await page.evaluate("() => window.__agastyaAgentActive") is False

    # Clicking submit in human mode must navigate to form_success.html
    async with page.expect_navigation():
        await page.evaluate(
            "const form = document.querySelector('#voterForm6'); "
            "form.noValidate = true; "
            "document.querySelector('#submit_btn').click();"
        )

    assert "form_success.html" in page.url


@pytest.mark.asyncio
async def test_human_mode_form_submit_navigates_to_form_success(browser_context: BrowserContext):
    """human mode (inactive) form.submit() navigates to form_success.html."""
    await set_agent_active(browser_context, False)
    page = await browser_context.new_page()
    url = fixture_url("pan_49a.html")
    await page.goto(url)

    async with page.expect_navigation():
        await page.evaluate("document.querySelector('#panForm').submit()")

    assert "form_success.html" in page.url


@pytest.mark.asyncio
async def test_state_persists_across_fixture_navigations(browser_context: BrowserContext):
    """state persists after navigation to another fixture page."""
    page = await browser_context.new_page()

    # 1. Set active -> navigate to pan_49a -> persists True
    await set_agent_active(browser_context, True)
    await page.goto(fixture_url("pan_49a.html"))
    assert await page.evaluate("() => window.__agastyaAgentActive") is True

    # 2. Navigate to voter_form6 -> remains True
    await page.goto(fixture_url("voter_form6.html"))
    assert await page.evaluate("() => window.__agastyaAgentActive") is True

    # 3. Set inactive -> persists False
    await set_agent_active(browser_context, False)
    assert await page.evaluate("() => window.__agastyaAgentActive") is False

    # 4. Navigate back to pan_49a -> remains False
    await page.goto(fixture_url("pan_49a.html"))
    assert await page.evaluate("() => window.__agastyaAgentActive") is False


@pytest.mark.asyncio
async def test_hindi_buttons_blocked(browser_context: BrowserContext):
    """Hindi 'जमा करें' and related government action buttons are blocked."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()

    html = (
        "data:text/html;charset=utf-8,"
        "<button id='b_submit'>जमा करें</button>"
        "<button id='b_otp'>ओटीपी भेजें</button>"
        "<button id='b_pay'>भुगतान करें</button>"
        "<button id='b_verify'>सत्यापित करें</button>"
        "<button id='b_login'>लॉगिन</button>"
        "<button id='b_confirm'>पुष्टि करें</button>"
    )
    await page.goto(html)

    # Click Hindi submit button
    await page.evaluate("document.querySelector('#b_submit').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 1
    assert blocked[0]["text"] == "जमा करें"
    assert blocked[0]["reason"] == "submit (hindi)"

    # Click Hindi OTP button
    await page.evaluate("document.querySelector('#b_otp').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 2
    assert "otp" in blocked[1]["reason"]

    # Click Hindi payment button
    await page.evaluate("document.querySelector('#b_pay').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 3
    assert "payment" in blocked[2]["reason"]

    # Click Hindi verify button
    await page.evaluate("document.querySelector('#b_verify').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 4
    assert "verify" in blocked[3]["reason"]

    # Click Hindi login button
    await page.evaluate("document.querySelector('#b_login').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 5
    assert "login" in blocked[4]["reason"]

    # Click Hindi confirm button
    await page.evaluate("document.querySelector('#b_confirm').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 6
    assert "confirm" in blocked[5]["reason"]


@pytest.mark.asyncio
async def test_custom_event_dispatched_on_block(browser_context: BrowserContext):
    """Each block dispatches a CustomEvent 'agastya-blocked' on window."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()
    await page.goto(fixture_url("pan_49a.html"))

    # Register listener for 'agastya-blocked'
    await page.evaluate(
        "window.__caughtEvents = []; "
        "window.addEventListener('agastya-blocked', (e) => { "
        "  window.__caughtEvents.push(e.detail); "
        "});"
    )

    # Trigger blocked click
    await page.evaluate("document.querySelector('#submit_btn').click()")

    caught = await page.evaluate("() => window.__caughtEvents")
    assert len(caught) == 1
    assert caught[0]["text"] == "Submit"
    assert caught[0]["reason"] == "submit"
    assert "ts" in caught[0]


@pytest.mark.asyncio
async def test_payment_and_logout_links_blocked(browser_context: BrowserContext):
    """Navigation links to payment gateways and logout URLs are blocked."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()

    html = (
        "data:text/html;charset=utf-8,"
        "<a id='link_pay' href='https://razorpay.com/pay'>Pay with Razorpay</a>"
        "<a id='link_logout' href='/logout'>Sign out</a>"
        "<button id='btn_pay_formaction' formaction='https://billdesk.com/pay'>Pay via Billdesk</button>"
    )
    await page.goto(html)

    # Click payment link
    await page.evaluate("document.querySelector('#link_pay').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 1
    assert "payment" in blocked[0]["reason"]

    # Click logout link
    await page.evaluate("document.querySelector('#link_logout').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 2
    assert "logout" in blocked[1]["reason"]

    # Click button with formaction pointing to payment gateway
    await page.evaluate("document.querySelector('#btn_pay_formaction').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 3
    assert "payment" in blocked[2]["reason"]


@pytest.mark.asyncio
async def test_keydown_space_and_enter_on_button_blocked(browser_context: BrowserContext):
    """keydown Enter and Space on a matching button are blocked while active."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()
    await page.goto(fixture_url("voter_form6.html"))

    # Focus submit button and press Space
    await page.locator("#submit_btn").focus()
    await page.keyboard.press("Space")
    await page.wait_for_timeout(100)

    blocked = await get_blocked_actions(page)
    assert len(blocked) == 1
    assert blocked[0]["reason"] == "submit"

    # Focus submit button and press Enter
    await page.locator("#submit_btn").focus()
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(100)

    blocked = await get_blocked_actions(page)
    assert len(blocked) == 2


@pytest.mark.asyncio
async def test_generic_submit_button_blocked(browser_context: BrowserContext):
    """Generic submit buttons with empty text or 'go'/'ok' are blocked."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()

    html = (
        "data:text/html;charset=utf-8,"
        "<input id='s1' type='submit' value='Go'>"
        "<input id='s2' type='submit' value='OK'>"
        "<button id='s3' type='submit'></button>"
    )
    await page.goto(html)

    await page.evaluate("document.querySelector('#s1').click()")
    await page.evaluate("document.querySelector('#s2').click()")
    await page.evaluate("document.querySelector('#s3').click()")

    blocked = await get_blocked_actions(page)
    assert len(blocked) == 3
    for b in blocked:
        assert b["reason"] == "generic submit"


@pytest.mark.asyncio
async def test_child_element_inside_button_blocked(browser_context: BrowserContext):
    """Clicking an inner icon/span inside a blocked button correctly blocks the action."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()

    html = (
        "data:text/html;charset=utf-8,"
        "<button id='btnSubmit'><i id='icon'></i><span>Submit</span></button>"
    )
    await page.goto(html)

    # Click on the inner <i> icon
    await page.evaluate("document.querySelector('#icon').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 1
    assert blocked[0]["reason"] == "submit"


@pytest.mark.asyncio
async def test_iframe_guard_protection(browser_context: BrowserContext):
    """Page guard runs inside iframes and prevents submissions within them."""
    await set_agent_active(browser_context, True)
    page = await browser_context.new_page()

    html = (
        "data:text/html;charset=utf-8,"
        "<iframe id='subframe' srcdoc='<button id=\"sub_btn\">Submit</button>'></iframe>"
    )
    await page.goto(html)
    await page.wait_for_timeout(200)

    frame = page.frame("subframe") or page.frames[1]
    assert await frame.evaluate("() => window.__agastyaAgentActive") is True

    await frame.evaluate("document.querySelector('#sub_btn').click()")
    blocked = await frame.evaluate("() => window.__agastyaBlocked")
    assert len(blocked) == 1
    assert blocked[0]["reason"] == "submit"


@pytest.mark.asyncio
async def test_install_page_guard_idempotence(browser_context: BrowserContext):
    """Calling install_page_guard multiple times on the same context is safe."""
    # Already called in fixture; calling again should be a clean no-op
    await install_page_guard(browser_context)
    await install_page_guard(browser_context)

    page = await browser_context.new_page()
    await set_agent_active(browser_context, True)
    await page.goto(fixture_url("pan_49a.html"))

    await page.evaluate("document.querySelector('#submit_btn').click()")
    blocked = await get_blocked_actions(page)
    assert len(blocked) == 1
