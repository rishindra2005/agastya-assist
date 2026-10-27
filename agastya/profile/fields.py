"""Canonical field catalog for Agastya Assist profile management."""

import re
from typing import Any


class FieldInfo(dict):
    """Metadata describing a profile field, supporting both dict and attribute access."""

    @property
    def label_en(self) -> str:
        return self["label_en"]

    @property
    def label_hi(self) -> str:
        return self["label_hi"]

    @property
    def description(self) -> str:
        return self["description"]

    @property
    def sensitive(self) -> bool:
        return bool(self["sensitive"])

    @property
    def validator(self) -> str | None:
        return self.get("validator")

    def masked(self, value: str) -> str:
        return masked(self.get("name", ""), value)


def mask_pan(val: str) -> str:
    """Mask PAN: Income Tax portal format (e.g. ABCDE1234F -> XXXXX1234X)."""
    cleaned = str(val).strip()
    if len(cleaned) == 10:
        return f"XXXXX{cleaned[5:9]}X"
    if len(cleaned) > 4:
        return "X" * (len(cleaned) - 4) + cleaned[-4:]
    return "X" * len(cleaned)


def mask_aadhaar(val: str) -> str:
    """Mask Aadhaar: UIDAI format masking first 8 digits (e.g. 123456789012 -> XXXXXXXX9012)."""
    cleaned = str(val).strip().replace(" ", "").replace("-", "")
    if len(cleaned) == 12:
        return f"XXXXXXXX{cleaned[-4:]}"
    if len(cleaned) > 4:
        return "X" * (len(cleaned) - 4) + cleaned[-4:]
    return "X" * len(cleaned)


def mask_bank_account(val: str) -> str:
    """Mask bank account number showing only last 4 digits."""
    cleaned = str(val).strip().replace(" ", "").replace("-", "")
    if len(cleaned) > 4:
        return "X" * (len(cleaned) - 4) + cleaned[-4:]
    return "X" * len(cleaned)


def masked(field_or_val: str, val_or_field: str | None = None) -> str:
    """Display helper that returns a masked representation for sensitive fields.

    Usage:
        masked("aadhaar", "123456789012") -> "XXXXXXXX9012"
        masked("pan", "ABCDE1234F") -> "XXXXX1234X"
        masked("bank_account_number", "1234567890") -> "XXXXXX7890"
    """
    if val_or_field is None:
        # Single argument: try to infer field type from value pattern
        val = str(field_or_val).strip()
        if re.match(r"^[0-9]{12}$", val):
            return mask_aadhaar(val)
        if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", val, re.IGNORECASE):
            return mask_pan(val)
        if len(val) > 4:
            return "X" * (len(val) - 4) + val[-4:]
        return "X" * len(val)

    # Two arguments: determine which is field and which is value
    if field_or_val in FIELDS or field_or_val in ("pan", "aadhaar", "bank_account_number"):
        field, val = field_or_val, str(val_or_field).strip()
    elif val_or_field in FIELDS or val_or_field in ("pan", "aadhaar", "bank_account_number"):
        field, val = val_or_field, str(field_or_val).strip()
    else:
        field, val = field_or_val, str(val_or_field).strip()

    if field == "aadhaar":
        return mask_aadhaar(val)
    elif field == "pan":
        return mask_pan(val)
    elif field == "bank_account_number":
        return mask_bank_account(val)

    # For other fields, return value as-is
    return val


_UPPER_FIELDS = {"pan", "epic_number", "bank_ifsc", "din", "cin"}
_DIGIT_FIELDS = {"aadhaar", "pincode", "mobile", "bank_account_number"}


def normalize_field(field: str, value: Any) -> str:
    """Normalise spoken/typed input (e.g. 'abcde 1234 f' -> 'ABCDE1234F')."""
    val = str(value).strip()
    if field in _UPPER_FIELDS:
        val = re.sub(r"[\s-]", "", val).upper()
    elif field in _DIGIT_FIELDS:
        val = re.sub(r"[\s-]", "", val)
        if field == "mobile":
            val = re.sub(r"^(\+?91|0)(?=[6-9][0-9]{9}$)", "", val)
    return val


def validate_field(field: str, value: Any) -> None:
    """Validate a field value against the catalog validator if present.

    Raises ValueError on invalid values.
    """
    if field not in FIELDS:
        return

    pattern = FIELDS[field].get("validator")
    if not pattern:
        return

    if value is None:
        raise ValueError(f"Value for '{field}' cannot be None.")

    val_str = str(value).strip()
    if not re.match(pattern, val_str):
        raise ValueError(
            f"Invalid value for field '{field}': '{value}'. "
            f"Expected pattern: {pattern}"
        )


