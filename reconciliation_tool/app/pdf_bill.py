"""Extraction of patient/billed-amount fields from IP Bill PDFs.

Only page 1 ("In Patient Collection & Appropriation") is read for the main
match. The annexure pages (itemised hospital / pharmacy charges, advances)
are not parsed individually -- the per-source subtotals that page 1 already
lists (e.g. "Amrita Institute of Medical Science [Annexure-1] : 95916.41")
are used only as a sanity check against the Summary of Transactions Total.
"""
import re
from pathlib import Path

import pdfplumber

_FIELD_LABELS = [
    "Visit Number", "Category", "Admitting Doctor", "Speciality", "Payer",
    "Patient Age", "Cell Phone", "Address", "Date", "Bed", "Sponsor",
    "D.O.A", "D.O.D", "Summary",
]


class BillExtractionError(Exception):
    """Raised when a required field could not be found in an IP Bill PDF."""


def _is_new_field_line(line: str) -> bool:
    return any(label in line for label in _FIELD_LABELS)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_bill_fields(pdf_path) -> dict:
    """Extract MRD Number, Patient Name and Billed Amount from page 1 of an
    IP Bill PDF.

    Billed Amount is the "Total" (Charges column) from the Summary of
    Transactions table on page 1 -- the figure that also appears (and should
    match) under the Credits column.

    Returns a dict with keys: mrd_number, patient_name, visit_number, doa,
    dod, billed_amount, credits_total, annexure_charge_sum,
    annexure_sanity_ok, source_file.

    Raises BillExtractionError if a required field is missing.
    """
    pdf_path = Path(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            raise BillExtractionError("PDF has no pages")
        text = pdf.pages[0].extract_text(layout=True) or ""

    lines = text.split("\n")
    joined = "\n".join(lines)
    fields: dict = {"source_file": pdf_path.name}

    m = re.search(r"M\s*R\s*D\s*Number\s*:\s*(\S+)", joined)
    if not m:
        raise BillExtractionError("MRD Number not found")
    fields["mrd_number"] = m.group(1).strip()

    m = re.search(r"M\s*R\s*D\s*Number\s*:\s*\S+.*?Patient\s*:\s*(.+)", joined)
    if not m:
        raise BillExtractionError("Patient name not found")
    label_idx = next(i for i, l in enumerate(lines) if re.search(r"M\s*R\s*D\s*Number", l))
    name = m.group(1).strip()
    if label_idx + 1 < len(lines):
        nxt = lines[label_idx + 1].strip()
        if nxt and ":" not in nxt and not _is_new_field_line(nxt):
            name = f"{name} {nxt}".strip()
    fields["patient_name"] = _clean(name)

    m = re.search(r"Visit Number\s*:\s*(\S+)", joined)
    fields["visit_number"] = m.group(1).strip() if m else ""

    m = re.search(r"D\.O\.A\s*:\s*([\d/]+\s+[\d:]+)", joined)
    fields["doa"] = m.group(1).strip() if m else ""

    m = re.search(r"D\.O\.D\s*:\s*([\d/]+\s+[\d:]+)", joined)
    fields["dod"] = m.group(1).strip() if m else ""

    m = re.search(r"^\s*Total\s*:\s*([\d,]+\.\d{2})\s+([\d,]+\.\d{2})", joined, re.MULTILINE)
    if not m:
        raise BillExtractionError("Summary of Transactions Total not found")
    charges_total = float(m.group(1).replace(",", ""))
    credits_total = float(m.group(2).replace(",", ""))
    fields["billed_amount"] = charges_total
    fields["credits_total"] = credits_total

    annexure_charges = [
        float(v.replace(",", ""))
        for v in re.findall(r"\[Annexure-\d+\]\s*:\s*([\d,]+\.\d{2})\s+[\d,]+\.\d{2}", joined)
    ]
    annexure_sum = round(sum(annexure_charges), 2)
    fields["annexure_charge_sum"] = annexure_sum
    fields["annexure_sanity_ok"] = abs(annexure_sum - charges_total) < 0.01

    return fields
