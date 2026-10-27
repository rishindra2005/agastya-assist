"""Tests for agastya.profile store and fields catalog."""

from pathlib import Path
import pytest

from agastya.profile.fields import (
    FIELD_CATALOG,
    FIELDS,
    mask_aadhaar,
    mask_bank_account,
    mask_pan,
    masked,
)
from agastya.profile.store import ProfileStore


def test_field_catalog_completeness() -> None:
    expected_fields = [
        "full_name",
        "father_name",
        "mother_name",
        "date_of_birth",
        "gender",
        "mobile",
        "email",
        "pan",
        "aadhaar",
        "address_line1",
        "address_line2",
        "city",
        "district",
        "state",
        "pincode",
        "epic_number",
        "assembly_constituency",
        "occupation",
        "annual_income",
        "bank_account_number",
        "bank_ifsc",
        "din",
        "company_name",
        "cin",
    ]
    for field in expected_fields:
        assert field in FIELDS, f"Field '{field}' missing from catalog"
        info = FIELDS[field]
        assert "label_en" in info and info["label_en"]
        assert "label_hi" in info and info["label_hi"]
        assert "description" in info and info["description"]
        assert "sensitive" in info and isinstance(info["sensitive"], bool)

    assert FIELD_CATALOG is FIELDS


def test_sensitive_fields_flagged() -> None:
    sensitive_keys = {k for k, v in FIELDS.items() if v.sensitive}
    assert sensitive_keys == {"pan", "aadhaar", "bank_account_number"}


def test_mask_helpers() -> None:
    assert mask_pan("ABCDE1234F") == "XXXXX1234X"
    assert mask_aadhaar("123456789012") == "XXXXXXXX9012"
    assert mask_bank_account("12345678901234") == "XXXXXXXXXX1234"

    # masked display helper
    assert masked("pan", "ABCDE1234F") == "XXXXX1234X"
    assert masked("aadhaar", "123456789012") == "XXXXXXXX9012"
    assert masked("bank_account_number", "9876543210") == "XXXXXX3210"
    assert masked("full_name", "Jane Doe") == "Jane Doe"


def test_store_crud_and_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "test_profile.db"
    store = ProfileStore(db_path=db_path)

    # Initial get returns None
    assert store.get("full_name") is None
    assert store.get_record("full_name") is None

    # Set and get
    store.set("full_name", "Rohit Kumar", source="user")
    assert store.get("full_name") == "Rohit Kumar"

    record = store.get_record("full_name")
    assert record is not None
    assert record["value"] == "Rohit Kumar"
    assert record["source"] == "user"
    assert "updated_at" in record

    # Update value and source
    store.set("full_name", "Rohit S. Kumar", source="portal")
    assert store.get("full_name") == "Rohit S. Kumar"
    record2 = store.get_record("full_name")
    assert record2["value"] == "Rohit S. Kumar"
    assert record2["source"] == "portal"

    # all() and all_records()
    store.set("city", "Bengaluru")
    all_data = store.all()
    assert all_data == {
        "city": "Bengaluru",
        "full_name": "Rohit S. Kumar",
    }

    all_records = store.all_records()
    assert "city" in all_records
    assert all_records["city"]["value"] == "Bengaluru"

    # delete
    assert store.delete("city") is True
    assert store.get("city") is None
    assert store.delete("city") is False

    store.close()


def test_store_missing(tmp_path: Path) -> None:
    store = ProfileStore(data_dir=tmp_path)
    store.set("full_name", "Priya Verma")
    store.set("mobile", "9876543210")

    required = ["full_name", "pan", "mobile", "aadhaar", "city"]
    missing = store.missing(required)
    assert missing == ["pan", "aadhaar", "city"]


def test_store_multi_user(tmp_path: Path) -> None:
    db_file = tmp_path / "multi_user.db"
    store_u1 = ProfileStore(user_id="user_1", db_path=db_file)
    store_u2 = ProfileStore(user_id="user_2", db_path=db_file)

    store_u1.set("full_name", "User One")
    store_u2.set("full_name", "User Two")

    assert store_u1.get("full_name") == "User One"
    assert store_u2.get("full_name") == "User Two"

    assert store_u1.all() == {"full_name": "User One"}
    assert store_u2.all() == {"full_name": "User Two"}

    store_u1.delete("full_name")
    assert store_u1.get("full_name") is None
    assert store_u2.get("full_name") == "User Two"


