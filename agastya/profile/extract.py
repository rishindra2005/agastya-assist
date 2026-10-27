"""Pattern-based field value extraction from natural language user responses."""

from __future__ import annotations

import re
from typing import Callable

DEVANAGARI_DIGITS: dict[str, str] = {
    "०": "0",
    "१": "1",
    "२": "2",
    "३": "3",
    "४": "4",
    "५": "5",
    "६": "6",
    "७": "7",
    "८": "8",
    "९": "9",
}

MONTH_MAP: dict[str, int] = {
    # English full & abbreviations
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
    # Hindi full & common variants
    "जनवरी": 1,
    "जनवरि": 1,
    "फ़रवरी": 2,
    "फरवरी": 2,
    "फ़रवरि": 2,
    "फरवरि": 2,
    "मार्च": 3,
    "अप्रैल": 4,
    "अप्रेल": 4,
    "मई": 5,
    "जून": 6,
    "जुलाई": 7,
    "जुलै": 7,
    "अगस्त": 8,
    "सितंबर": 9,
    "सितम्बर": 9,
    "अक्टूबर": 10,
    "अक्तूबर": 10,
    "नवंबर": 11,
    "नवम्बर": 11,
    "दिसंबर": 12,
    "दिसम्बर": 12,
}

_MONTH_PATTERN = "|".join(
    re.escape(k) for k in sorted(MONTH_MAP.keys(), key=len, reverse=True)
)


def devanagari_to_ascii(text: str) -> str:
    """Convert Devanagari digits ०-९ to ASCII 0-9."""
    return "".join(DEVANAGARI_DIGITS.get(ch, ch) for ch in text)


def extract_pan(text: str) -> str | None:
    """Extract 10-char PAN (5 letters, 4 digits, 1 letter)."""
    pattern = re.compile(
        r"(?i)(?<![A-Za-z0-9])([a-z]{5}\s*[0-9]{4}\s*[a-z])(?![A-Za-z0-9])"
    )
    m = pattern.search(text)
    if m:
        return re.sub(r"\s+", "", m.group(1)).upper()
    return None


def extract_aadhaar(text: str) -> str | None:
    """Extract 12-digit Aadhaar number with optional spaces/dashes."""
    pattern = re.compile(r"(?<!\d)(?:\d[\s-]?){11}\d(?!\d)")
    for m in pattern.finditer(text):
        start, end = m.span()
        prefix = text[:start]
        suffix = text[end:]
        if re.search(r"\d[\s-]*$", prefix) or re.match(r"^[\s-]*\d", suffix):
            continue
        digits = re.sub(r"[\s-]", "", m.group(0))
        if len(digits) == 12:
            return digits
    return None


def extract_mobile(text: str) -> str | None:
    """Extract 10-digit Indian mobile number starting with 6-9, with optional +91/0 prefix."""
    pattern = re.compile(
        r"(?<!\d)(?:(?:\+91|0)[\s-]?|91[\s-]|\+?91(?=[6-9]\d{9}))?([6-9](?:[\s-]?\d){9})(?!\d)"
    )
    for m in pattern.finditer(text):
        start, end = m.span()
        prefix = text[:start]
        suffix = text[end:]
        # Check if preceded or followed by digits separated only by spaces/dashes
        if re.search(r"\d[\s-]*$", prefix) or re.match(r"^[\s-]*\d", suffix):
            continue
        digits = re.sub(r"[\s-]", "", m.group(1))
        if len(digits) == 10 and digits[0] in "6789":
            return digits
    return None


def extract_email(text: str) -> str | None:
    """Extract email address."""
    pattern = re.compile(r"(?i)\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b")
    m = pattern.search(text)
    if m:
        return m.group(1).rstrip(".,;")
    return None


def extract_pincode(text: str) -> str | None:
    """Extract 6-digit Indian PIN code, ensuring it is not part of Aadhaar, mobile or date."""
    pattern = re.compile(r"(?<!\d)([1-9]\d{5})(?!\d)")
    for m in pattern.finditer(text):
        start, end = m.span()
        prefix = text[:start]
        suffix = text[end:]
        # Reject if adjacent to other digits separated by spaces or dashes (e.g. Aadhaar/mobile)
        if re.search(r"\d[\s-]*$", prefix) or re.match(r"^[\s-]*\d", suffix):
            continue
        # Reject if adjacent to date delimiters
        if re.search(r"[/-]\s*$", prefix) or re.match(r"^\s*[/-]", suffix):
            continue
        return m.group(1)
    return None


