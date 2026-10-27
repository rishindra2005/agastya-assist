"""Tests for Income Tax e-Filing (Demo Replica) — ITR-1 filing with Form 16.

Validates:
- Demo portal replica serving under /mock/itr/
- Semantic page snapshots with labelled fields via BrowserDriver
- Landing page with due-date banner, quick links, tax calendar, 'Know your AO'
- Login step 1 (User ID + Captcha) & step 2 (Secure Access Message + Password) + OTP
- ?demo_test=1 support exposing window.__demoCaptchaAnswer for automated tests
- Dashboard profile completion, pending actions, and filing wizard progression
- Tabbed ITR-1 form with per-tab confirmation
- Live tax liability calculation for known Form 16 inputs under AY 2026-27
- Regime switch greying out / disabling Chapter VI-A deductions (80C, 80D, 80TTA)
- Preview, declaration check, validation, and e-verification with simulated Aadhaar OTP
- Acknowledgement page with 15-digit acknowledgement number and computed refund/payable
- Safety guard enforcement on login, OTP, and verification actions
"""

from __future__ import annotations

import functools
import http.server
from pathlib import Path
import socket
import threading
from typing import Generator
import pytest

from agastya.browser.driver import BlockedActionError, BrowserDriver, PageSnapshot

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_PORTALS_DIR = REPO_ROOT / "demo_portals"


def find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_server(tmp_path_factory) -> Generator[str, None, None]:
    """Serve demo_portals directory at /mock/ on a free port."""
    temp_dir = tmp_path_factory.mktemp("server_root")
    mock_symlink = temp_dir / "mock"
    mock_symlink.symlink_to(DEMO_PORTALS_DIR.resolve(), target_is_directory=True)

    port = find_free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(temp_dir))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}/mock/itr/"
    yield base_url

    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
async def driver():
    """Async BrowserDriver instance for test execution."""
    d = BrowserDriver(headless=True)
    await d.start()
    try:
        yield d
    finally:
        await d.close()


@pytest.mark.asyncio
async def test_landing_page_dense_elements_and_safety_guard(driver: BrowserDriver, mock_server: str):
    """Landing page contains quick links, tax calendar, due-date banner, and blocks agent login click."""
    url = f"{mock_server}index.html?demo_test=1"
    snap = await driver.goto(url)

    assert isinstance(snap, PageSnapshot)
    assert "[DEMO]" in snap.title
    assert "Income Tax" in snap.title

    # Text content checks
    assert "Due date" in snap.text or "31st July 2026" in snap.text
    assert "Tax Calendar" in snap.text
    assert "Quick Services" in snap.text
    assert "Know your AO" in snap.text

    # Actions discoverable
    action_texts = [a["text"] for a in snap.actions]
    assert any("Login" in t for t in action_texts)
    assert any("File Now" in t or "File Income Tax Return" in t for t in action_texts)

    # Verify safety guard blocks agent from clicking Login
    login_action = next(a for a in snap.actions if a["text"].strip() == "Login")
    with pytest.raises(BlockedActionError) as exc_info:
        await driver.click(login_action["ref"])
    assert "login" in exc_info.value.decision.reason


