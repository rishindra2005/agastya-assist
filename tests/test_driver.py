"""Tests for Playwright browser driver (agastya/browser/driver.py).

Validates:
- Semantic snapshots and field/action ref discovery on pan_49a.html and other fixtures
- Robust label resolution (label for, wrapping label, aria-label, placeholder, preceding text)
- Fill and select round-trip (read value back via page.evaluate)
- Radio button group filling
- Date format conversion (DD/MM/YYYY -> YYYY-MM-DD)
- Checkbox single and group filling
- Multi-step form progression (clicking 'Next' works and preserves ref numbering on 'Back')
- Safety guard enforcement: clicking 'Submit', 'Pay Now', 'Send OTP', 'e-Verify'
  raises BlockedActionError and the URL never navigates to form_success.html
- Navigation safety guard on goto()
- Screenshot returns valid PNG bytes
- Helpful ValueError on unknown refs
"""

from pathlib import Path
import pytest

from agastya.browser.driver import BlockedActionError, BrowserDriver, FieldInfo, PageSnapshot

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
async def test_snapshot_pan_49a_fields(driver: BrowserDriver):
    """Snapshot finds all visible labelled fields and actions on pan_49a.html."""
    url = fixture_url("pan_49a.html")
    snap = await driver.goto(url)

    assert isinstance(snap, PageSnapshot)
    assert snap.url == url
    assert "PAN" in snap.title
    assert len(snap.text) > 0
    assert len(snap.text) <= 6000

    field_by_name = {f.name: f for f in snap.fields}

    # Verify standard labelled fields
    assert "application_type" in field_by_name
    assert field_by_name["application_type"].tag == "select"
    assert "Application Type" in field_by_name["application_type"].label
    assert field_by_name["application_type"].required is True

    assert "category" in field_by_name
    assert field_by_name["category"].tag == "select"
    assert "Category" in field_by_name["category"].label
    assert any("INDIVIDUAL" in opt for opt in field_by_name["category"].options)

    # Radio group for title
    assert "title" in field_by_name
    title_field = field_by_name["title"]
    assert title_field.type == "radio"
    assert "Title" in title_field.label
    assert "Shri" in title_field.options
    assert "Smt" in title_field.options
    assert "Kumari" in title_field.options

    # Text fields with <label for=>
    assert "last_name" in field_by_name
    assert "Last Name" in field_by_name["last_name"].label
    assert field_by_name["last_name"].required is True

    assert "first_name" in field_by_name
    assert "First Name" in field_by_name["first_name"].label

    # Robustness: fields with only aria-label / placeholder
    assert "middle_name" in field_by_name
    assert field_by_name["middle_name"].label == "Middle Name"
    assert field_by_name["middle_name"].required is False

    assert "aadhaar" in field_by_name
    assert "Aadhaar" in field_by_name["aadhaar"].label
    assert field_by_name["aadhaar"].required is True

    # Date field
    assert "dob" in field_by_name
    assert field_by_name["dob"].type == "date"
    assert "Date of Birth" in field_by_name["dob"].label

    # Contact & Address fields
    assert "mobile" in field_by_name
    assert field_by_name["mobile"].type == "tel"
    assert "email" in field_by_name
    assert field_by_name["email"].type == "email"
    assert "flat_no" in field_by_name
    assert "pincode" in field_by_name

    # Checkbox group
    assert "source_of_income" in field_by_name
    income_field = field_by_name["source_of_income"]
    assert income_field.type == "checkbox"
    assert "Source of Income" in income_field.label
    assert len(income_field.options) >= 5

    # Actions: Step 1 should only show Save Draft and Next (Step 2 buttons are hidden)
    action_texts = [a["text"] for a in snap.actions]
    assert any("Save Draft" in t for t in action_texts)
    assert any("Next" in t for t in action_texts)
    assert not any("Pay Now" in t for t in action_texts)
    assert not any("Submit" in t for t in action_texts)