FIELDS: dict[str, FieldInfo] = {
    "full_name": FieldInfo(
        name="full_name",
        label_en="Full Name",
        label_hi="पूरा नाम",
        description="Full legal name as per official government records",
        sensitive=False,
        validator=None,
    ),
    "father_name": FieldInfo(
        name="father_name",
        label_en="Father's Name",
        label_hi="पिता का नाम",
        description="Father's full name",
        sensitive=False,
        validator=None,
    ),
    "mother_name": FieldInfo(
        name="mother_name",
        label_en="Mother's Name",
        label_hi="माता का नाम",
        description="Mother's full name",
        sensitive=False,
        validator=None,
    ),
    "date_of_birth": FieldInfo(
        name="date_of_birth",
        label_en="Date of Birth",
        label_hi="जन्म तिथि",
        description="Date of birth in DD/MM/YYYY or YYYY-MM-DD format",
        sensitive=False,
        validator=None,
    ),
    "gender": FieldInfo(
        name="gender",
        label_en="Gender",
        label_hi="लिंग",
        description="Gender (Male / Female / Transgender)",
        sensitive=False,
        validator=None,
    ),
    "mobile": FieldInfo(
        name="mobile",
        label_en="Mobile Number",
        label_hi="मोबाइल नंबर",
        description="10-digit Indian mobile number starting with 6, 7, 8, or 9",
        sensitive=False,
        validator=r"^[6-9][0-9]{9}$",
    ),
    "email": FieldInfo(
        name="email",
        label_en="Email Address",
        label_hi="ईमेल",
        description="Personal or official email address",
        sensitive=False,
        validator=None,
    ),
    "pan": FieldInfo(
        name="pan",
        label_en="Permanent Account Number (PAN)",
        label_hi="पैन नंबर",
        description="10-character alphanumeric PAN issued by Income Tax Department (e.g. ABCDE1234F)",
        sensitive=True,
        validator=r"^[A-Z]{5}[0-9]{4}[A-Z]$",
    ),
    "aadhaar": FieldInfo(
        name="aadhaar",
        label_en="Aadhaar Number",
        label_hi="आधार नंबर",
        description="12-digit unique identity number issued by UIDAI",
        sensitive=True,
        validator=r"^[0-9]{12}$",
    ),
    "address_line1": FieldInfo(
        name="address_line1",
        label_en="Address Line 1",
        label_hi="पता पंक्ति 1",
        description="House/Flat/Premises number and street name",
        sensitive=False,
        validator=None,
    ),
    "address_line2": FieldInfo(
        name="address_line2",
        label_en="Address Line 2",
        label_hi="पता पंक्ति 2",
        description="Locality, area, or landmark",
        sensitive=False,
        validator=None,
    ),
    "city": FieldInfo(
        name="city",
        label_en="City / Town",
        label_hi="शहर / कस्बा",
        description="City, town, or village name",
        sensitive=False,
        validator=None,
    ),
    "district": FieldInfo(
        name="district",
        label_en="District",
        label_hi="ज़िला",
        description="District name",
        sensitive=False,
        validator=None,
    ),
    "state": FieldInfo(
        name="state",
        label_en="State / Union Territory",
        label_hi="राज्य / केंद्र शासित प्रदेश",
        description="Indian state or union territory",
        sensitive=False,
        validator=None,
    ),
    "pincode": FieldInfo(
        name="pincode",
        label_en="PIN Code",
        label_hi="पिन कोड",
        description="6-digit postal index number",
        sensitive=False,
        validator=r"^[0-9]{6}$",
    ),
    "epic_number": FieldInfo(
        name="epic_number",
        label_en="Voter ID (EPIC Number)",
        label_hi="मतदाता पहचान पत्र संख्या (EPIC)",
        description="Electors Photo Identity Card number (3 letters followed by 7 digits, e.g. ABC1234567)",
        sensitive=False,
        validator=r"^[A-Z]{3}[0-9]{7}$",
    ),
    "assembly_constituency": FieldInfo(
        name="assembly_constituency",
        label_en="Assembly Constituency",
        label_hi="विधानसभा निर्वाचन क्षेत्र",
        description="Legislative assembly constituency name or number",
        sensitive=False,
        validator=None,
    ),
    "occupation": FieldInfo(
        name="occupation",
        label_en="Occupation",
        label_hi="व्यवसाय",
        description="Profession, employment, or business activity",
        sensitive=False,
        validator=None,
    ),
    "annual_income": FieldInfo(
        name="annual_income",
        label_en="Annual Income",
        label_hi="वार्षिक आय",
        description="Annual income in Indian Rupees (INR)",
        sensitive=False,
        validator=None,
    ),
    "bank_account_number": FieldInfo(
        name="bank_account_number",
        label_en="Bank Account Number",
        label_hi="बैंक खाता संख्या",
        description="Bank account number for refunds or transactions",
        sensitive=True,
        validator=None,
    ),
    "bank_ifsc": FieldInfo(
        name="bank_ifsc",
        label_en="Bank IFSC Code",
        label_hi="बैंक IFSC कोड",
        description="11-character Indian Financial System Code (e.g. SBIN0001234)",
        sensitive=False,
        validator=r"^[A-Z]{4}0[A-Z0-9]{6}$",
    ),
    "din": FieldInfo(
        name="din",
        label_en="Director Identification Number (DIN)",
        label_hi="निदेशक पहचान संख्या (DIN)",
        description="8-digit DIN for company directors registered with MCA",
        sensitive=False,
        validator=None,
    ),
    "company_name": FieldInfo(
        name="company_name",
        label_en="Company Name",
        label_hi="कंपनी का नाम",
        description="Registered company name as per MCA",
        sensitive=False,
        validator=None,
    ),
    "cin": FieldInfo(
        name="cin",
        label_en="Corporate Identification Number (CIN)",
        label_hi="कॉर्पोरेट पहचान संख्या (CIN)",
        description="21-character CIN issued by Registrar of Companies",
        sensitive=False,
        validator=None,
    ),
}

FIELD_CATALOG = FIELDS
