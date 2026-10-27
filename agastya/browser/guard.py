"""Safety guard for browser automation.

Prevents the automated assistant from performing irreversible, sensitive,
or final actions such as submitting forms, making payments, handling OTPs,
logging in/out, or navigating to payment gateways and logout endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
import urllib.parse


@dataclass
class GuardDecision:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


# Navigation block targets: payment gateways and logout endpoints
PAYMENT_HOST_KEYWORDS: list[str] = [
    "paygov",
    "billdesk",
    "razorpay",
    "payu",
    "sbiepay",
    "ccavenue",
    "paytm",
]

LOGOUT_PATH_KEYWORDS: list[str] = [
    "/logout",
    "/signout",
    "logout",
    "signout",
]

# Explicit ALLOW list overrides generic submit rules:
# 'save draft','save as draft','next','continue','previous','back','add',
# 'search','select','preview','download','clear','reset filter'
ALLOW_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsave\s+as\s+draft\b", re.I), "save as draft"),
    (re.compile(r"\bsave\s+draft\b", re.I), "save draft"),
    (re.compile(r"\bnext\b", re.I), "next"),
    (re.compile(r"\bcontinue\b", re.I), "continue"),
    (re.compile(r"\bprevious\b", re.I), "previous"),
    (re.compile(r"\bback\b", re.I), "back"),
    (re.compile(r"\badd\b", re.I), "add"),
    (re.compile(r"\bsearch\b", re.I), "search"),
    (re.compile(r"\bselect\b", re.I), "select"),
    (re.compile(r"\bpreview\b", re.I), "preview"),
    (re.compile(r"\bdownload\b", re.I), "download"),
    (re.compile(r"\bclear\b", re.I), "clear"),
    (re.compile(r"\breset\s+filters?\b", re.I), "reset filter"),
]

# Generic text values for submit buttons that trigger 'generic submit' block
GENERIC_SUBMIT_TEXTS: set[str] = {"", "go", "ok"}

# Block rules: list of (compiled regex, reason).
# Checked with case-insensitivity and word boundaries so 'submit'
# never matches inside words like 'submitted' listing text.
BLOCK_RULES: list[tuple[re.Pattern[str], str]] = [
    # Submit / Final action combinations (more specific rules first)
    (re.compile(r"\b(i\s+)?agree\s+(&|and\s+)?submit\b", re.I), "agree and submit"),
    (re.compile(r"\bfinal\s+submit\b", re.I), "final submit"),
    (re.compile(r"\bsubmit\s+return\b", re.I), "submit return"),
    (re.compile(r"\bsubmit\s+form\b", re.I), "submit form"),
    (re.compile(r"\bfile\s+(return|now)\b", re.I), "file return"),
    (re.compile(r"\b(final\s+)?submission\b", re.I), "final submit"),
    # Register only when combined with final/submit
    (
        re.compile(
            r"\b(final\s+.*regist(er|ration)|regist(er|ration)\s+.*(final|submit)|submit\s+.*regist(er|ration))\b",
            re.I,
        ),
        "register (final)",
    ),
    (re.compile(r"\bsubmit\b", re.I), "submit"),
    # Hindi submit terms (जमा करें, जमा करे, जमा करना)
    (re.compile(r"जमा\s+कर(ें|े|ना)", re.I), "submit (hindi)"),

    # OTP actions
    (
        re.compile(
            r"\b(generate|resend|verify|validate|send|get|aadhaar)\s+otp\b",
            re.I,
        ),
        "otp",
    ),
    (re.compile(r"\botp\b", re.I), "otp"),
    # Hindi OTP terms (ओटीपी)
    (re.compile(r"ओटीपी", re.I), "otp (hindi)"),

    # Payment actions
    (re.compile(r"\bproceed\s+to\s+pay\b", re.I), "proceed to pay"),
    (re.compile(r"\b(make\s+payment|pay\s+now)\b", re.I), "payment"),
    (re.compile(r"\b(pay|payment)\b", re.I), "payment"),
    # Hindi payment terms (भुगतान)
    (re.compile(r"भुगतान(\s+कर(ें|े))?", re.I), "payment (hindi)"),

    # E-Verify & Verification
    (re.compile(r"\b(e[\s-]?verify|everify)\b", re.I), "e-verify"),
    (re.compile(r"\bverify\b", re.I), "verify"),
    # ITR: "Proceed to Verification" files the return before e-verify
    (re.compile(r"\b(proceed|continue|go)\s+to\s+(e[\s-]?)?verification\b", re.I), "file return (proceed to verification)"),
    # Hindi verify terms (सत्यापित, सत्यापन)
    (re.compile(r"सत्यापित(\s+कर(ें|े))?|सत्यापन", re.I), "verify (hindi)"),

    # Login / Sign In / Logout
    (re.compile(r"\b(log\s*in|login)\b", re.I), "login"),
    (re.compile(r"\b(sign\s*in|signin|sign-in)\b", re.I), "sign in"),
    (re.compile(r"\b(log\s*out|logout|sign\s*out|signout|sign-out)\b", re.I), "logout"),
    # Hindi login terms (लॉगिन, लॉग इन)
    (re.compile(r"लॉग(\s*इन|िन)", re.I), "login (hindi)"),

    # Confirmation
    (re.compile(r"\bconfirm\b", re.I), "confirm"),
    # Hindi confirm terms (पुष्टि करें)
    (re.compile(r"पुष्टि(\s+कर(ें|े))?", re.I), "confirm (hindi)"),

    # Captcha refresh
    (
        re.compile(
            r"\b(captcha\s+refresh|refresh\s+captcha|reload\s+captcha|new\s+captcha)\b",
            re.I,
        ),
        "captcha refresh",
    ),
    # Hindi captcha
    (re.compile(r"कैप्चा", re.I), "captcha (hindi)"),
]


def _normalize_text(s: Any) -> str:
    """Normalize visible text: expand &, collapse whitespace, lowercase."""
    if s is None:
        return ""
    text = str(s).strip()
    if not text:
        return ""
    text = re.sub(r"&", " and ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _normalize_identifier(s: Any) -> str:
    """Normalize identifier (id, name, href): split camelCase and separators, lowercase."""
    if s is None:
        return ""
    text = str(s).strip()
    if not text:
        return ""
    # Split camelCase: e.g. btnSubmit -> btn Submit, getOTP -> get OTP
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    # Split common identifier prefixes e.g. BTNSUBMIT -> BTN SUBMIT
    text = re.sub(r"^(btn|button|lnk|link)([a-zA-Z])", r"\1 \2", text, flags=re.I)
    text = re.sub(r"&", " and ", text)
    # Replace common identifier/path separators with space
    text = re.sub(r"[-_./:#?&=]+", " ", text)
    # Collapse multiple whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def check_navigation(url: str) -> GuardDecision:
    """Check if navigating to a URL is safe.

    Blocks payment gateways (paygov, billdesk, razorpay, payu, sbiepay,
    ccavenue, paytm) and logout endpoints (/logout, /signout).
    Allows everything else.
    """
    if not url or not isinstance(url, str):
        return GuardDecision(allowed=True, reason="allowed")

    cleaned = url.strip()
    if not cleaned:
        return GuardDecision(allowed=True, reason="allowed")

    parsed = urllib.parse.urlsplit(cleaned)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    # Handle URLs without scheme like "billdesk.com/pay" or "razorpay.com"
    if not host and not cleaned.startswith("/"):
        first_segment = cleaned.split("/", 1)[0]
        if "." in first_segment:
            parts = cleaned.split("/", 1)
            host = parts[0].lower()
            path = ("/" + parts[1].lower()) if len(parts) > 1 else ""
        else:
            path = cleaned.lower()

    # Block hosts/paths containing payment gateway keywords
    for kw in PAYMENT_HOST_KEYWORDS:
        if kw in host:
            return GuardDecision(allowed=False, reason=f"payment gateway ({kw})")

    for kw in PAYMENT_HOST_KEYWORDS:
        if kw in path:
            return GuardDecision(allowed=False, reason=f"payment gateway ({kw})")

    # Block logout endpoints
    for kw in LOGOUT_PATH_KEYWORDS:
        if kw in path or kw in host or cleaned.lower() == kw:
            return GuardDecision(allowed=False, reason=f"logout endpoint ({kw})")

    return GuardDecision(allowed=True, reason="allowed")


def check_click(element: dict | None) -> GuardDecision:
    """Check if clicking an element is safe.

    element keys:
      tag, type, text, id, name, aria_label (or aria-label), value, href, url

    BLOCK when the element is a final action:
      submit, final submit, file return, e-verify, verify, pay, payment,
      proceed to pay, send/validate/get/resend/generate otp, login, sign in,
      captcha refresh, register (final), confirm, agree & submit,
      including Hindi variants.
    ALLOW:
      save draft, save as draft, next, continue, previous, back, add, search,
      select, preview, download, clear, reset filter.
      Unknown actions -> allowed=True.
    """
    if not element or not isinstance(element, dict):
        return GuardDecision(allowed=True, reason="allowed")

    tag = str(element.get("tag") or "").strip().lower()
    elem_type = str(element.get("type") or "").strip().lower()

    # Extract all relevant attributes
    raw_text = str(element.get("text") or "").strip()
    raw_value = str(element.get("value") or "").strip()
    raw_aria = str(element.get("aria_label") or element.get("aria-label") or "").strip()
    raw_id = str(element.get("id") or "").strip()
    raw_name = str(element.get("name") or "").strip()
    raw_href = str(element.get("href") or "").strip()

    # Normalized versions
    # For visible text labels, include direct normalization
    norm_text = _normalize_text(raw_text)
    norm_value = _normalize_text(raw_value)
    norm_aria = _normalize_text(raw_aria)

    norm_id = _normalize_identifier(raw_id)
    norm_name = _normalize_identifier(raw_name)
    norm_href = _normalize_identifier(raw_href)

    # 1. If href is present, verify navigation safety (catches links to gateways / logout)
    if raw_href:
        nav_decision = check_navigation(raw_href)
        if not nav_decision.allowed:
            return nav_decision

    # Primary visible / accessible labels
    primary_texts = [t for t in (norm_text, norm_value, norm_aria) if t]
    secondary_texts = [t for t in (norm_id, norm_name, norm_href) if t]

    # 2. Check BLOCK rules on primary visible texts.
    # If any visible label contains a blocked final action (e.g. "Submit", "Pay", "Verify",
    # or combined like "Save and Submit", "Pay and Continue"), it MUST be blocked.
    for text in primary_texts:
        for pattern, reason in BLOCK_RULES:
            if pattern.search(text):
                return GuardDecision(allowed=False, reason=reason)

    # 3. Explicit ALLOW list overrides generic submit rules
    # Check primary texts first; if primary texts are empty (e.g. icon button), check secondary texts.
    candidate_allow_texts = primary_texts if primary_texts else secondary_texts
    for text in candidate_allow_texts:
        for pattern, allow_reason in ALLOW_PATTERNS:
            if pattern.search(text):
                return GuardDecision(allowed=True, reason=f"allowed: {allow_reason}")

    # 4. Check BLOCK rules on secondary attributes (id, name, href).
    # Catches buttons with only id/name like id='btnSubmit' or name='btnLogin'.
    for text in secondary_texts:
        for pattern, reason in BLOCK_RULES:
            if pattern.search(text):
                return GuardDecision(allowed=False, reason=reason)

    # 5. Generic submit check:
    # An <input type=submit> or <button type=submit> whose text is empty or generic ('go','ok')
    # -> block with reason 'generic submit'.
    # In HTML, <button> without explicit type defaults to submit.
    is_submit_type = (elem_type == "submit") or (tag == "input" and elem_type == "submit") or (tag == "button" and elem_type in ("submit", ""))
    if is_submit_type:
        primary_repr = norm_text or norm_value or norm_aria
        if primary_repr in GENERIC_SUBMIT_TEXTS:
            return GuardDecision(allowed=False, reason="generic submit")

    # 6. Default: allow unknown / safe actions
    return GuardDecision(allowed=True, reason="allowed")
