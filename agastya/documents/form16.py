"""Form 16 document parsing and data extraction."""

from __future__ import annotations

import io
import logging
import re
from typing import Any

import pypdf

logger = logging.getLogger(__name__)


def parse_indian_number(val: Any) -> int | None:
    """Parse an Indian-formatted number string into an integer rupee amount.

    Supports:
    - Standard commas: '1,250,000'
    - Indian grouping commas: '12,50,000'
    - Currency prefixes/suffixes: 'Rs. 1,250,000.00', '₹ 75,000', '1,250,000/-'
    - Decimals (rounds to nearest rupee): '12,50,000.50' -> 1250001
    - Zero values: '0', '0.00', 'Rs. 0'
    - Null / placeholder values return None: '-', '—', 'N/A', 'nil', None
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(round(val))

    s = str(val).strip()
    # Strip currency symbols, currency abbreviations, whitespace, and dashes/slashes
    s = re.sub(r"[₹\s]|Rs\.?|INR|/-", "", s, flags=re.IGNORECASE)

    if not s or s in ("-", "—", "N/A", "NA", "nil", "NIL", "none", "None", "."):
        return None

    # Handle optional decimal point
    if "." in s:
        parts = s.split(".")
        int_str = parts[0].replace(",", "")
        dec_str = parts[1][:2] if len(parts) > 1 else "0"
        try:
            return int(round(float(f"{int_str}.{dec_str}")))
        except ValueError:
            return None

    cleaned = s.replace(",", "")
    try:
        return int(round(float(cleaned)))
    except ValueError:
        return None


def _extract_amount_after(text: str, pattern: str) -> int | None:
    """Search for pattern matches and extract the first valid rupee amount following any match."""
    for m in re.finditer(pattern, text, re.IGNORECASE):
        sub = text[m.end() :]
        newline_pos = sub.find("\n")
        line_sub = sub[:newline_pos] if newline_pos != -1 else sub[:200]

        # Priority 1: explicitly marked with colon or currency symbol (e.g. ": Rs. 1,250,000" or ": 1,250,000")
        cm = re.search(r"(?:[:₹]|Rs\.?)\s*([0-9][0-9,\.]*(?!\w))", line_sub, re.IGNORECASE)
        if cm:
            val = parse_indian_number(cm.group(1))
            if val is not None:
                return val

        # Priority 2: standalone number not part of alphanumeric tokens (e.g. avoids '12BA', '115BAC')
        nums = re.findall(
            r"(?<![A-Za-z0-9])([0-9]{1,3}(?:,[0-9]{2,3})*(?:\.[0-9]+)?|[0-9]+)(?![A-Za-z0-9])",
            line_sub,
        )
        for n in nums:
            val = parse_indian_number(n)
            if val is not None:
                return val

    return None


def _is_form16_text(text: str) -> bool:
    """Heuristic check to determine if text belongs to an Indian Form 16."""
    text_lower = text.lower()
    if "form no. 16" in text_lower or "form 16" in text_lower:
        return True
    if "certificate under section 203" in text_lower:
        return True

    # Count matching key Form 16 section labels
    indicators = [
        r"17\s*\(\s*1\s*\)",
        r"17\s*\(\s*2\s*\)",
        r"16\s*\(\s*i?a\s*\)",
        r"tan\s*(?:of|:)",
        r"pan\s*(?:of\s+(?:the\s+)?employee|:)",
    ]
    matched = sum(1 for pat in indicators if re.search(pat, text_lower))
    return matched >= 2


def extract_form16(pdf_bytes: bytes) -> dict[str, Any]:
    """Extract structured tax data from Form 16 PDF bytes.

    Returns:
        A dictionary with Form 16 fields, or {'kind': 'unknown', 'text_excerpt': ...}
        if the layout is unrecognized. Never raises exceptions on unknown layouts.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages_text: list[str] = []
        for page in reader.pages:
            try:
                t = page.extract_text()
                if t:
                    pages_text.append(t)
            except Exception as e:
                logger.debug("Failed extracting text from page: %s", e)
        full_text = "\n".join(pages_text)
    except Exception as e:
        logger.warning("Failed to parse PDF bytes: %s", e)
        # Attempt fallback to raw byte excerpt
        text_excerpt = pdf_bytes[:500].decode("utf-8", errors="ignore")
        return {"kind": "unknown", "text_excerpt": text_excerpt}

    if not full_text.strip() or not _is_form16_text(full_text):
        return {"kind": "unknown", "text_excerpt": full_text[:500]}

    # 1. Assessment Year
    m_ay = re.search(
        r"Assessment\s+Year\s*[:\-]?\s*([0-9]{4}\s*[-/]\s*[0-9]{2,4})",
        full_text,
        re.IGNORECASE,
    )
    ay = m_ay.group(1).replace(" ", "") if m_ay else None

    # 2. Employer TAN (4 letters, 5 digits, 1 letter)
    m_tan = re.search(
        r"TAN\s*(?:of\s+(?:the\s+)?Employer)?\s*[:\-]?\s*([A-Z]{4}[0-9]{5}[A-Z])",
        full_text,
        re.IGNORECASE,
    )
    if not m_tan:
        # Fallback TAN search
        m_tan = re.search(r"\b([A-Z]{4}[0-9]{5}[A-Z])\b", full_text)
    tan = m_tan.group(1).upper() if m_tan else None

    # 3. Employee PAN (5 letters, 4 digits, 1 letter)
    m_pan = re.search(
        r"PAN\s*(?:of\s+(?:the\s+)?Employee)\s*[:\-]?\s*([A-Z]{5}[0-9]{4}[A-Z])",
        full_text,
        re.IGNORECASE,
    )
    if not m_pan:
        # Find PAN after 'Employee'
        emp_idx = full_text.lower().find("employee")
        if emp_idx != -1:
            m_pan = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", full_text[emp_idx:])
    employee_pan = m_pan.group(1).upper() if m_pan else None

    # 4. Employer Name
    m_emp = re.search(
        r"Name\s+(?:and\s+address\s+)?of\s+(?:the\s+)?Employer\s*[:\-]?\s*([^\n\r]+?)(?=\s+Name\s+of|\s+Address:|\s+PAN:|\n|$)",
        full_text,
        re.IGNORECASE,
    )
    employer_name = m_emp.group(1).strip() if m_emp else None

    # 5. Employee Name
    m_ee = re.search(
        r"Name\s+(?:and\s+address\s+)?of\s+(?:the\s+)?Employee\s*[:\-]?\s*([^\n\r]+?)(?=\s+Address:|\s+PAN:|\n|$)",
        full_text,
        re.IGNORECASE,
    )
    employee_name = m_ee.group(1).strip() if m_ee else None

    # 6. Period From and Period To
    m_period = re.search(
        r"Period[^\n\r]*?(?:From\s+)?([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\s+(?:to|To|-)\s+([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
        full_text,
        re.IGNORECASE,
    )
    period_from = m_period.group(1) if m_period else None
    period_to = m_period.group(2) if m_period else None

    # 7. Salary 17(1)
    salary_17_1 = _extract_amount_after(full_text, r"17\s*\(\s*1\s*\)")

    # 8. Perquisites 17(2)
    perquisites_17_2 = _extract_amount_after(full_text, r"17\s*\(\s*2\s*\)")

    # 9. Profits 17(3)
    profits_17_3 = _extract_amount_after(full_text, r"17\s*\(\s*3\s*\)")

    # 10. Gross Salary
    gross_salary = _extract_amount_after(full_text, r"Total\s+Gross\s+Salary")
    if gross_salary is None:
        gross_salary = _extract_amount_after(full_text, r"Gross\s+Salary")

    # 11. HRA Exempt 10(13A)
    hra_exempt_10_13a = _extract_amount_after(full_text, r"10\s*\(\s*13A\s*\)")

    # 12. Standard Deduction 16(ia)
    standard_deduction_16ia = _extract_amount_after(full_text, r"16\s*\(\s*i?a\s*\)")

    # 13. Professional Tax 16(iii)
    professional_tax_16iii = _extract_amount_after(
        full_text, r"(?:16\s*\(\s*iii\s*\)|Professional\s+tax\s+under\s+section\s+16\(iii\)|Tax\s+on\s+employment\s*/\s*Professional\s+tax|Professional\s+tax)"
    )

    # 14. Income Chargeable under Salaries
    income_chargeable_salaries = _extract_amount_after(
        full_text, r"Income\s+chargeable\s+under\s+(?:the\s+head\s+)?['\"]?Salaries['\"]?"
    )

    # 15. Chapter VI-A Deductions
    deduction_80c = _extract_amount_after(full_text, r"(?:Section\s+|Sec\.?\s*)?80\s*C\b")
    deduction_80d = _extract_amount_after(full_text, r"(?:Section\s+|Sec\.?\s*)?80\s*D\b")

    # 16. Total Taxable Income
    total_taxable_income = _extract_amount_after(full_text, r"Total\s+taxable\s+income")

    # 17. TDS Total
    tds_total = _extract_amount_after(
        full_text,
        r"(?:Total\s+amount\s+of\s+tax\s+deducted|Total\s+tax\s+deducted|TDS\s+Total\b)",
    )

    # 18. Quarterly Table
    quarters: list[dict[str, Any]] = []
    for q_num in ("1", "2", "3", "4"):
        q_label = f"Q{q_num}"
        qm = re.search(rf"\bQ{q_num}\b[^\n\r]*", full_text, re.IGNORECASE)
        if qm:
            line = qm.group(0)
            curr_amounts = re.findall(
                r"(?:Rs\.?|₹)\s*([0-9,]+(?:\.[0-9]+)?)", line, re.IGNORECASE
            )
            if len(curr_amounts) >= 2:
                q_paid = parse_indian_number(curr_amounts[0])
                q_tds = parse_indian_number(curr_amounts[1])
            else:
                fallback_amounts = re.findall(
                    r"(?<![A-Za-z0-9\-])([0-9]{1,3}(?:,[0-9]{2,3})+(?:\.[0-9]+)?|[0-9]{4,})(?![A-Za-z0-9\-])",
                    line,
                )
                q_paid = (
                    parse_indian_number(fallback_amounts[0])
                    if len(fallback_amounts) > 0
                    else None
                )
                q_tds = (
                    parse_indian_number(fallback_amounts[1])
                    if len(fallback_amounts) > 1
                    else None
                )
            quarters.append({"q": q_label, "amount_paid": q_paid, "tds": q_tds})
        else:
            quarters.append({"q": q_label, "amount_paid": None, "tds": None})

    return {
        "kind": "form16",
        "assessment_year": ay,
        "employer_name": employer_name,
        "employer_tan": tan,
        "employee_pan": employee_pan,
        "employee_name": employee_name,
        "period_from": period_from,
        "period_to": period_to,
        "gross_salary": gross_salary,
        "salary_17_1": salary_17_1,
        "perquisites_17_2": perquisites_17_2,
        "profits_17_3": profits_17_3,
        "hra_exempt_10_13a": hra_exempt_10_13a,
        "standard_deduction_16ia": standard_deduction_16ia,
        "professional_tax_16iii": professional_tax_16iii,
        "income_chargeable_salaries": income_chargeable_salaries,
        "deduction_80c": deduction_80c,
        "deduction_80d": deduction_80d,
        "total_taxable_income": total_taxable_income,
        "tds_total": tds_total,
        "quarters": quarters,
    }
