"""Unit tests for browser safety guard (agastya.browser.guard)."""

import pytest

from agastya.browser.guard import (
    GuardDecision,
    check_click,
    check_navigation,
)


# =====================================================================
# 1. English final actions (check_click -> blocked)
# =====================================================================
@pytest.mark.parametrize(
    "text,expected_reason_substr",
    [
        ("Submit", "submit"),
        ("Final Submit", "final submit"),
        ("Submit Return", "submit return"),
        ("Submit Form", "submit form"),
        ("File Return", "file return"),
        ("File Now", "file return"),
        ("I Agree and Submit", "agree and submit"),
        ("Agree & Submit", "agree and submit"),
        ("e-Verify", "e-verify"),
        ("everify", "e-verify"),
        ("E-VERIFY", "e-verify"),
        ("Verify", "verify"),
        ("Make Payment", "payment"),
        ("Pay Now", "payment"),
        ("Proceed to Pay", "proceed to pay"),
        ("Payment", "payment"),
        ("Pay", "payment"),
        ("Generate OTP", "otp"),
        ("Resend OTP", "otp"),
        ("Verify OTP", "otp"),
        ("Validate OTP", "otp"),
        ("Send OTP", "otp"),
        ("Get OTP", "otp"),
        ("Aadhaar OTP", "otp"),
        ("OTP", "otp"),
        ("Log In", "login"),
        ("Login", "login"),
        ("Sign In", "sign in"),
        ("Signin", "sign in"),
        ("Sign-in", "sign in"),
        ("Logout", "logout"),
        ("Sign out", "logout"),
        ("Confirm", "confirm"),
        ("Captcha Refresh", "captcha refresh"),
        ("Refresh Captcha", "captcha refresh"),
        ("Reload Captcha", "captcha refresh"),
        ("New Captcha", "captcha refresh"),
        ("Final Register", "register"),
        ("Register & Submit", "register"),
        ("Register and Submit", "register"),
        ("Submit Registration", "register"),
        ("Final Registration", "register"),
        ("Save Draft and Submit", "submit"),
        ("Pay and Continue", "payment"),
    ],
)
def test_check_click_blocked_english(text: str, expected_reason_substr: str) -> None:
    element = {"tag": "button", "text": text}
    decision = check_click(element)
    assert not decision.allowed, f"Expected '{text}' to be blocked"
    assert not bool(decision)
    assert expected_reason_substr in decision.reason.lower()


# =====================================================================
# 2. Hindi terms (check_click -> blocked)
# =====================================================================
@pytest.mark.parametrize(
    "text,expected_reason_substr",
    [
        ("जमा करें", "submit"),
        ("जमा करे", "submit"),
        ("जमा करना", "submit"),
        ("भुगतान करें", "payment"),
        ("भुगतान करे", "payment"),
        ("भुगतान", "payment"),
        ("सत्यापित करें", "verify"),
        ("सत्यापित करे", "verify"),
        ("सत्यापित", "verify"),
        ("सत्यापन", "verify"),
        ("लॉगिन", "login"),
        ("लॉग इन", "login"),
        ("ओटीपी", "otp"),
        ("ओटीपी भेजें", "otp"),
        ("ओटीपी सत्यापित करें", "otp"),
        ("पुष्टि करें", "confirm"),
        ("पुष्टि", "confirm"),
        ("कैप्चा रिफ्रेश", "captcha"),
        ("कैप्चा", "captcha"),
    ],
)
def test_check_click_blocked_hindi(text: str, expected_reason_substr: str) -> None:
    element = {"tag": "button", "text": text}
    decision = check_click(element)
    assert not decision.allowed, f"Expected Hindi '{text}' to be blocked"
    assert not bool(decision)
    assert expected_reason_substr in decision.reason.lower()


# =====================================================================
# 3. Mixed case & whitespace variations (check_click -> blocked)
# =====================================================================
@pytest.mark.parametrize(
    "text",
    [
        "SUBMIT",
        "submit",
        "sUbMiT",
        "  Final   Submit  ",
        "PAY NOW",
        "pAy NoW",
        "Make Payment",
        "MAKE PAYMENT",
        "VERIFY OTP",
        "Verify Otp",
        "vErIfY oTp",
        "LOG IN",
        "log in",
        "LoG iN",
        "SIGN IN",
        "Sign In",
        "sign-in",
        "SIGN-IN",
        "e-verify",
        "E-Verify",
        "E-VERIFY",
        "eVerify",
        "EVERIFY",
        "aadhaar otp",
        "AADHAAR OTP",
        "Aadhaar Otp",
        "  Proceed   to   Pay  ",
        "I AGREE AND SUBMIT",
        "Agree & Submit",
    ],
)
def test_check_click_mixed_case(text: str) -> None:
    element = {"tag": "button", "text": text}
    decision = check_click(element)
    assert not decision.allowed, f"Expected mixed-case '{text}' to be blocked"
    assert not bool(decision)