@pytest.mark.asyncio
async def test_fill_text_roundtrip(driver: BrowserDriver):
    """Fill text, email, and tel fields and verify values via page.evaluate."""
    url = fixture_url("pan_49a.html")
    snap = await driver.goto(url)

    first_name_ref = next(f.ref for f in snap.fields if f.name == "first_name")
    last_name_ref = next(f.ref for f in snap.fields if f.name == "last_name")
    email_ref = next(f.ref for f in snap.fields if f.name == "email")
    mobile_ref = next(f.ref for f in snap.fields if f.name == "mobile")

    await driver.fill(first_name_ref, "Aarav")
    await driver.fill(last_name_ref, "Patel")
    await driver.fill(email_ref, "aarav.patel@example.com")
    await driver.fill(mobile_ref, "9876543210")

    vals = await driver.page.evaluate("""() => ({
        first_name: document.getElementById('first_name').value,
        last_name: document.getElementById('last_name').value,
        email: document.getElementById('email').value,
        mobile: document.getElementById('mobile').value,
    })""")

    assert vals["first_name"] == "Aarav"
    assert vals["last_name"] == "Patel"
    assert vals["email"] == "aarav.patel@example.com"
    assert vals["mobile"] == "9876543210"


@pytest.mark.asyncio
async def test_date_conversion(driver: BrowserDriver):
    """Fill date field accepts DD/MM/YYYY, DD-MM-YYYY, or YYYY-MM-DD and converts."""
    url = fixture_url("pan_49a.html")
    snap = await driver.goto(url)

    dob_ref = next(f.ref for f in snap.fields if f.name == "dob")

    # Test DD/MM/YYYY
    await driver.fill(dob_ref, "26/01/1990")
    val1 = await driver.page.evaluate("() => document.getElementById('dob').value")
    assert val1 == "1990-01-26"

    # Test DD-MM-YYYY
    await driver.fill(dob_ref, "15-08-1985")
    val2 = await driver.page.evaluate("() => document.getElementById('dob').value")
    assert val2 == "1985-08-15"

    # Test YYYY-MM-DD
    await driver.fill(dob_ref, "2000-12-31")
    val3 = await driver.page.evaluate("() => document.getElementById('dob').value")
    assert val3 == "2000-12-31"


@pytest.mark.asyncio
async def test_radio_fill(driver: BrowserDriver):
    """Fill radio group by option label or value and verify checked state."""
    url = fixture_url("pan_49a.html")
    snap = await driver.goto(url)

    title_ref = next(f.ref for f in snap.fields if f.name == "title")

    # Fill 'Shri'
    await driver.fill(title_ref, "Shri")
    val_shri = await driver.page.evaluate(
        "() => document.querySelector('input[name=title]:checked')?.value"
    )
    assert val_shri == "Shri"

    # Switch to 'Smt'
    await driver.fill(title_ref, "Smt")
    val_smt = await driver.page.evaluate(
        "() => document.querySelector('input[name=title]:checked')?.value"
    )
    assert val_smt == "Smt"

    # Switch to 'Kumari'
    await driver.fill(title_ref, "Kumari")
    val_kumari = await driver.page.evaluate(
        "() => document.querySelector('input[name=title]:checked')?.value"
    )
    assert val_kumari == "Kumari"

    # Invalid option raises ValueError
    with pytest.raises(ValueError, match="not found in radio group"):
        await driver.fill(title_ref, "Dr")


@pytest.mark.asyncio
async def test_select_roundtrip(driver: BrowserDriver):
    """Select option by exact label, exact value, or substring and read value back."""
    url = fixture_url("pan_49a.html")
    snap = await driver.goto(url)

    cat_ref = next(f.ref for f in snap.fields if f.name == "category")
    app_type_ref = next(f.ref for f in snap.fields if f.name == "application_type")
    state_ref = next(f.ref for f in snap.fields if f.name == "state")

    # Match by exact label
    await driver.select(cat_ref, "INDIVIDUAL")
    val_cat = await driver.page.evaluate("() => document.getElementById('category').value")
    assert val_cat == "INDIVIDUAL"

    # Match by value
    await driver.select(app_type_ref, "49A_IND")
    val_app = await driver.page.evaluate("() => document.getElementById('application_type').value")
    assert val_app == "49A_IND"

    # Match by case-insensitive substring
    await driver.select(state_ref, "maharashtra")
    val_state = await driver.page.evaluate("() => document.getElementById('state').value")
    assert val_state == "Maharashtra"

    # Invalid option raises ValueError
    with pytest.raises(ValueError, match="not found in <select>"):
        await driver.select(cat_ref, "NonExistentOption")