@pytest.mark.asyncio
async def test_login_flow_with_captcha_and_otp(driver: BrowserDriver, mock_server: str):
    """Login flow: step 1 User ID + Captcha with ?demo_test=1, step 2 password, OTP verification."""
    url = f"{mock_server}login.html?demo_test=1"
    snap = await driver.goto(url)

    # Check labelled fields in Step 1
    field_by_name = {f.name: f for f in snap.fields}
    assert "user_id" in field_by_name
    assert "login_captcha" in field_by_name

    # Captcha answer is exposed to automation via window.__demoCaptchaAnswer ONLY when demo_test=1
    captcha_answer = await driver.page.evaluate("() => window.__demoCaptchaAnswer")
    assert captcha_answer is not None
    assert len(captcha_answer) == 5

    # Fill User ID and Captcha
    user_id_ref = field_by_name["user_id"].ref
    captcha_ref = field_by_name["login_captcha"].ref
    await driver.fill(user_id_ref, "ABCDE1234F")
    await driver.fill(captcha_ref, captcha_answer)

    # Click Continue (allowed by guard)
    continue_action = next(a for a in snap.actions if "Continue" in a["text"])
    await driver.click(continue_action["ref"])

    # Step 2 is now active
    step2_visible = await driver.page.is_visible("#login_step_2")
    assert step2_visible is True

    # Safety: clicking 'Login' button is blocked by safety guard for agent
    snap2 = await driver.snapshot()
    login_action = next(a for a in snap2.actions if a["text"].strip() == "Login")
    with pytest.raises(BlockedActionError) as exc_login:
        await driver.click(login_action["ref"])
    assert "login" in exc_login.value.decision.reason

    # Citizen takes over in live view: ticks secure access message & enters password
    await driver.page.check("#secure_message_confirm")
    await driver.page.fill("#password", "SecurePass123")
    await driver.page.click("#btn_login")

    # Step 3 OTP verification is displayed and mock SMS phone pops up
    step3_visible = await driver.page.is_visible("#login_step_3")
    assert step3_visible is True

    # Read OTP from .demo-sms .demo-sms-otp
    await driver.page.wait_for_selector(".demo-sms .demo-sms-otp", timeout=3000)
    otp_text = (await driver.page.inner_text(".demo-sms .demo-sms-otp")).strip()
    assert len(otp_text) == 6
    assert otp_text.isdigit()

    # Enter OTP and submit
    await driver.page.fill("#login_otp", otp_text)
    await driver.page.click("#btn_submit_login_otp")

    # Navigates to dashboard
    await driver.page.wait_for_url("**/dashboard.html*", timeout=4000)
    assert "dashboard.html" in driver.page.url


@pytest.mark.asyncio
async def test_dashboard_filing_wizard_progression(driver: BrowserDriver, mock_server: str):
    """Dashboard displays profile completion %, pending actions, and filing wizard reaches ITR-1."""
    url = f"{mock_server}dashboard.html?demo_test=1"
    snap = await driver.goto(url)

    assert "Dashboard" in snap.title
    assert "85% Completed" in snap.text or "85%" in snap.text
    assert "Pending Actions" in snap.text

    # Safety: clicking 'File Now' is blocked for agent
    file_now_action = next(a for a in snap.actions if "File Now" in a["text"])
    with pytest.raises(BlockedActionError) as exc_file:
        await driver.click(file_now_action["ref"])
    assert "file return" in exc_file.value.decision.reason

    # Citizen clicks 'File Now'
    await driver.page.click("#btn_file_now")
    assert await driver.page.is_visible("#filing_wizard_container")

    # Step 1: AY 2026-27 & Mode Online
    ay_val = await driver.page.input_value("#assessment_year")
    assert ay_val == "2026-27"
    await driver.page.click("#btn_wizard_step1_continue")

    # Step 2: Status Individual
    assert await driver.page.is_checked("#status_individual")
    await driver.page.click("#btn_wizard_step2_continue")

    # Step 3: Select ITR-1
    itr_val = await driver.page.input_value("#itr_type")
    assert "ITR-1" in itr_val
    await driver.page.click("#btn_proceed_itr1")

    # Step 4: Let's Get Started
    assert await driver.page.is_visible("#btn_get_started")
    await driver.page.click("#btn_get_started")

    # Navigates to filing.html
    await driver.page.wait_for_url("**/filing.html*", timeout=4000)
    assert "filing.html" in driver.page.url