# =====================================================================
# 4. Buttons with only id/name or accessible attributes (check_click -> blocked)
# =====================================================================
@pytest.mark.parametrize(
    "attr_dict,expected_reason_substr",
    [
        ({"id": "btnSubmit"}, "submit"),
        ({"id": "submitBtn"}, "submit"),
        ({"id": "btn_submit"}, "submit"),
        ({"id": "btn-submit"}, "submit"),
        ({"id": "btn_final_submit"}, "final submit"),
        ({"id": "BTNSUBMIT"}, "submit"),
        ({"name": "btnSubmit"}, "submit"),
        ({"name": "submit"}, "submit"),
        ({"name": "btn_submit_form"}, "submit form"),
        ({"id": "btnPayNow"}, "payment"),
        ({"id": "btn_pay"}, "payment"),
        ({"name": "pay_now"}, "payment"),
        ({"name": "action_login"}, "login"),
        ({"id": "loginBtn"}, "login"),
        ({"id": "btnVerifyOtp"}, "otp"),
        ({"name": "generateOtp"}, "otp"),
        ({"id": "aadhaar_otp_btn"}, "otp"),
        ({"id": "everify_btn"}, "e-verify"),
        ({"id": "btnEVerify"}, "e-verify"),
        ({"aria_label": "Submit Application"}, "submit"),
        ({"aria-label": "Final Submit Return"}, "final submit"),
        ({"aria_label": "Make Payment Online"}, "payment"),
        ({"value": "Submit Form"}, "submit form"),
        ({"value": "Pay Now"}, "payment"),
    ],
)
def test_check_click_button_only_id_or_name(
    attr_dict: dict, expected_reason_substr: str
) -> None:
    element = {"tag": "button", **attr_dict}
    decision = check_click(element)
    assert not decision.allowed, f"Expected element with {attr_dict} to be blocked"
    assert expected_reason_substr in decision.reason.lower()


# =====================================================================
# 5. Generic submit checks (check_click -> blocked with 'generic submit')
# =====================================================================
@pytest.mark.parametrize(
    "element",
    [
        {"tag": "input", "type": "submit", "value": ""},
        {"tag": "input", "type": "submit"},
        {"tag": "input", "type": "submit", "value": "go"},
        {"tag": "input", "type": "submit", "value": "Go"},
        {"tag": "input", "type": "submit", "value": "GO"},
        {"tag": "input", "type": "submit", "value": "ok"},
        {"tag": "input", "type": "submit", "value": "OK"},
        {"tag": "input", "type": "submit", "value": "  OK  "},
        {"tag": "button", "type": "submit", "text": ""},
        {"tag": "button", "type": "submit", "text": "   "},
        {"tag": "button", "type": "submit", "text": "go"},
        {"tag": "button", "type": "submit", "text": "Go"},
        {"tag": "button", "type": "submit", "text": "GO"},
        {"tag": "button", "type": "submit", "text": "ok"},
        {"tag": "button", "type": "submit", "text": "OK"},
        {"tag": "button", "type": "submit", "text": "  Ok  "},
        {"tag": "button", "text": ""},
        {"tag": "button", "text": "go"},
        {"tag": "button", "text": "ok"},
    ],
)
def test_check_click_generic_submit_blocked(element: dict) -> None:
    decision = check_click(element)
    assert not decision.allowed, f"Expected {element} to be blocked as generic submit"
    assert decision.reason == "generic submit"


# =====================================================================
# 6. Explicit ALLOW list (check_click -> allowed)
# =====================================================================
@pytest.mark.parametrize(
    "text",
    [
        "Save Draft",
        "save draft",
        "SAVE DRAFT",
        "Save as Draft",
        "save as draft",
        "Next",
        "next",
        "NEXT",
        "Continue",
        "continue",
        "CONTINUE",
        "Previous",
        "previous",
        "PREVIOUS",
        "Back",
        "back",
        "BACK",
        "Add",
        "add",
        "Add Row",
        "Add Member",
        "Search",
        "search",
        "Search PAN",
        "Search Records",
        "Select",
        "select",
        "Select All",
        "Select State",
        "Preview",
        "preview",
        "Preview Form",
        "Preview Return",
        "Download",
        "download",
        "Download PDF",
        "Download Acknowledgement",
        "Clear",
        "clear",
        "Clear Form",
        "Reset Filter",
        "reset filter",
        "Reset Filters",
        "RESET FILTER",
    ],
)
def test_check_click_allow_list(text: str) -> None:
    element = {"tag": "button", "type": "button", "text": text}
    decision = check_click(element)
    assert decision.allowed, f"Expected '{text}' to be allowed"
    assert bool(decision)


