"""Tests for Form 16 extraction and document store."""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
import pytest

from fpdf import FPDF
from agastya.documents.form16 import extract_form16, parse_indian_number
from agastya.documents.store import Document, DocumentStore
from scripts.make_sample_form16 import build_form16_pdf, generate_sample_form16


def test_parse_indian_number():
    """Test parse_indian_number handles standard, Indian grouping, currency symbols, and edge cases."""
    # Standard and Indian grouping
    assert parse_indian_number("12,50,000") == 1250000
    assert parse_indian_number("1,250,000") == 1250000
    assert parse_indian_number("75,000") == 75000
    assert parse_indian_number("2,500") == 2500

    # Currency symbols and suffixes
    assert parse_indian_number("Rs. 1,250,000.00") == 1250000
    assert parse_indian_number("Rs 12,50,000") == 1250000
    assert parse_indian_number("₹ 75,000") == 75000
    assert parse_indian_number("₹2,500/-") == 2500
    assert parse_indian_number("INR 11,97,500") == 1197500

    # Decimals and rounding
    assert parse_indian_number("12,50,000.40") == 1250000
    assert parse_indian_number("12,50,000.60") == 1250001

    # Zero values
    assert parse_indian_number("0") == 0
    assert parse_indian_number("0.00") == 0
    assert parse_indian_number("Rs. 0") == 0
    assert parse_indian_number(0) == 0

    # Numeric inputs
    assert parse_indian_number(1250000) == 1250000
    assert parse_indian_number(75000.0) == 75000

    # Missing / placeholder / invalid values
    assert parse_indian_number(None) is None
    assert parse_indian_number("") is None
    assert parse_indian_number("-") is None
    assert parse_indian_number("—") is None
    assert parse_indian_number("N/A") is None
    assert parse_indian_number("nil") is None
    assert parse_indian_number("NIL") is None
    assert parse_indian_number("None") is None
    assert parse_indian_number("invalid_string") is None


def test_extract_form16_from_sample():
    """Test Form 16 extraction from the generated specimen Form 16."""
    pdf_bytes = build_form16_pdf()
    extracted = extract_form16(pdf_bytes)

    assert extracted["kind"] == "form16"
    assert extracted["assessment_year"] == "2026-27"
    assert extracted["employer_name"] == "Demo Infotech Private Limited"
    assert extracted["employer_tan"] == "DELD12345E"
    assert extracted["employee_name"] == "TEST KUMAR DEMO"
    assert extracted["employee_pan"] == "ABCDE1234F"
    assert extracted["period_from"] == "01-Apr-2025"
    assert extracted["period_to"] == "31-Mar-2026"

    # Salary breakdown Part B
    assert extracted["salary_17_1"] == 1250000
    assert extracted["perquisites_17_2"] == 25000
    assert extracted["profits_17_3"] == 0
    assert extracted["gross_salary"] == 1275000
    assert extracted["hra_exempt_10_13a"] == 0
    assert extracted["standard_deduction_16ia"] == 75000
    assert extracted["professional_tax_16iii"] == 2500
    assert extracted["income_chargeable_salaries"] == 1197500
    assert extracted["deduction_80c"] == 150000
    assert extracted["deduction_80d"] == 0
    assert extracted["total_taxable_income"] == 1197500
    assert extracted["tds_total"] == 82810

    # Quarters
    quarters = extracted["quarters"]
    assert len(quarters) == 4
    assert quarters[0] == {"q": "Q1", "amount_paid": 318750, "tds": 20702}
    assert quarters[1] == {"q": "Q2", "amount_paid": 318750, "tds": 20702}
    assert quarters[2] == {"q": "Q3", "amount_paid": 318750, "tds": 20703}
    assert quarters[3] == {"q": "Q4", "amount_paid": 318750, "tds": 20703}


def test_extract_form16_disk_sample():
    """Verify demo_portals/samples/form16_demo_test_kumar.pdf matches expected values."""
    sample_path = Path("demo_portals/samples/form16_demo_test_kumar.pdf")
    if not sample_path.exists():
        generate_sample_form16(sample_path)
    pdf_bytes = sample_path.read_bytes()
    res = extract_form16(pdf_bytes)
    assert res["kind"] == "form16"
    assert res["employee_pan"] == "ABCDE1234F"
    assert res["employer_tan"] == "DELD12345E"
    assert res["salary_17_1"] == 1250000
    assert res["gross_salary"] == 1275000
    assert res["total_taxable_income"] == 1197500
    assert res["tds_total"] == 82810


def test_extract_form16_unknown_pdf():
    """Test extract_form16 gracefully handles an unknown / non-Form-16 PDF without raising."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Electricity Bill - Customer Copy")
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 10, "Consumer No: 99881122 Amount Due: Rs. 1,450 Due Date: 12-Nov-2026")
    raw_pdf = bytes(pdf.output())

    res = extract_form16(raw_pdf)
    assert res["kind"] == "unknown"
    assert "Electricity Bill" in res["text_excerpt"]
    assert len(res["text_excerpt"]) <= 500


def test_extract_form16_corrupt_bytes():
    """Test extract_form16 handles completely corrupt / non-PDF data without raising."""
    corrupt_data = b"This is not a PDF file at all. Just arbitrary raw text."
    res = extract_form16(corrupt_data)
    assert res["kind"] == "unknown"
    assert "This is not a PDF file" in res["text_excerpt"]


def test_document_store_lifecycle():
    """Test DocumentStore add, get, list, and multi-user isolation."""
    with tempfile.TemporaryDirectory() as td:
        temp_dir = Path(td)
        store_user1 = DocumentStore(user_id="user_alpha", data_dir=temp_dir)
        store_user2 = DocumentStore(user_id="user_beta", data_dir=temp_dir)

        # Initially empty
        assert store_user1.list() == []
        assert store_user2.list() == []

        # Add Form 16 PDF to user1
        f16_bytes = build_form16_pdf()
        doc1 = store_user1.add("form16_sample.pdf", f16_bytes)

        assert isinstance(doc1, Document)
        assert doc1.user_id == "user_alpha"
        assert doc1.filename == "form16_sample.pdf"
        assert doc1.kind == "form16"
        assert doc1.extracted["salary_17_1"] == 1250000
        assert Path(doc1.path).exists()
        assert Path(doc1.path).read_bytes() == f16_bytes

        # Add unknown document to user1
        unknown_bytes = b"Grocery list: Milk, Eggs, Bread"
        doc2 = store_user1.add("notes.txt", unknown_bytes)
        assert doc2.kind == "unknown"
        assert doc2.extracted["kind"] == "unknown"

        # List user1 documents (should have 2, ordered latest first)
        u1_docs = store_user1.list()
        assert len(u1_docs) == 2
        assert u1_docs[0].id == doc2.id
        assert u1_docs[1].id == doc1.id

        # Get existing document
        retrieved = store_user1.get(doc1.id)
        assert retrieved is not None
        assert retrieved.id == doc1.id
        assert retrieved.extracted["gross_salary"] == 1275000

        # Get non-existent document
        assert store_user1.get("non_existent_id") is None

        # Verify user2 cannot see user1 documents (isolation)
        assert store_user2.list() == []
        assert store_user2.get(doc1.id) is None
