"""Tests for PAN Application Portal (Demo Replica) and Form 49A flow."""

import asyncio
import http.server
from pathlib import Path
import re
import socket
import tempfile
import threading
import pytest

from agastya.browser.driver import BrowserDriver

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_PORTALS_DIR = REPO_ROOT / "demo_portals"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def mock_server():
    """Serve demo_portals mapped under /mock/ on an ephemeral localhost port."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir)
        mock_link = temp_path / "mock"
        mock_link.symlink_to(DEMO_PORTALS_DIR)

        port = _find_free_port()
        handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(
            *args, directory=str(temp_path), **kwargs
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{port}"
        yield base_url

        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_demo_portals_index_loads(mock_server: str):
    """Verify demo_portals/index.html lists all 4 replicas and disclaimer."""
    driver = BrowserDriver(headless=True)
    await driver.start()
    try:
        snap = await driver.goto(f"{mock_server}/mock/index.html")
        assert "Demo Replica" in snap.title or "Agastya Assist" in snap.title
        # All 4 replicas listed
        assert "PAN Application Portal (Demo Replica)" in snap.text
        assert "Income Tax e-Filing Portal (Demo Replica)" in snap.text
        assert "Voters' Service Portal (Demo Replica)" in snap.text
        assert "Ministry of Corporate Affairs Portal (Demo Replica)" in snap.text
        # Disclaimer present
        assert "NOT official government portals" in snap.text or "fictional" in snap.text.lower()
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_pan_landing_page_loads(mock_server: str):
    """Verify PAN replica landing page loads with news ticker, 6 tiles, and accordion."""
    driver = BrowserDriver(headless=True)
    await driver.start()
    try:
        snap = await driver.goto(f"{mock_server}/mock/pan/index.html")
        assert "PAN Application Portal" in snap.title
        # Warning banner
        assert "NOT A GOVERNMENT WEBSITE" in snap.text
        # News ticker
        assert "LATEST ALERTS" in snap.text or "NOTICE" in snap.text
        # 6 Service tiles
        assert "New PAN - Indian Citizen (Form 49A)" in snap.text
        assert "New PAN - Foreign Citizen (Form 49AA)" in snap.text
        assert "Changes or Correction in PAN Data" in snap.text
        assert "Reprint of PAN Card" in snap.text
        assert "Know Status of Application" in snap.text
        assert "Aadhaar-based Instant e-PAN" in snap.text
        # Accordion instructions
        assert "Important Instructions" in snap.text
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_registration_labelled_fields(mock_server: str):
    """Verify registration page has all expected labelled form fields."""
    driver = BrowserDriver(headless=True)
    await driver.start()
    try:
        snap = await driver.goto(f"{mock_server}/mock/pan/register.html")
        field_names = {f.name for f in snap.fields}
        expected_names = {
            "application_type",
            "category",
            "applicant_title",
            "last_name",
            "first_name",
            "middle_name",
            "date_of_birth",
            "email",
            "mobile",
            "is_citizen",
            "consent",
            "captcha_input",
        }
        for name in expected_names:
            assert name in field_names, f"Missing expected field: {name}"

        # Every field must have a non-empty semantic label
        for f in snap.fields:
            if f.name in expected_names:
                assert f.label.strip(), f"Field {f.name} lacks label"
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_registration_consent_required(mock_server: str):
    """Verify registration fails if Indian citizen applicant does not check consent."""
    driver = BrowserDriver(headless=True)
    await driver.start()
    try:
        await driver.goto(f"{mock_server}/mock/pan/register.html?demo_test=1")
        page = driver.page

        # Fill details but leave consent unchecked
        await page.fill("#last_name", "VERMA")
        await page.fill("#date_of_birth", "1992-04-10")
        await page.fill("#email", "verma@example.com")
        await page.fill("#mobile", "9876543210")

        # Fill captcha using test mode answer
        captcha_val = await page.evaluate("window.__demoCaptchaAnswer")
        assert captcha_val and len(captcha_val) == 5
        await page.fill("#captcha_input", captcha_val)

        # Ensure consent is unchecked
        await page.uncheck("#consent")

        # Click submit button
        await page.click("#btn-submit")

        # Modal should NOT be shown
        modal_visible = await page.evaluate(
            "document.getElementById('token-modal').classList.contains('active')"
        )
        assert not modal_visible

        # Error banner should be visible with consent requirement message
        error_text = await page.locator("#error-banner").inner_text()
        assert "Consent checkbox is required" in error_text
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_captcha_test_mode_flag(mock_server: str):
    """Verify window.__demoCaptchaAnswer is defined ONLY when ?demo_test=1 is provided."""
    driver = BrowserDriver(headless=True)
    await driver.start()
    try:
        # Without ?demo_test=1
        await driver.goto(f"{mock_server}/mock/pan/register.html")
        answer_normal = await driver.page.evaluate("window.__demoCaptchaAnswer")
        assert answer_normal is None or answer_normal == ""

        # With ?demo_test=1
        await driver.goto(f"{mock_server}/mock/pan/register.html?demo_test=1")
        answer_test = await driver.page.evaluate("window.__demoCaptchaAnswer")
        assert isinstance(answer_test, str)
        assert len(answer_test) == 5
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_form49a_tabs_and_labelled_fields(mock_server: str):
    """Verify Form 49A tabs switch and list labelled fields on each tab."""
    driver = BrowserDriver(headless=True)
    await driver.start()
    try:
        snap1 = await driver.goto(f"{mock_server}/mock/pan/form49a.html?tab=personal")
        names_tab1 = {f.name for f in snap1.fields}
        assert "name_on_card" in names_tab1
        assert "gender" in names_tab1
        assert "father_last_name" in names_tab1

        snap2 = await driver.goto(f"{mock_server}/mock/pan/form49a.html?tab=contact")
        names_tab2 = {f.name for f in snap2.fields}
        assert "residence_flat" in names_tab2
        assert "residence_city" in names_tab2
        assert "residence_state" in names_tab2
        assert "residence_pincode" in names_tab2
        assert "ao_area_code" in names_tab2
        assert "ao_type" in names_tab2
        assert "ao_range_code" in names_tab2
        assert "ao_number" in names_tab2

        snap3 = await driver.goto(f"{mock_server}/mock/pan/form49a.html?tab=documents")
        names_tab3 = {f.name for f in snap3.fields}
        assert "proof_identity" in names_tab3
        assert "proof_address" in names_tab3
        assert "proof_dob" in names_tab3
        assert "declaration_place" in names_tab3
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_ao_code_search_modal(mock_server: str):
    """Verify AO code search modal opens, filters by city, and populates fields on selection."""
    driver = BrowserDriver(headless=True)
    await driver.start()
    try:
        await driver.goto(f"{mock_server}/mock/pan/form49a.html?tab=contact")
        page = driver.page

        # Open AO Code Modal
        await page.click("#btn-search-ao")
        modal_active = await page.evaluate(
            "document.getElementById('ao-modal').classList.contains('active')"
        )
        assert modal_active

        # Search for Bengaluru
        await page.fill("#ao_search_input", "Bengaluru")
        await page.wait_for_timeout(100)

        # Select first matching row
        await page.click("#ao-tbody tr button")

        # Modal should be closed
        modal_closed = await page.evaluate(
            "!document.getElementById('ao-modal').classList.contains('active')"
        )
        assert modal_closed

        # Verify AO fields populated with BLR
        area_code = await page.input_value("#ao_area_code")
        ao_type = await page.input_value("#ao_type")
        range_code = await page.input_value("#ao_range_code")
        ao_number = await page.input_value("#ao_number")

        assert area_code == "BLR"
        assert ao_type in ("W", "C")
        assert range_code in ("301", "302")
        assert ao_number in ("1", "2")
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_happy_path_end_to_end(mock_server: str):
    """Verify complete end-to-end happy path flow with ?demo_test=1:

    Landing -> Registration -> Form 49A tabs (AO modal) -> Preview ->
    Payment (₹107) -> Send OTP (mock SMS) -> Authenticate -> Acknowledgement (15-digit number & name).
    """
    driver = BrowserDriver(headless=True)
    await driver.start()
    try:
        page = driver.page

        # 1. Landing
        await driver.goto(f"{mock_server}/mock/pan/index.html?demo_test=1")
        await page.click("#apply-online-49a")

        # 2. Registration
        assert "register.html" in page.url
        await page.fill("#last_name", "SHARMA")
        await page.fill("#first_name", "RAJESH")
        await page.fill("#middle_name", "KUMAR")
        await page.fill("#date_of_birth", "1988-06-20")
        await page.fill("#email", "rajesh.sharma@example.com")
        await page.fill("#mobile", "9876543210")
        await page.check("#consent")

        # Captcha in test mode
        captcha_val = await page.evaluate("window.__demoCaptchaAnswer")
        assert captcha_val
        await page.fill("#captcha_input", captcha_val)

        # Submit registration
        await page.click("#btn-submit")

        # Token modal shown
        await page.wait_for_selector("#token-modal.active", timeout=3000)
        token_num = await page.locator("#token-number").inner_text()
        assert token_num and len(token_num) >= 8

        # Continue with Form 49A
        await page.click("#btn-continue-form")

        # 3. Form 49A - Tab 1 (Personal Details)
        await page.wait_for_url(re.compile(r"form49a\.html"))
        name_on_card = await page.input_value("#name_on_card")
        assert "RAJESH" in name_on_card.upper()
        await page.fill("#father_last_name", "SHARMA")
        await page.fill("#father_first_name", "OM")
        await page.click("#btn-next-tab1")

        # Tab 2 (Contact & Other Details)
        await page.wait_for_selector("#tab-contact.active", timeout=3000)
        await page.fill("#residence_flat", "Flat 301, Tower A")
        await page.fill("#residence_premises", "Lotus Enclave")
        await page.fill("#residence_road", "Outer Ring Road")
        await page.fill("#residence_area", "Bellandur")
        await page.fill("#residence_city", "Bengaluru")
        await page.select_option("#residence_state", "Karnataka")
        await page.fill("#residence_pincode", "560103")

        # AO Code search
        await page.click("#btn-search-ao")
        await page.wait_for_selector("#ao-modal.active", timeout=3000)
        await page.fill("#ao_search_input", "Bengaluru")
        await page.wait_for_timeout(100)
        await page.click("#ao-tbody tr button")
        await page.wait_for_timeout(100)

        # Next to Tab 3
        await page.click("#btn-next-tab2")

        # Tab 3 (Document Details)
        await page.wait_for_selector("#tab-documents.active", timeout=3000)
        await page.fill("#declaration_place", "Bengaluru")

        # Proceed to Preview
        await page.click("#btn-proceed-preview")

        # 4. Preview Page
        await page.wait_for_url(re.compile(r"preview\.html"), timeout=4000)
        preview_text = await page.inner_text("main")
        assert "RAJESH KUMAR SHARMA" in preview_text
        assert "560103" in preview_text
        assert "Bengaluru" in preview_text

        # Proceed to Payment
        await page.click("#btn-proceed-pay")

        # 5. Payment Page
        await page.wait_for_url(re.compile(r"payment\.html"), timeout=4000)
        pay_text = await page.inner_text("main")
        assert "Demo payment — no money moves" in pay_text
        assert "₹107.00" in pay_text

        # Click Mock Pay
        await page.click("#btn-mock-pay")
        await page.wait_for_selector("#esign-card", state="visible", timeout=3000)

        # Click Send OTP
        await page.click("#btn-send-otp")
        await page.wait_for_selector(".demo-sms", timeout=3000)

        # Read OTP from .demo-sms element
        otp_elem = page.locator(".demo-sms-otp")
        otp_text = (await otp_elem.inner_text()).strip()
        assert re.match(r"^\d{6}$", otp_text)

        # Enter OTP
        await page.fill("#otp_input", otp_text)

        # Authenticate & Submit
        await page.click("#btn-authenticate-submit")

        # 6. Acknowledgement Page
        await page.wait_for_url(re.compile(r"ack\.html"), timeout=4000)
        ack_text = await page.inner_text(".ack-card")

        # Verify applicant name is shown
        assert "RAJESH KUMAR SHARMA" in ack_text

        # Verify 15-digit acknowledgement number
        ack_elem = page.locator("#ack-number")
        ack_num = (await ack_elem.inner_text()).strip()
        assert re.match(r"^\d{15}$", ack_num), f"Expected 15-digit ack number, got {ack_num}"

        # Verify print download button
        assert await page.is_visible("#btn-download-ack")
    finally:
        await driver.close()