def test_store_masked_method(tmp_path: Path) -> None:
    store = ProfileStore(data_dir=tmp_path)
    assert store.masked("pan") is None

    store.set("pan", "ABCDE1234F")
    store.set("aadhaar", "123456789012")
    store.set("full_name", "Amit Shah")

    assert store.masked("pan") == "XXXXX1234X"
    assert store.masked("aadhaar") == "XXXXXXXX9012"
    assert store.masked("full_name") == "Amit Shah"


def test_store_validation_success_and_failures(tmp_path: Path) -> None:
    store = ProfileStore(data_dir=tmp_path)

    # PAN validation
    store.set("pan", "ABCDE1234F")
    with pytest.raises(ValueError, match="Invalid value for field 'pan'"):
        store.set("pan", "INVALID_PAN")
    with pytest.raises(ValueError, match="Invalid value for field 'pan'"):
        store.set("pan", "12345ABCDE")

    # Aadhaar validation (12 digits)
    store.set("aadhaar", "123456789012")
    with pytest.raises(ValueError, match="Invalid value for field 'aadhaar'"):
        store.set("aadhaar", "12345")
    with pytest.raises(ValueError, match="Invalid value for field 'aadhaar'"):
        store.set("aadhaar", "1234567890123")
    with pytest.raises(ValueError, match="Invalid value for field 'aadhaar'"):
        store.set("aadhaar", "12345678901a")

    # Mobile validation (10 digits starting 6-9)
    store.set("mobile", "9876543210")
    store.set("mobile", "8123456789")
    store.set("mobile", "7000000000")
    store.set("mobile", "6123456789")
    with pytest.raises(ValueError, match="Invalid value for field 'mobile'"):
        store.set("mobile", "5123456789")
    with pytest.raises(ValueError, match="Invalid value for field 'mobile'"):
        store.set("mobile", "987654321")
    with pytest.raises(ValueError, match="Invalid value for field 'mobile'"):
        store.set("mobile", "98765432100")

    # PIN code validation (6 digits)
    store.set("pincode", "110001")
    with pytest.raises(ValueError, match="Invalid value for field 'pincode'"):
        store.set("pincode", "11000")
    with pytest.raises(ValueError, match="Invalid value for field 'pincode'"):
        store.set("pincode", "1100001")
    with pytest.raises(ValueError, match="Invalid value for field 'pincode'"):
        store.set("pincode", "11000a")

    # Bank IFSC validation (4 letters, 0, 6 alphanumeric)
    store.set("bank_ifsc", "SBIN0001234")
    store.set("bank_ifsc", "HDFC0000123")
    with pytest.raises(ValueError, match="Invalid value for field 'bank_ifsc'"):
        store.set("bank_ifsc", "SBIN1001234")
    with pytest.raises(ValueError, match="Invalid value for field 'bank_ifsc'"):
        store.set("bank_ifsc", "SBI0001234")

    # EPIC number validation (3 letters + 7 digits)
    store.set("epic_number", "ABC1234567")
    store.set("epic_number", "XYZ9876543")
    with pytest.raises(ValueError, match="Invalid value for field 'epic_number'"):
        store.set("epic_number", "AB1234567")
    with pytest.raises(ValueError, match="Invalid value for field 'epic_number'"):
        store.set("epic_number", "1234567890")
    with pytest.raises(ValueError, match="Invalid value for field 'epic_number'"):
        store.set("epic_number", "ABCD1234567")

    # Fields without validators should accept standard values
    store.set("full_name", "Vikramaditya")
    store.set("annual_income", "1200000")
    assert store.get("full_name") == "Vikramaditya"
    assert store.get("annual_income") == "1200000"


@pytest.mark.parametrize(
    "field,raw,expected",
    [
        ("pan", "abcde 1234 f", "ABCDE1234F"),
        ("aadhaar", "1234 5678 9012", "123456789012"),
        ("aadhaar", "1234-5678-9012", "123456789012"),
        ("mobile", "+91 98765 43210", "9876543210"),
        ("mobile", "09876543210", "9876543210"),
        ("bank_ifsc", "sbin0001234", "SBIN0001234"),
        ("epic_number", "abc1234567", "ABC1234567"),
    ],
)
def test_set_normalises_spoken_input(tmp_path, field, raw, expected):
    store = ProfileStore(db_path=tmp_path / "p.db")
    store.set(field, raw)
    assert store.get(field) == expected


@pytest.mark.parametrize("blank", ["", "   "])
def test_set_rejects_blank_values(tmp_path, blank):
    store = ProfileStore(db_path=tmp_path / "p.db")
    with pytest.raises(ValueError):
        store.set("gender", blank)
    assert store.get("gender") is None
