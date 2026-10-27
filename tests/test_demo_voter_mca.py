"""Tests for Demo Replica Portals: Voter Form 6 and MCA SPICe+ Part A.

Tests validate:
- Demo portals served under /mock/ mapping
- Voter Portal landing with 8 dense service cards
- Dependent dropdowns (Voter: state -> district -> AC; MCA: type -> class -> category -> sub-category)
- NIC Code modal search with >= 20 industrial activity rows
- 300-char counter for MCA main objects
- Proposed name availability rules (Bharat/National approval, existing company collision, available)
- ?demo_test=1 automation bridge for captcha answers
- OTP reading from .demo-sms demo phone element
- Full happy path progression for both portals
- BrowserDriver semantic snapshots and labelled fields discovery
"""

import http.server
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from typing import Generator
import pytest
from playwright.async_api import async_playwright, Browser, Page

from agastya.browser.driver import BrowserDriver, PageSnapshot

REPO_ROOT = Path(__file__).parent.parent.resolve()
DEMO_PORTALS_DIR = REPO_ROOT / "demo_portals"


def find_free_port() -> int:
    """Find an available TCP port for the demo HTTP server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def demo_server() -> Generator[str, None, None]:
    """Start a local HTTP server in a temp directory with a symlink /mock -> demo_portals."""
    port = find_free_port()
    temp_dir = tempfile.mkdtemp(prefix="agastya_demo_srv_")
    mock_symlink = Path(temp_dir) / "mock"
    os.symlink(DEMO_PORTALS_DIR, mock_symlink, target_is_directory=True)

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port),
        lambda *args, **kwargs: QuietHandler(*args, directory=temp_dir, **kwargs),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    time.sleep(0.1)

    yield base_url

    server.shutdown()
    server.server_close()
    if mock_symlink.is_symlink():
        mock_symlink.unlink()
    os.rmdir(temp_dir)


@pytest.mark.asyncio
async def test_demo_test_flag_security(demo_server: str):
    """When ?demo_test=1 is absent, window.__demoCaptchaAnswer must NOT be set."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{demo_server}/mock/voter/login.html")
        has_answer = await page.evaluate("() => typeof window.__demoCaptchaAnswer !== 'undefined'")
        assert not has_answer, "window.__demoCaptchaAnswer must not be exposed without ?demo_test=1"

        # Now with ?demo_test=1
        await page.goto(f"{demo_server}/mock/voter/login.html?demo_test=1")
        answer = await page.evaluate("() => window.__demoCaptchaAnswer")
        assert answer and len(answer) == 5, f"Expected 5-char captcha answer, got {answer}"
        await browser.close()


@pytest.mark.asyncio
async def test_voter_landing_page(demo_server: str):
    """Validate Voter landing page has demo replica headers and 8 service cards."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{demo_server}/mock/voter/index.html")

        # Demo replica headers injected by demo.js
        title = await page.title()
        assert "[DEMO]" in title
        assert "Voters' Service Portal (Demo Replica)" in title

        warning_bar = await page.locator(".demo-warning-bar").text_content()
        assert "DEMO REPLICA — NOT A GOVERNMENT WEBSITE" in warning_bar

        # Check 8 service cards
        cards = await page.locator(".service-card").all()
        assert len(cards) == 8, f"Expected 8 service cards, found {len(cards)}"

        # Card 1 points to Form 6
        fill_link = page.locator("#link-fill-form6")
        assert await fill_link.count() == 1
        href = await fill_link.get_attribute("href")
        assert "form6" in href

        await browser.close()


@pytest.mark.asyncio
async def test_voter_dependent_dropdowns(demo_server: str):
    """Test Section A State -> District -> Assembly Constituency dependent dropdowns."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{demo_server}/mock/voter/form6.html?demo_test=1")

        state_select = page.locator("#state")
        district_select = page.locator("#district")
        ac_select = page.locator("#assembly_constituency")

        # Initially district and AC are disabled
        assert await district_select.is_disabled()
        assert await ac_select.is_disabled()

        # Select Maharashtra (MH)
        await state_select.select_option("MH")
        assert not await district_select.is_disabled()

        # District options should include Mumbai Suburban and Pune
        dist_options = await district_select.locator("option").all_text_contents()
        assert any("Mumbai Suburban" in opt for opt in dist_options)
        assert any("Pune" in opt for opt in dist_options)

        # Select Mumbai Suburban
        await district_select.select_option("mumbai_suburban")
        assert not await ac_select.is_disabled()

        # AC options should include Bandra West
        ac_options = await ac_select.locator("option").all_text_contents()
        assert any("154 - Bandra West" in opt for opt in ac_options)
        assert any("165 - Chandivali" in opt for opt in ac_options)

        # Change State to Karnataka (KA)
        await state_select.select_option("KA")
        dist_options_ka = await district_select.locator("option").all_text_contents()
        assert any("Bengaluru Urban" in opt for opt in dist_options_ka)
        assert not any("Mumbai Suburban" in opt for opt in dist_options_ka)

        await browser.close()