def extract_bank_ifsc(text: str) -> str | None:
    """Extract 11-char IFSC code (4 letters, 0, 6 alphanumeric)."""
    pattern = re.compile(
        r"(?i)(?<![A-Za-z0-9])([a-z]{4}\s*0\s*[a-z0-9]{6})(?![A-Za-z0-9])"
    )
    m = pattern.search(text)
    if m:
        return re.sub(r"\s+", "", m.group(1)).upper()
    return None


def extract_epic_number(text: str) -> str | None:
    """Extract 10-char EPIC voter ID number (3 letters, 7 digits)."""
    pattern = re.compile(
        r"(?i)(?<![A-Za-z0-9])([a-z]{3}\s*[0-9]{7})(?![A-Za-z0-9])"
    )
    m = pattern.search(text)
    if m:
        return re.sub(r"\s+", "", m.group(1)).upper()
    return None


def extract_date_of_birth(text: str) -> str | None:
    """Extract date of birth in formats DD/MM/YYYY, DD-MM-YYYY, '5 March 2001', '5 मार्च 2001'.

    Always outputs DD/MM/YYYY.
    """
    # 1. Named month: Day Month Year (e.g. 5 March 2001, 5th March 2001, 5 मार्च 2001)
    pat_dmy = re.compile(
        rf"(?i)(?:^|[^\d])(\d{{1,2}})(?:st|nd|rd|th)?[\s,-]+({_MONTH_PATTERN})[\s,-]+(\d{{4}})(?:[^\d]|$)"
    )
    m = pat_dmy.search(text)
    if m:
        day = int(m.group(1))
        m_name = m.group(2).lower()
        year = int(m.group(3))
        month = MONTH_MAP.get(m_name)
        if month and 1 <= day <= 31 and 1800 <= year <= 2100:
            return f"{day:02d}/{month:02d}/{year:04d}"

    # 2. Named month: Month Day Year (e.g. March 5, 2001, मार्च 5, 2001)
    pat_mdy = re.compile(
        rf"(?i)(?:^|[^\d])({_MONTH_PATTERN})[\s,-]+(\d{{1,2}})(?:st|nd|rd|th)?[\s,-]+(\d{{4}})(?:[^\d]|$)"
    )
    m = pat_mdy.search(text)
    if m:
        m_name = m.group(1).lower()
        day = int(m.group(2))
        year = int(m.group(3))
        month = MONTH_MAP.get(m_name)
        if month and 1 <= day <= 31 and 1800 <= year <= 2100:
            return f"{day:02d}/{month:02d}/{year:04d}"

    # 3. Numeric: DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    pat_num_dmy = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)")
    m = pat_num_dmy.search(text)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3))
        if 1 <= day <= 31 and 1 <= month <= 12 and 1800 <= year <= 2100:
            return f"{day:02d}/{month:02d}/{year:04d}"

    # 4. Numeric: YYYY-MM-DD or YYYY/MM/DD
    pat_num_ymd = re.compile(r"(?<!\d)(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})(?!\d)")
    m = pat_num_ymd.search(text)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        day = int(m.group(3))
        if 1 <= day <= 31 and 1 <= month <= 12 and 1800 <= year <= 2100:
            return f"{day:02d}/{month:02d}/{year:04d}"

    return None


_EXTRACTORS: dict[str, Callable[[str], str | None]] = {
    "pan": extract_pan,
    "aadhaar": extract_aadhaar,
    "mobile": extract_mobile,
    "email": extract_email,
    "pincode": extract_pincode,
    "bank_ifsc": extract_bank_ifsc,
    "epic_number": extract_epic_number,
    "date_of_birth": extract_date_of_birth,
}


def extract_values(answer: str, fields: list[str]) -> dict[str, str]:
    """Extract structured values from user answer for requested fields.

    Converts Devanagari digits to ASCII before extraction. Free-text fields
    (names, addresses) are never guessed and left for the agent to resolve.
    """
    if not answer or not fields:
        return {}

    normalized_text = devanagari_to_ascii(str(answer))
    extracted: dict[str, str] = {}

    for field in fields:
        extractor = _EXTRACTORS.get(field)
        if extractor is not None:
            val = extractor(normalized_text)
            if val is not None:
                extracted[field] = val

    return extracted


__all__ = [
    "devanagari_to_ascii",
    "extract_aadhaar",
    "extract_bank_ifsc",
    "extract_date_of_birth",
    "extract_email",
    "extract_epic_number",
    "extract_mobile",
    "extract_pan",
    "extract_pincode",
    "extract_values",
]