@pytest.mark.asyncio
async def test_itr1_filing_happy_path_and_tax_computation(driver: BrowserDriver, mock_server: str):
    """ITR-1 filing happy path computes exact tax liabilities for known Form 16 inputs."""
    url = f"{mock_server}filing.html?demo_test=1"
    snap = await driver.goto(url)

    # Check labelled fields on Personal Information tab
    field_by_name = {f.name: f for f in snap.fields}
    assert "taxpayer_name" in field_by_name
    assert "pan" in field_by_name
    assert "dob" in field_by_name
    assert "nature_of_employment" in field_by_name
    assert "opt_out_regime" in field_by_name

    # Switch to Gross Total Income tab
    await driver.page.click("#tab_btn_income")
    snap_income = await driver.snapshot()
    income_fields = {f.name: f for f in snap_income.fields}
    assert "salary_17_1" in income_fields
    assert "perquisites_17_2" in income_fields
    assert "profits_17_3" in income_fields
    assert "professional_tax_16iii" in income_fields
    assert "interest_savings" in income_fields

    # Set known Form 16 test figures:
    # 17(1) = 1,250,000, 17(2) = 25,000, 17(3) = 0 -> Gross Salary = 1,275,000
    # Standard deduction = 75,000 (New Regime)
    # Professional tax = 2,500
    # Savings interest = 10,000
    # TDS = 90,000
    await driver.page.fill("#salary_17_1", "1250000")
    await driver.page.fill("#perquisites_17_2", "25000")
    await driver.page.fill("#profits_17_3", "0")
    await driver.page.fill("#professional_tax_16iii", "2500")
    await driver.page.fill("#interest_savings", "10000")

    # Set TDS on Tax Paid tab
    await driver.page.click("#tab_btn_taxes")
    await driver.page.fill("#tds_tan_0", "DELD12345E")
    await driver.page.fill("#tds_employer_name_0", "Demo Infotech Private Limited")
    await driver.page.fill("#tds_amount_0", "90000")

    # Switch to Total Tax Liability tab
    await driver.page.click("#tab_btn_liability")

    # Read computed values directly from DOM elements
    gti_val = int(await driver.page.input_value("#gross_total_income"))
    tax_liability_val = int(await driver.page.input_value("#tax_liability_total"))
    refund_val = int(await driver.page.input_value("#refund_amount"))
    payable_val = int(await driver.page.input_value("#tax_payable_amount"))

    # Expected values under New Tax Regime (AY 2026-27):
    # Gross Salary: 1,275,000
    # Standard deduction: 75,000
    # Professional tax: 2,500
    # Salary Chargeable: 1,197,500
    # Savings interest: 10,000
    # Gross Total Income: 1,207,500
    # Taxable Income: 1,207,500
    # Slabs:
    # 0 - 3L: 0
    # 3L - 7L: 20,000
    # 7L - 10L: 30,000
    # 10L - 12L: 30,000
    # 12L - 12.075L (7500 @ 20%): 1,500
    # Sum tax before cess = 81,500
    # Cess @ 4% = 3,260
    # Total Tax Liability = 84,760
    # TDS = 90,000
    # Expected Refund = 90,000 - 84,760 = 5,240!
    assert gti_val == 1207500
    assert tax_liability_val == 84760
    assert refund_val == 5240
    assert payable_val == 0

    # Spoken comparison sentence check
    comparison_text = await driver.page.inner_text("#comparison_sentence")
    assert "refund of ₹5,240" in comparison_text
    assert "old regime you would have to pay ₹41,820" in comparison_text
    assert "saves you ₹47,060" in comparison_text


@pytest.mark.asyncio
async def test_regime_switch_greys_80c_and_recomputes(driver: BrowserDriver, mock_server: str):
    """Regime switch: New Regime greys 80C, switching to Old Regime enables 80C and changes computation."""
    url = f"{mock_server}filing.html?demo_test=1"
    await driver.goto(url)

    # In default New Regime:
    # Check that 80C, 80D, 80TTA are disabled and have greyed-out class
    await driver.page.click("#tab_btn_deductions")
    is_80c_disabled = await driver.page.is_disabled("#deduction_80c")
    is_80d_disabled = await driver.page.is_disabled("#deduction_80d")
    is_80tta_disabled = await driver.page.is_disabled("#deduction_80tta")
    classes_80c = await driver.page.get_attribute("#deduction_80c", "class")

    assert is_80c_disabled is True
    assert is_80d_disabled is True
    assert is_80tta_disabled is True
    assert "greyed-out" in (classes_80c or "")

    # Standard deduction in New Regime is 75,000
    await driver.page.click("#tab_btn_income")
    std_ded_new = int(await driver.page.input_value("#standard_deduction_16ia"))
    assert std_ded_new == 75000

    # Switch to Old Tax Regime (opt out = 'yes')
    await driver.page.click("#tab_btn_personal")
    await driver.page.click("#opt_out_yes")

    # In Old Regime:
    # Standard deduction automatically updates to 50,000
    await driver.page.click("#tab_btn_income")
    std_ded_old = int(await driver.page.input_value("#standard_deduction_16ia"))
    assert std_ded_old == 50000

    # 80C is now ENABLED and NOT greyed out
    await driver.page.click("#tab_btn_deductions")
    is_80c_disabled_old = await driver.page.is_disabled("#deduction_80c")
    classes_80c_old = await driver.page.get_attribute("#deduction_80c", "class")

    assert is_80c_disabled_old is False
    assert "greyed-out" not in (classes_80c_old or "")

    # Fill deductions: 80C = 150000, 80D = 25000, 80TTA = 10000
    await driver.page.fill("#deduction_80c", "150000")
    await driver.page.fill("#deduction_80d", "25000")
    await driver.page.fill("#deduction_80tta", "10000")

    # Check Old Regime Tax computation on Liability tab:
    # Gross Salary: 1,275,000
    # Standard deduction: 50,000
    # Professional tax: 2,500
    # Salaries chargeable: 1,222,500
    # Other sources: 10,000
    # GTI: 1,232,500
    # Chapter VI-A deductions: 185,000
    # Taxable Income: 1,047,500
    # Slabs:
    # 0 - 2.5L: 0
    # 2.5L - 5L: 12,500
    # 5L - 10L: 1,00,000
    # 10L - 10.475L (47,500 @ 30%): 14,250
    # Sum tax before cess = 1,26,750
    # Cess @ 4% = 5,070
    # Total Tax Liability (Old Regime) = 1,31,820
    # TDS = 90,000 -> Tax Payable = 41,820!
    await driver.page.click("#tab_btn_liability")
    old_liability_val = int(await driver.page.input_value("#tax_liability_total"))
    old_payable_val = int(await driver.page.input_value("#tax_payable_amount"))
    old_refund_val = int(await driver.page.input_value("#refund_amount"))

    assert old_liability_val == 131820
    assert old_payable_val == 41820
    assert old_refund_val == 0