@pytest.mark.asyncio
async def test_voter_happy_path(demo_server: str):
    """Full happy path: Login with OTP + Captcha -> Form 6 sections A-J -> Preview -> Submit -> Ack."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # 1. Login page
        await page.goto(f"{demo_server}/mock/voter/login.html?demo_test=1")
        await page.fill("#mobile_number", "9876543210")

        # Read captcha answer via bridge
        captcha_ans = await page.evaluate("() => window.__demoCaptchaAnswer")
        assert captcha_ans, "Captcha answer not available from bridge"
        await page.fill("#login_captcha", captcha_ans)

        # Request OTP
        await page.click("#btn-request-otp")
        await page.wait_for_selector(".demo-sms")

        # Read OTP from demo phone
        otp = await page.locator(".demo-sms-otp").text_content()
        assert otp and len(otp.strip()) == 6

        await page.fill("#login_otp", otp.strip())
        await page.click("#btn-verify-login")

        # 2. Redirected to Form 6
        await page.wait_for_url("**/form6.html*")
        assert "form6.html" in page.url

        # Section A: Location
        await page.select_option("#state", "MH")
        await page.select_option("#district", "mumbai_suburban")
        await page.select_option("#assembly_constituency", "154 - Bandra West")

        # Section B: Names
        await page.fill("#first_name", "Ramesh Kumar")
        await page.fill("#surname", "Sharma")

        # Section D: Relative Details
        await page.select_option("#relative_type", "Father")
        await page.fill("#relative_name", "Suresh")
        await page.fill("#relative_surname", "Sharma")

        # Section E: Contact
        await page.fill("#mobile", "9876543210")
        await page.fill("#email", "ramesh.sharma@example.com")

        # Section F: Aadhaar
        await page.fill("#aadhaar", "987654321098")

        # Section G: Gender
        await page.check("#gender_male")

        # Section H: DOB
        await page.fill("#dob", "1998-07-20")
        await page.select_option("#dob_doc_type", "Aadhaar Card")

        # Section I: Present Address
        await page.fill("#house_no", "Flat 402, Green Heights")
        await page.fill("#street_area", "Linking Road, Bandra West")
        await page.fill("#town_village", "Mumbai")
        await page.fill("#post_office", "Bandra West PO")
        await page.fill("#pincode", "400050")
        await page.fill("#tehsil_taluka", "Bandra")
        await page.fill("#address_district", "Mumbai Suburban")
        await page.fill("#address_state", "Maharashtra")
        await page.select_option("#resident_month", "March")
        await page.fill("#resident_year", "2018")
        await page.select_option("#address_doc_type", "Water/Electricity Bill")

        # Section J: Disability (optional)
        await page.check("#disability_visual")
        await page.fill("#disability_percentage", "20")

        # Declaration
        await page.fill("#decl_village", "Mumbai")
        await page.fill("#decl_place", "Bandra")

        # Click Preview & Submit
        await page.click("#btn-preview")
        preview_sec = page.locator("#preview-section")
        assert await preview_sec.is_visible()

        preview_text = await preview_sec.text_content()
        assert "Ramesh Kumar Sharma" in preview_text
        assert "154 - Bandra West" in preview_text

        # Solve preview captcha
        submit_captcha_ans = await page.evaluate("() => window.__demoCaptchaAnswer")
        assert submit_captcha_ans, "Submit captcha answer not exposed"
        await page.fill("#submit_captcha", submit_captcha_ans)

        # Submit application
        await page.click("#btn-final-submit")

        # 3. Acknowledgement Page
        await page.wait_for_url("**/ack.html*")
        ref_badge = await page.locator("#ack-reference-number").text_content()
        assert ref_badge.startswith("F6-")
        assert len(ref_badge) >= 10

        ack_text = await page.text_content(".ack-card")
        assert "Application Submitted Successfully" in ack_text
        assert "Ramesh Kumar Sharma" in ack_text

        await browser.close()


@pytest.mark.asyncio
async def test_mca_landing_and_mega_menu(demo_server: str):
    """Test MCA landing page notices and MCA Services mega menu navigation."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{demo_server}/mock/mca/index.html")

        title = await page.title()
        assert "[DEMO]" in title
        assert "Corporate Affairs Portal (Demo Replica)" in title

        # Mega menu link to SPICe+ Part A
        spice_link = page.locator("#link-spice-part-a")
        assert await spice_link.count() == 1

        # Direct CTA button
        start_btn = page.locator("#btn-start-spice")
        assert await start_btn.count() == 1

        await browser.close()