# =====================================================================
# 7. Explicit ALLOW list overrides generic submit rules
# =====================================================================
@pytest.mark.parametrize(
    "element",
    [
        {"tag": "button", "type": "submit", "text": "Save Draft"},
        {"tag": "button", "type": "submit", "text": "Save as Draft"},
        {"tag": "button", "type": "submit", "text": "Next"},
        {"tag": "button", "type": "submit", "text": "Continue"},
        {"tag": "button", "type": "submit", "text": "Previous"},
        {"tag": "button", "type": "submit", "text": "Back"},
        {"tag": "button", "type": "submit", "text": "Search"},
        {"tag": "button", "type": "submit", "text": "Preview"},
        {"tag": "button", "type": "submit", "text": "Download"},
        {"tag": "button", "type": "submit", "text": "Add"},
        {"tag": "button", "type": "submit", "text": "Select"},
        {"tag": "button", "type": "submit", "text": "Clear"},
        {"tag": "button", "type": "submit", "text": "Reset Filter"},
        {"tag": "input", "type": "submit", "value": "Save Draft"},
        {"tag": "input", "type": "submit", "value": "Next"},
        {"tag": "input", "type": "submit", "value": "Continue"},
        {"tag": "input", "type": "submit", "value": "Search"},
        {"tag": "button", "type": "submit", "id": "btnNext"},
        {"tag": "button", "type": "submit", "id": "btnContinue"},
        {"tag": "button", "type": "submit", "id": "btnSaveDraft"},
    ],
)
def test_check_click_allow_overrides_generic_submit(element: dict) -> None:
    decision = check_click(element)
    assert decision.allowed, f"Expected allow list to override generic submit for {element}"
    assert bool(decision)


# =====================================================================
# 8. Word boundaries test: 'submit' inside words like 'submitted'
# =====================================================================
@pytest.mark.parametrize(
    "text",
    [
        "View submitted forms",
        "Submitted Applications",
        "submitted returns",
        "Already Submitted",
        "List of submitted returns",
        "Show Submitted",
        "submitted",
    ],
)
def test_check_click_word_boundary_submitted(text: str) -> None:
    element = {"tag": "button", "type": "button", "text": text}
    decision = check_click(element)
    assert decision.allowed, f"Expected '{text}' to NOT be blocked by submit rule"
    assert bool(decision)


def test_check_click_submitted_in_id() -> None:
    element = {"tag": "button", "id": "btnSubmitted", "text": "Submitted"}
    decision = check_click(element)
    assert decision.allowed, "Expected btnSubmitted to NOT be blocked"


# =====================================================================
# 9. Register alone is allowed (only blocked when combined with final/submit)
# =====================================================================
@pytest.mark.parametrize(
    "text",
    [
        "Register",
        "register",
        "REGISTER",
        "New User? Register",
        "Register Here",
        "Register to Portal",
        "Click to Register",
    ],
)
def test_check_click_register_allowed_when_not_final(text: str) -> None:
    element = {"tag": "button", "type": "button", "text": text}
    decision = check_click(element)
    assert decision.allowed, f"Expected '{text}' to be allowed"
    assert bool(decision)


# =====================================================================
# 10. General allowed actions (unknown actions -> allowed)
# =====================================================================
@pytest.mark.parametrize(
    "element",
    [
        {"tag": "a", "href": "/form/step2", "text": "Step 2"},
        {"tag": "button", "text": "Edit Profile"},
        {"tag": "button", "text": "Upload Document"},
        {"tag": "button", "text": "View Details"},
        {"tag": "input", "type": "checkbox", "name": "agree_terms"},
        {"tag": "input", "type": "text", "name": "pan_number"},
        {"tag": "a", "href": "/help", "text": "Help & Support"},
        {"tag": "button", "text": "Close Dialog"},
    ],
)
def test_check_click_general_allowed(element: dict) -> None:
    decision = check_click(element)
    assert decision.allowed, f"Expected {element} to be allowed"
    assert bool(decision)


# =====================================================================
# 11. Links with href pointing to blocked payment/logout
# =====================================================================
@pytest.mark.parametrize(
    "href,expected_reason_substr",
    [
        ("https://checkout.razorpay.com/v1/checkout", "razorpay"),
        ("https://pgi.billdesk.com/pay", "billdesk"),
        ("https://secure.paygov.gov.in/pg", "paygov"),
        ("https://secure.payu.in/pay", "payu"),
        ("https://www.sbiepay.sbi/pay", "sbiepay"),
        ("https://secure.ccavenue.com/pay", "ccavenue"),
        ("https://securegw.paytm.in/pay", "paytm"),
        ("https://incometax.gov.in/iec/foportal/logout", "logout"),
        ("/logout", "logout"),
        ("/signout", "signout"),
        ("javascript:submitForm()", "submit"),
    ],
)
def test_check_click_href_blocks(href: str, expected_reason_substr: str) -> None:
    element = {"tag": "a", "href": href, "text": "Click Here"}
    decision = check_click(element)
    assert not decision.allowed, f"Expected link with href '{href}' to be blocked"
    assert expected_reason_substr in decision.reason.lower()