@pytest.mark.asyncio
async def test_checkbox_fill(driver: BrowserDriver):
    """Fill standalone checkbox ('true'/'yes' checks it, 'false' unchecks it) and checkbox groups."""
    # 1. Standalone checkbox on voter_form6.html
    voter_url = fixture_url("voter_form6.html")
    snap = await driver.goto(voter_url)

    decl_ref = next(f.ref for f in snap.fields if f.name == "declaration")

    # Check via 'true'
    await driver.fill(decl_ref, "true")
    c1 = await driver.page.evaluate("() => document.querySelector('input[name=declaration]').checked")
    assert c1 is True

    # Uncheck via 'false'
    await driver.fill(decl_ref, "false")
    c2 = await driver.page.evaluate("() => document.querySelector('input[name=declaration]').checked")
    assert c2 is False

    # Check via 'yes'
    await driver.fill(decl_ref, "yes")
    c3 = await driver.page.evaluate("() => document.querySelector('input[name=declaration]').checked")
    assert c3 is True

    # 2. Checkbox group on pan_49a.html
    pan_url = fixture_url("pan_49a.html")
    snap_pan = await driver.goto(pan_url)
    income_ref = next(f.ref for f in snap_pan.fields if f.name == "source_of_income")

    await driver.fill(income_ref, "salary")
    income_checked = await driver.page.evaluate(
        "() => document.querySelector('input[name=source_of_income][value=salary]').checked"
    )
    assert income_checked is True


@pytest.mark.asyncio
async def test_clicking_next_and_step_progression(driver: BrowserDriver):
    """Clicking 'Next' progresses the form, reveals Step 2, and preserves stable refs."""
    url = fixture_url("pan_49a.html")
    snap1 = await driver.goto(url)

    next_action = next(a for a in snap1.actions if "Next" in a["text"])
    snap2 = await driver.click(next_action["ref"])

    # Step 2 fields are now visible
    step2_field_names = {f.name for f in snap2.fields}
    assert "proof_of_identity" in step2_field_names
    assert "proof_of_address" in step2_field_names
    assert "proof_of_dob" in step2_field_names

    # Step 2 actions: Back, Pay Now, Submit
    step2_action_texts = [a["text"] for a in snap2.actions]
    assert any("Back" in t for t in step2_action_texts)
    assert any("Pay Now" in t for t in step2_action_texts)
    assert any("Submit" in t for t in step2_action_texts)

    # Click 'Back' action
    back_action = next(a for a in snap2.actions if "Back" in a["text"])
    snap3 = await driver.click(back_action["ref"])

    # Returns to Step 1 and reuses existing refs
    assert snap3.fields[0].ref == snap1.fields[0].ref == "f1"
    assert any("Next" in a["text"] for a in snap3.actions)


@pytest.mark.asyncio
async def test_safety_guard_blocks_submit_and_pay_now(driver: BrowserDriver):
    """Clicking 'Submit' or 'Pay Now' on pan_49a raises BlockedActionError and URL remains safe."""
    url = fixture_url("pan_49a.html")
    snap1 = await driver.goto(url)

    # Move to Step 2
    next_action = next(a for a in snap1.actions if "Next" in a["text"])
    snap2 = await driver.click(next_action["ref"])

    pay_now_action = next(a for a in snap2.actions if "Pay Now" in a["text"])
    submit_action = next(a for a in snap2.actions if "Submit" in a["text"])

    # Attempt clicking 'Pay Now'
    with pytest.raises(BlockedActionError) as exc_pay:
        await driver.click(pay_now_action["ref"])
    assert "payment" in exc_pay.value.decision.reason
    assert "form_success.html" not in driver.page.url

    # Attempt clicking 'Submit'
    with pytest.raises(BlockedActionError) as exc_sub:
        await driver.click(submit_action["ref"])
    assert "submit" in exc_sub.value.decision.reason
    assert "form_success.html" not in driver.page.url


@pytest.mark.asyncio
async def test_safety_guard_blocks_send_otp_on_voter_form(driver: BrowserDriver):
    """Clicking 'Send OTP' on voter_form6.html raises BlockedActionError."""
    url = fixture_url("voter_form6.html")
    snap = await driver.goto(url)

    otp_action = next(a for a in snap.actions if "Send OTP" in a["text"])
    with pytest.raises(BlockedActionError) as exc:
        await driver.click(otp_action["ref"])

    assert "otp" in exc.value.decision.reason
    assert "form_success.html" not in driver.page.url


@pytest.mark.asyncio
async def test_safety_guard_blocks_everify_and_login_on_itr(driver: BrowserDriver):
    """Clicking 'Login' and 'e-Verify' on itr_login_and_form.html raises BlockedActionError."""
    url = fixture_url("itr_login_and_form.html")
    snap = await driver.goto(url)

    login_action = next(a for a in snap.actions if "Login" in a["text"])
    with pytest.raises(BlockedActionError) as exc_login:
        await driver.click(login_action["ref"])
    assert "login" in exc_login.value.decision.reason

    everify_action = next(a for a in snap.actions if "e-Verify" in a["text"])
    with pytest.raises(BlockedActionError) as exc_everify:
        await driver.click(everify_action["ref"])
    assert "e-verify" in exc_everify.value.decision.reason
    assert "form_success.html" not in driver.page.url


