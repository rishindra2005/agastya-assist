"""Generate a realistic specimen Form 16 PDF for demo and automated testing.

The generated PDF includes clear warning headers and diagonal watermarks
indicating it is demo specimen data and NOT a real tax document.
"""

from __future__ import annotations

from pathlib import Path
from fpdf import FPDF, XPos, YPos


class Form16SpecimenPDF(FPDF):
    """FPDF subclass for Form 16 with specimen headers and diagonal watermark."""

    def header(self) -> None:
        # Diagonal background watermark
        with self.rotation(35, x=self.w / 2, y=self.h / 2):
            self.set_text_color(230, 230, 230)
            self.set_font("Helvetica", "B", 24)
            wm_text = "SPECIMEN -- DEMO DATA -- NOT A REAL TAX DOCUMENT"
            wm_w = self.get_string_width(wm_text)
            self.text((self.w - wm_w) / 2, self.h / 2, wm_text)

        # Top specimen banner
        self.set_fill_color(255, 235, 235)
        self.set_draw_color(200, 30, 30)
        self.set_line_width(0.4)
        self.rect(10, 8, self.w - 20, 7, "DF")
        self.set_xy(10, 8)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(180, 0, 0)
        self.cell(
            self.w - 20,
            7,
            "SPECIMEN -- DEMO DATA -- NOT A REAL TAX DOCUMENT",
            align="C",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.ln(3)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(128, 128, 128)
        self.cell(
            0,
            5,
            f"Page {self.page_no()} of {{nb}} | Agastya Assist Specimen Tax Document",
            align="C",
        )


def build_form16_pdf() -> bytes:
    """Construct the specimen Form 16 PDF and return raw bytes."""
    pdf = Form16SpecimenPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)

    # ---------------- PAGE 1: PART A ----------------
    pdf.add_page()
    pdf.set_text_color(0, 0, 0)

    # Title
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 6, "FORM NO. 16", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 4, "[See rule 31(1)(a)]", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 5, "PART A", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.multi_cell(
        0,
        3.5,
        "Certificate under section 203 of the Income-tax Act, 1961 for tax deducted at source on debits to salary",
        align="C",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(2)

    # Certificate Metadata Box
    pdf.set_draw_color(120, 120, 120)
    pdf.set_line_width(0.3)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(95, 5, "Certificate No.: CERT/2026/001928", border="LTB")
    pdf.cell(95, 5, "Last updated on: 15-May-2026", border="RTB", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)

    # Employer / Employee Details Box
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(95, 5, "Name of the Employer: Demo Infotech Private Limited", border="LT")
    pdf.cell(95, 5, "Name of the Employee: TEST KUMAR DEMO", border="RT", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 7.5)
    pdf.cell(95, 4.5, "Address: Plot 42, Tech Park, Okhla Phase III, New Delhi 110001", border="L")
    pdf.cell(95, 4.5, "Address: Flat 402, Shanti Vihar, Mayur Vihar, New Delhi 110091", border="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(95, 5, "PAN of the Employer: AAACD1234E", border="L")
    pdf.cell(95, 5, "Employee Ref No.: EMP-2025-8891", border="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.cell(95, 5, "TAN of the Employer: DELD12345E", border="L")
    pdf.cell(95, 5, "PAN of the Employee: ABCDE1234F", border="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.cell(95, 5, "Assessment Year: 2026-27", border="LB")
    pdf.cell(95, 5, "Period with the Employer: From 01-Apr-2025 to 31-Mar-2026", border="RB", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    # Summary of Tax Deducted at Source (Quarterly table)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 5, "Summary of amount paid/credited and tax deducted at source thereon in respect of the employee:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Table Header
    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(20, 6, "Quarter", border=1, align="C")
    pdf.cell(45, 6, "Receipt Numbers", border=1, align="C")
    pdf.cell(45, 6, "Amount paid/credited (Rs.)", border=1, align="C")
    pdf.cell(40, 6, "Amount of tax deducted (Rs.)", border=1, align="C")
    pdf.cell(40, 6, "Amount of tax deposited (Rs.)", border=1, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Rows Q1 - Q4
    quarterly_data = [
        ("Q1", "2025Q1-098172", "3,18,750", "20,702", "20,702"),
        ("Q2", "2025Q2-088192", "3,18,750", "20,702", "20,702"),
        ("Q3", "2025Q3-077211", "3,18,750", "20,703", "20,703"),
        ("Q4", "2025Q4-066129", "3,18,750", "20,703", "20,703"),
    ]

    pdf.set_font("Helvetica", "", 7.5)
    for q, rec, paid, tds, dep in quarterly_data:
        pdf.cell(20, 5, q, border=1, align="C")
        pdf.cell(45, 5, rec, border=1, align="C")
        pdf.cell(45, 5, f"Rs. {paid}", border=1, align="R")
        pdf.cell(40, 5, f"Rs. {tds}", border=1, align="R")
        pdf.cell(40, 5, f"Rs. {dep}", border=1, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Total Row
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.cell(65, 5, "Total (Rs.)", border=1, align="C")
    pdf.cell(45, 5, "Rs. 12,75,000", border=1, align="R")
    pdf.cell(40, 5, "Rs. 82,810", border=1, align="R")
    pdf.cell(40, 5, "Rs. 82,810", border=1, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    # Verification Block
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 4, "Verification:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 7)
    pdf.multi_cell(
        0,
        3.5,
        "I, Ramesh Sharma, working in the capacity of Director, do hereby certify that a sum of Rs. 82,810 "
        "[Rupees Eighty Two Thousand Eight Hundred Ten only] has been deducted and a sum of Rs. 82,810 has been "
        "deposited to the credit of the Central Government. I further certify that the information given above is true, "
        "complete and correct based on the book of accounts, documents, TDS statements and other available records.",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(1)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.cell(95, 4, "Place: New Delhi")
    pdf.cell(95, 4, "Signature of the person responsible for deduction of tax", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(95, 4, "Date: 15-May-2026")
    pdf.cell(95, 4, "Designation: Director (HR & Finance)", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ---------------- PAGE 2: PART B ----------------
    pdf.add_page()
    pdf.set_text_color(0, 0, 0)

    # Title
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 6, "FORM NO. 16", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 5, "PART B", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.cell(0, 4, "Details of Salary Paid and any other income and tax deducted (u/s 115BAC - New Tax Regime)", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)

    # Part B Table
    # Column widths: Description (145), Amount (45)
    w_desc, w_amt = 145, 45

    def row(desc: str, amt: str, bold: bool = False, fill: bool = False) -> None:
        pdf.set_font("Helvetica", "B" if bold else "", 7.5)
        if fill:
            pdf.set_fill_color(245, 245, 245)
        pdf.cell(w_desc, 4.8, desc, border=1, fill=fill)
        pdf.cell(w_amt, 4.8, amt, border=1, align="R", fill=fill, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    row("1. Gross Salary:", "", bold=True, fill=True)
    row("  (a) Salary as per provisions contained in sec. 17(1)", "Rs. 1,250,000")
    row("  (b) Value of perquisites u/s 17(2) (as per Form No. 12BA)", "Rs. 25,000")
    row("  (c) Profits in lieu of salary under section 17(3)", "Rs. 0")
    row("  (d) Total Gross Salary (a + b + c)", "Rs. 1,275,000", bold=True)

    row("2. Less: Allowance to the extent exempt under section 10:", "", bold=True, fill=True)
    row("  (a) Travel concession or assistance under section 10(5)", "Rs. 0")
    row("  (b) House rent allowance under section 10(13A) (not applicable in new regime)", "Rs. 0")
    row("  Total exemption under section 10", "Rs. 0")

    row("3. Balance (1(d) - 2)", "Rs. 1,275,000", bold=True)

    row("4. Deductions under section 16:", "", bold=True, fill=True)
    row("  (a) Standard deduction under section 16(ia)", "Rs. 75,000")
    row("  (b) Tax on employment / Professional tax under section 16(iii)", "Rs. 2,500")
    row("  Total deductions under section 16", "Rs. 77,500", bold=True)

    row("5. Income chargeable under the head 'Salaries' (3 - 4)", "Rs. 1,197,500", bold=True, fill=True)
    row("6. Any other income reported by the employee", "Rs. 0")
    row("7. Gross total income (5 + 6)", "Rs. 1,197,500", bold=True)

    row("8. Deductions under Chapter VI-A:", "", bold=True, fill=True)
    row("  (a) Section 80C (not applicable in new regime u/s 115BAC)", "Rs. 150,000")
    row("  (b) Section 80D", "Rs. 0")
    row("  Total deductible under Chapter VI-A (opted New Tax Regime)", "Rs. 0", bold=True)

    row("9. Total taxable income (7 - 8)", "Rs. 1,197,500", bold=True, fill=True)

    row("10. Tax computation on total taxable income:", "", bold=True, fill=True)
    row("  Tax on total taxable income (u/s 115BAC slabs)", "Rs. 79,625")
    row("  Rebate under section 87A", "Rs. 0")
    row("  Surcharge", "Rs. 0")
    row("  Health and education cess (4%)", "Rs. 3,185")
    row("  Total tax payable", "Rs. 82,810", bold=True)
    row("  Relief under section 89", "Rs. 0")
    row("11. Net tax payable", "Rs. 82,810", bold=True)
    row("12. Total amount of tax deducted at source (TDS Total)", "Rs. 82,810", bold=True, fill=True)
    pdf.ln(2)

    # Verification Part B
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.cell(0, 4, "Verification by Employer:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 7)
    pdf.multi_cell(
        0,
        3.5,
        "I, Ramesh Sharma, son of Late Shri K. L. Sharma, working in the capacity of Director (HR & Finance) of "
        "Demo Infotech Private Limited, do hereby certify that the information given above is complete, true and correct "
        "and is based on the books of account, documents, Form No. 24Q and other available records of the company.",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(1)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.cell(95, 4, "Place: New Delhi")
    pdf.cell(95, 4, "Signature: Ramesh Sharma", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(95, 4, "Date: 15-May-2026")
    pdf.cell(95, 4, "Full Name: Ramesh Sharma", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())


def generate_sample_form16(output_path: Path | str | None = None) -> bytes:
    """Generate specimen Form 16 PDF and optionally write to output_path."""
    pdf_bytes = build_form16_pdf()
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf_bytes)
    return pdf_bytes


if __name__ == "__main__":
    dest = Path("demo_portals/samples/form16_demo_test_kumar.pdf")
    generate_sample_form16(dest)
    print(f"Specimen Form 16 successfully written to {dest} ({dest.stat().st_size} bytes)")