# =====================================================================
# 12. check_navigation blocked cases (payment gateways & logout URLs)
# =====================================================================
@pytest.mark.parametrize(
    "url,expected_reason_substr",
    [
        ("https://services.paygov.gov.in/payment", "paygov"),
        ("https://paygov.gov.in", "paygov"),
        ("https://pgi.billdesk.com/pgidsk/PGIMerchantPayment", "billdesk"),
        ("https://merchant.billdesk.com", "billdesk"),
        ("billdesk.com/pay", "billdesk"),
        ("https://api.razorpay.com/v1/checkout", "razorpay"),
        ("https://checkout.razorpay.com", "razorpay"),
        ("https://RAZORPAY.com/checkout", "razorpay"),
        ("razorpay.com/pay", "razorpay"),
        ("https://secure.payu.in/_payment", "payu"),
        ("https://payu.in/checkout", "payu"),
        ("https://www.sbiepay.sbi/secure/AggregatorHostedListener", "sbiepay"),
        ("https://sbiepay.sbi", "sbiepay"),
        ("https://secure.ccavenue.com/transaction/transaction.do", "ccavenue"),
        ("https://ccavenue.com", "ccavenue"),
        ("https://securegw.paytm.in/theia/processTransaction", "paytm"),
        ("https://paytm.com/payment", "paytm"),
        ("https://incometax.gov.in/iec/foportal/logout", "logout"),
        ("https://incometax.gov.in/iec/foportal/signout", "signout"),
        ("https://example.com/app/logout?redirect=true", "logout"),
        ("https://example.com/auth/signout", "signout"),
        ("/logout", "logout"),
        ("/signout", "signout"),
        ("/api/v1/logout", "logout"),
        ("/portal/user/signout", "signout"),
        ("logout", "logout"),
        ("signout", "signout"),
    ],
)
def test_check_navigation_blocked(url: str, expected_reason_substr: str) -> None:
    decision = check_navigation(url)
    assert not decision.allowed, f"Expected navigation to '{url}' to be blocked"
    assert not bool(decision)
    assert expected_reason_substr in decision.reason.lower()


# =====================================================================
# 13. check_navigation allowed cases (government portals, forms, dashboards)
# =====================================================================
@pytest.mark.parametrize(
    "url",
    [
        "https://eportal.incometax.gov.in/iec/foservices/#/login",
        "https://eportal.incometax.gov.in/iec/foservices/#/dashboard",
        "https://voters.eci.gov.in/",
        "https://voters.eci.gov.in/form6",
        "https://tin.tin.nsdl.com/pan/form49A.html",
        "https://www.mca.gov.in/mcafoportal/companyLLPSearch.do",
        "https://www.mca.gov.in/mcafoportal/showEformSubmit.do",
        "https://incometax.gov.in/iec/foportal/taxpayer-services",
        "https://taxpayer.incometax.gov.in/home",
        "/form/step1",
        "/form/step2",
        "/profile",
        "/dashboard",
        "https://example.com/search?q=pan",
    ],
)
def test_check_navigation_allowed(url: str) -> None:
    decision = check_navigation(url)
    assert decision.allowed, f"Expected navigation to '{url}' to be allowed"
    assert bool(decision)


# =====================================================================
# 14. Edge cases & GuardDecision behavior
# =====================================================================
def test_guard_decision_bool_conversion() -> None:
    allowed_decision = GuardDecision(allowed=True, reason="allowed")
    blocked_decision = GuardDecision(allowed=False, reason="blocked")
    assert bool(allowed_decision) is True
    assert bool(blocked_decision) is False
    if allowed_decision:
        pass
    else:
        pytest.fail("allowed_decision should evaluate to True")

    if not blocked_decision:
        pass
    else:
        pytest.fail("blocked_decision should evaluate to False")


def test_check_click_empty_or_none() -> None:
    assert check_click(None).allowed is True
    assert check_click({}).allowed is True
    assert check_click({"tag": "div"}).allowed is True


def test_check_navigation_empty_or_none() -> None:
    assert check_navigation("").allowed is True
    assert check_navigation("   ").allowed is True


@pytest.mark.parametrize("text", ["Proceed to Verification", "Continue to e-Verification", "proceed to verification"])
def test_proceed_to_verification_is_blocked(text):
    assert not check_click({"tag": "button", "text": text}).allowed
