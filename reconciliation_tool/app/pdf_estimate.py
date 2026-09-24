"""Extraction of patient/estimate fields from Estimate Performa PDFs."""
import re
from pathlib import Path

import pdfplumber

# Labels that mark the start of a new field. Used to detect where a wrapped
# value (e.g. a patient name split across two lines) ends.
_FIELD_LABELS = [
    "Admitting Doctor", "Duration of Stay", "Speciality", "Expected D.O.A",
    "Bed Type", "Estimation", "Particulars", "Estimated Expenditure",
    "Total Estimate", "Patient Name", "M R D Number",
]


class EstimateExtractionError(Exception):
    """Raised when a required field could not be found in an Estimate PDF."""


def _is_new_field_line(line: str) -> bool:
    return any(label in line for label in _FIELD_LABELS)


def _append_wrapped_line(lines: list[str], label_line_idx: int, value: str) -> str:
    """If the physical line right after the label's line is a continuation
    of the value (no colon, no known label), append it."""
    if label_line_idx + 1 < len(lines):
        nxt = lines[label_line_idx + 1].strip()
        if nxt and ":" not in nxt and not _is_new_field_line(nxt):
            value = f"{value} {nxt}".strip()
    return value


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_estimate_fields(pdf_path) -> dict:
    """Extract the required fields from page 1 of an Estimate Performa PDF.

    Returns a dict with keys: mrd_number, patient_name, admitting_doctor,
    duration_of_stay, speciality, expected_doa, bed_type, estimation_label,
    estimate_amount, source_file.

    Raises EstimateExtractionError if a required field is missing.
    """
    pdf_path = Path(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            raise EstimateExtractionError("PDF has no pages")
        text = pdf.pages[0].extract_text(layout=True) or ""

    lines = text.split("\n")
    joined = "\n".join(lines)
    fields: dict = {"source_file": pdf_path.name}

    m = re.search(r"M\s*R\s*D\s*Number\s*:\s*(\S+)", joined)
    if not m:
        raise EstimateExtractionError("MRD Number not found")
    fields["mrd_number"] = m.group(1).strip()

    m = re.search(r"Patient Name\s*:\s*(.+)", joined)
    if not m:
        raise EstimateExtractionError("Patient Name not found")
    label_idx = next(i for i, l in enumerate(lines) if "Patient Name" in l)
    name = _append_wrapped_line(lines, label_idx, m.group(1).strip())
    fields["patient_name"] = _clean(name)

    m = re.search(r"Admitting Doctor\s*:\s*(.+?)\s+Duration of Stay\s*:\s*(\S+)", joined)
    if not m:
        raise EstimateExtractionError("Admitting Doctor / Duration of Stay not found")
    fields["admitting_doctor"] = _clean(m.group(1))
    fields["duration_of_stay"] = m.group(2).strip()

    m = re.search(r"Speciality\s*:\s*(.+?)\s+Expected D\.O\.A\s*:\s*([\d/]+)", joined)
    if not m:
        raise EstimateExtractionError("Speciality / Expected D.O.A not found")
    fields["speciality"] = _clean(m.group(1))
    fields["expected_doa"] = m.group(2).strip()

    m = re.search(r"Bed Type\s*:\s*(.+?)\s+Estimation\s*:\s*(.+)", joined)
    if not m:
        raise EstimateExtractionError("Bed Type / Estimation not found")
    fields["bed_type"] = _clean(m.group(1))
    fields["estimation_label"] = _clean(m.group(2))

    m = re.search(r"Total Estimate\s+([\d,]+\.\d{2})", joined)
    if not m:
        raise EstimateExtractionError("Total Estimate not found")
    fields["estimate_amount"] = float(m.group(1).replace(",", ""))

    return fields