@pytest.mark.asyncio
async def test_mca_dependent_dropdowns(demo_server: str):
    """Test MCA company type -> class -> category -> sub-category dependent dropdowns."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{demo_server}/mock/mca/spice.html?demo_test=1")

        type_sel = page.locator("#company_type")
        class_sel = page.locator("#company_class")
        cat_sel = page.locator("#company_category")
        subcat_sel = page.locator("#company_subcategory")

        # Initially disabled
        assert await class_sel.is_disabled()
        assert await cat_sel.is_disabled()
        assert await subcat_sel.is_disabled()

        # Select Private Company
        await type_sel.select_option("private")
        assert not await class_sel.is_disabled()

        await class_sel.select_option("Private")
        assert not await cat_sel.is_disabled()

        await cat_sel.select_option("Company limited by shares")
        assert not await subcat_sel.is_disabled()

        subcats = await subcat_sel.locator("option").all_text_contents()
        assert any("Non-government company" in s for s in subcats)

        await browser.close()


@pytest.mark.asyncio
async def test_mca_nic_search_modal(demo_server: str):
    """Test MCA NIC search modal contains >= 20 rows and populates division code."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{demo_server}/mock/mca/spice.html?demo_test=1")

        nic_modal = page.locator("#nic-modal")
        assert not await nic_modal.is_visible()

        # Open modal
        await page.click("#btn-search-nic")
        assert await nic_modal.is_visible()

        # Check at least 20 rows
        rows = await page.locator("#nic-table tbody tr").all()
        assert len(rows) >= 20, f"Expected >= 20 NIC rows, found {len(rows)}"

        # Search for "computer"
        await page.fill("#nic-filter-input", "computer")
        filtered_rows = await page.locator("#nic-table tbody tr").all()
        assert len(filtered_rows) >= 1

        # Select first matching row (Division 62)
        await page.click("#nic-table tbody tr:first-child button")
        assert not await nic_modal.is_visible()

        # Check values populated
        nic_code = await page.input_value("#nic_code")
        assert nic_code in ("26", "62")

        await browser.close()


@pytest.mark.asyncio
async def test_mca_name_check_rules(demo_server: str):
    """Test proposed name availability check rules: approval, unavailable, available."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{demo_server}/mock/mca/spice.html?demo_test=1")

        # 1. Rule 1: contains 'Bharat' or 'National'
        await page.fill("#proposed_name_1", "BHARAT AGRO LOGISTICS PRIVATE LIMITED")
        await page.click("#btn-check-name")
        status_1 = await page.locator("#name_1_status").text_content()
        assert "requires central government approval" in status_1.lower()

        # 1b. Rule 1 with 'National'
        await page.fill("#proposed_name_1", "NATIONAL CONSULTING LABS LIMITED")
        await page.click("#btn-check-name")
        status_1b = await page.locator("#name_1_status").text_content()
        assert "requires central government approval" in status_1b.lower()

        # 2. Rule 2: collision with existing company
        await page.fill("#proposed_name_1", "TATA CONSULTANCY SERVICES LIMITED")
        await page.click("#btn-check-name")
        status_2 = await page.locator("#name_1_status").text_content()
        assert "name not available" in status_2.lower()

        # 3. Rule 3: available name
        await page.fill("#proposed_name_1", "AGASTYA COGNITIVE ASSIST SYSTEMS PRIVATE LIMITED")
        await page.fill("#proposed_name_2", "AGASTYA INTELLIGENCE LABS PRIVATE LIMITED")
        await page.click("#btn-check-name")
        status_3a = await page.locator("#name_1_status").text_content()
        status_3b = await page.locator("#name_2_status").text_content()
        assert "name available" in status_3a.lower()
        assert "name available" in status_3b.lower()

        await browser.close()


@pytest.mark.asyncio
async def test_mca_objects_char_counter(demo_server: str):
    """Test 300-char counter for main objects description."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{demo_server}/mock/mca/spice.html?demo_test=1")

        counter = page.locator("#objects-char-counter")
        assert "300 characters remaining" in await counter.text_content()

        await page.fill("#main_objects", "A" * 50)
        assert "250 characters remaining" in await counter.text_content()

        await browser.close()