@pytest.mark.asyncio
async def test_preview_validation_and_everify_to_ack(driver: BrowserDriver, mock_server: str):
    """Preview summary, declaration validation, Aadhaar OTP e-verify, and 15-digit acknowledgement."""
    url = f"{mock_server}preview.html?demo_test=1"
    snap = await driver.goto(url)

    assert "Preview" in snap.title
    assert "TEST KUMAR DEMO" in snap.text
    assert "ABCDE1234F" in snap.text

    # Citizen ticks declaration checkbox
    await driver.page.check("#declaration_agree")
    await driver.page.click("#btn_proceed_validation")

    # Validation succeeds and shows Proceed to Verification
    assert await driver.page.is_visible("#validation_success_box")

    # Safety: clicking 'Proceed to Verification' via driver.click is blocked by guard
    snap_after_val = await driver.snapshot()
    verif_action = next(a for a in snap_after_val.actions if "Proceed to Verification" in a["text"])
    with pytest.raises(BlockedActionError) as exc_verif:
        await driver.click(verif_action["ref"])
    assert "verification" in exc_verif.value.decision.reason

    # Citizen clicks Proceed to Verification in live view
    await driver.page.click("#btn_proceed_verification")

    # Verification panel appears
    assert await driver.page.is_visible("#verification_panel")
    await driver.page.click("#btn_continue_verification")

    # Aadhaar OTP simulated phone pops up
    await driver.page.wait_for_selector(".demo-sms .demo-sms-otp", timeout=3000)
    everify_otp = (await driver.page.inner_text(".demo-sms .demo-sms-otp")).strip()
    assert len(everify_otp) == 6

    # Safety: clicking 'e-Verify' via driver.click is blocked by guard
    snap_otp = await driver.snapshot()
    everify_action = next(a for a in snap_otp.actions if "e-Verify" in a["text"])
    with pytest.raises(BlockedActionError) as exc_everify:
        await driver.click(everify_action["ref"])
    assert "e-verify" in exc_everify.value.decision.reason

    # Citizen enters OTP and clicks e-Verify in live view
    await driver.page.fill("#everify_otp", everify_otp)
    await driver.page.click("#btn_everify_submit")

    # Navigates to ack.html
    await driver.page.wait_for_url("**/ack.html*", timeout=4000)
    assert "ack.html" in driver.page.url

    # Acknowledgement page displays 15-digit ack number and computed refund
    ack_number = (await driver.page.inner_text("#ack_number")).strip()
    assert len(ack_number) == 15
    assert ack_number.isdigit()

    ack_text = await driver.page.inner_text(".ack-box")
    assert "Return Successfully Filed and e-Verified" in ack_text
    assert "TEST KUMAR DEMO" in ack_text
    assert "₹5,240" in ack_text or "5,240" in ack_text