@pytest.mark.asyncio
async def test_goto_navigation_guard(driver: BrowserDriver):
    """Navigation to payment gateways or logout endpoints raises BlockedActionError."""
    with pytest.raises(BlockedActionError) as exc_gw:
        await driver.goto("https://paygov.example.com/checkout")
    assert "payment gateway" in exc_gw.value.decision.reason

    with pytest.raises(BlockedActionError) as exc_logout:
        await driver.goto("https://incometax.gov.in/logout")
    assert "logout endpoint" in exc_logout.value.decision.reason


@pytest.mark.asyncio
async def test_screenshot_returns_png_bytes(driver: BrowserDriver, tmp_path: Path):
    """Screenshot returns valid PNG bytes and optionally saves to screenshot_dir."""
    url = fixture_url("pan_49a.html")
    await driver.goto(url)

    png_bytes = await driver.screenshot()
    assert isinstance(png_bytes, bytes)
    assert len(png_bytes) > 1000
    # PNG signature: 89 50 4E 47 0D 0A 1A 0A
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"

    # Test with custom screenshot_dir
    d2 = BrowserDriver(headless=True, screenshot_dir=tmp_path)
    await d2.start()
    try:
        await d2.goto(url)
        bytes2 = await d2.screenshot()
        assert bytes2[:8] == b"\x89PNG\r\n\x1a\n"
        saved_files = list(tmp_path.glob("*.png"))
        assert len(saved_files) == 1
    finally:
        await d2.close()


@pytest.mark.asyncio
async def test_unknown_ref_raises_value_error(driver: BrowserDriver):
    """Unknown refs in fill, select, or click raise ValueError with clear messages."""
    url = fixture_url("pan_49a.html")
    await driver.goto(url)

    with pytest.raises(ValueError, match="Unknown ref"):
        await driver.fill("f999", "sample")

    with pytest.raises(ValueError, match="Unknown ref"):
        await driver.select("f999", "sample")

    with pytest.raises(ValueError, match="Unknown ref"):
        await driver.click("a999")


@pytest.mark.asyncio
async def test_cdp_endpoint_reachable(driver: BrowserDriver):
    """CDP endpoint is reachable and exposes http://127.0.0.1:<port>/json/version via urllib."""
    import json
    import urllib.request

    assert driver.cdp_port > 0
    assert driver.cdp_endpoint == f"http://127.0.0.1:{driver.cdp_port}"

    version_url = f"{driver.cdp_endpoint}/json/version"
    req = urllib.request.Request(version_url)
    with urllib.request.urlopen(req, timeout=5) as response:
        assert response.status == 200
        payload = json.loads(response.read().decode("utf-8"))
        assert "Browser" in payload
        assert "webSocketDebuggerUrl" in payload


@pytest.mark.asyncio
async def test_active_page_tracking_and_popup(driver: BrowserDriver):
    """Popup/new tab becomes the active page and listener fires; closes fall back to last open page."""
    import asyncio

    page_changes = []

    async def on_change(page):
        page_changes.append(page)

    driver.add_page_change_listener(on_change)

    initial_page = driver.page
    assert initial_page is not None

    count_before = len(page_changes)

    # Open a new tab via window.open
    await initial_page.evaluate("() => window.open('about:blank')")

    # Wait for listener to fire
    for _ in range(30):
        if len(page_changes) > count_before:
            break
        await asyncio.sleep(0.05)

    assert len(page_changes) > count_before
    new_page = driver.page
    assert new_page != initial_page
    assert page_changes[-1] == new_page

    count_after_open = len(page_changes)

    # Now close the active new page
    await new_page.close()

    # Wait for fallback
    for _ in range(30):
        if len(page_changes) > count_after_open:
            break
        await asyncio.sleep(0.05)

    assert len(page_changes) > count_after_open
    assert driver.page == initial_page
    assert page_changes[-1] == initial_page


@pytest.mark.asyncio
async def test_driver_custom_cdp_port():
    """BrowserDriver respects custom cdp_port if provided."""
    d = BrowserDriver(headless=True, cdp_port=9444)
    assert d.cdp_port == 9444
    assert d.cdp_endpoint == "http://127.0.0.1:9444"