@pytest.mark.asyncio
async def test_mca_happy_path(demo_server: str):
    """Full happy path: SPICe+ Part A -> Check name -> Auto-check -> Captcha+OTP -> Ack SRN-… with fee ₹1000."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{demo_server}/mock/mca/spice.html?demo_test=1")

        # 1. Classification
        await page.select_option("#company_type", "private")
        await page.select_option("#company_class", "Private")
        await page.select_option("#company_category", "Company limited by shares")
        await page.select_option("#company_subcategory", "Non-government company")

        # 2. Activity & Objects
        await page.fill("#nic_code", "62")
        await page.fill("#nic_description", "Computer programming, consultancy and related activities")
        await page.fill("#main_objects", "To provide artificial intelligence and voice assistive automation services to citizens.")

        # 3. Proposed Names & Check
        await page.fill("#proposed_name_1", "AGASTYA AUTOMATION PRIVATE LIMITED")
        await page.fill("#proposed_name_2", "AGASTYA SYSTEMS PRIVATE LIMITED")
        await page.click("#btn-check-name")

        status_text = await page.locator("#name_1_status").text_content()
        assert "name available" in status_text.lower()

        # 4. Applicant Details
        await page.select_option("#applicant_id_type", "DIN")
        await page.fill("#applicant_id_number", "09876543")
        await page.fill("#applicant_name", "Ramesh Kumar Sharma")
        await page.fill("#applicant_email", "ramesh.sharma@example.com")
        await page.fill("#applicant_mobile", "9876543210")

        # 5. Auto-check
        await page.click("#btn-auto-check")
        autocheck = await page.locator("#autocheck-status").text_content()
        assert "Auto-check Passed" in autocheck

        # 6. Submit for name reservation (triggers Captcha + OTP modal)
        await page.click("#btn-submit-name")
        modal = page.locator("#submission-modal")
        assert await modal.is_visible()

        # Read OTP from demo phone
        await page.wait_for_selector(".demo-sms")
        otp = await page.locator(".demo-sms-otp").text_content()
        assert otp and len(otp.strip()) == 6
        await page.fill("#spice_otp", otp.strip())

        # Read Captcha from bridge
        captcha_ans = await page.evaluate("() => window.__demoCaptchaAnswer")
        assert captcha_ans and len(captcha_ans) == 5
        await page.fill("#spice_captcha", captcha_ans)

        # Confirm submission
        await page.click("#btn-confirm-submit")

        # 7. Acknowledgement Page
        await page.wait_for_url("**/ack.html*")
        srn_badge = await page.locator("#ack-srn-badge").text_content()
        assert srn_badge.startswith("SRN-")
        assert len(srn_badge) >= 10

        ack_text = await page.text_content(".ack-card")
        assert "Company Name Reservation Submitted" in ack_text
        assert "₹1000 (demo)" in ack_text
        assert "AGASTYA AUTOMATION PRIVATE LIMITED" in ack_text

        await browser.close()


@pytest.mark.asyncio
async def test_browser_driver_snapshot_fields(demo_server: str):
    """BrowserDriver semantic snapshot must list labelled fields on both replica portals."""
    driver = BrowserDriver(headless=True)
    await driver.start()
    try:
        # Snapshot on Voter Form 6
        voter_snap = await driver.goto(f"{demo_server}/mock/voter/form6.html?demo_test=1")
        assert isinstance(voter_snap, PageSnapshot)
        assert len(voter_snap.fields) > 10

        field_names = {f.name: f for f in voter_snap.fields}
        assert "state" in field_names
        assert "State" in field_names["state"].label
        assert "first_name" in field_names
        assert "First Name" in field_names["first_name"].label
        assert "mobile" in field_names
        assert "Mobile" in field_names["mobile"].label

        # Snapshot on MCA SPICe+ Part A
        mca_snap = await driver.goto(f"{demo_server}/mock/mca/spice.html?demo_test=1")
        assert isinstance(mca_snap, PageSnapshot)
        assert len(mca_snap.fields) > 8

        mca_fields = {f.name: f for f in mca_snap.fields}
        assert "company_type" in mca_fields
        assert "Type of Company" in mca_fields["company_type"].label
        assert "proposed_name_1" in mca_fields
        assert "Proposed Name 1" in mca_fields["proposed_name_1"].label
        assert "main_objects" in mca_fields
        assert "Main Objects" in mca_fields["main_objects"].label

    finally:
        await driver.close()
