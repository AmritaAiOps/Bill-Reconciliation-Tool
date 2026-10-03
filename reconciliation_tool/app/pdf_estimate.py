"""Extraction of patient/estimate fields from Estimate Performa PDFs."""
import logging
import re
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)

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


_TYPED_ROW = re.compile(r"^\s*(.+?)\s+(\S+)\s+(\d+(?:\.\d+)?)\s+([\d,]+\.\d{2})\s*$")
_AMOUNT_AT_END = re.compile(r"[\d,]+\.\d{2}\s*$")


def _particulars_rows(lines: list[str]) -> list[dict]:
    """Rows of the Estimated Expenditure table that have a Type column
    (e.g. Service, Bed), as dicts with particulars/type/quantity/amount.

    A long Particulars name wraps onto the next line; such a line has text
    only to the left of the Type column and is appended to the row above."""
    header_idx = next((i for i, l in enumerate(lines)
                       if re.search(r"Particulars\s+Type\s+Quantity", l)), None)
    if header_idx is None:
        return []
    type_col = lines[header_idx].index("Type")

    rows: list[dict] = []
    last_typed = False  # was the previous table line a typed row?
    for line in lines[header_idx + 1:]:
        if "Total Estimate" in line:
            break
        if not line.strip():
            continue
        m = _TYPED_ROW.match(line)
        if m:
            rows.append({"particulars": _clean(m.group(1)), "type": m.group(2),
                         "quantity": float(m.group(3)),
                         "amount": float(m.group(4).replace(",", ""))})
            last_typed = True
        elif (last_typed and not _AMOUNT_AT_END.search(line)
              and not line[type_col:].strip()):
            rows[-1]["particulars"] = _clean(f"{rows[-1]['particulars']} {line}")
        else:
            last_typed = False
    return rows


def _as_number(value):
    """2.0 -> 2, 2.5 -> 2.5, '2' -> 2; None if not numeric."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def extract_estimate_fields(pdf_path) -> dict:
    """Extract the required fields from page 1 of an Estimate Performa PDF.

    Returns a dict with keys: mrd_number, patient_name, admitting_doctor,
    duration_of_stay, speciality, expected_doa, bed_type, estimation_label,
    estimate_amount, surgery, duration_days, source_file.

    Raises EstimateExtractionError if a required field is missing.
    """
    pdf_path = Path(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            raise EstimateExtractionError("PDF has no pages")
        text = pdf.pages[0].extract_text(layout=True) or ""
    fields = parse_estimate_text(text)
    fields["source_file"] = pdf_path.name
    return fields


def parse_estimate_text(text: str) -> dict:
    """Parse the page-1 layout text of an Estimate Performa (see
    extract_estimate_fields). Pure function, testable without a PDF."""
    lines = text.split("\n")
    joined = "\n".join(lines)
    fields: dict = {}

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

    rows = _particulars_rows(lines)
    fields["surgery"] = "; ".join(r["particulars"] for r in rows
                                  if r["type"].lower() == "service")

    header_days = _as_number(fields["duration_of_stay"])
    bed = next((r for r in rows if r["type"].lower() == "bed"), None)
    if bed is None:
        fields["duration_days"] = header_days if header_days is not None \
            else fields["duration_of_stay"]
    else:
        fields["duration_days"] = _as_number(bed["quantity"])
        if header_days is not None and header_days != fields["duration_days"]:
            logger.warning("MRD %s: Bed quantity %s differs from Duration of Stay %s; "
                           "using Bed quantity", fields["mrd_number"],
                           fields["duration_days"], header_days)

    return fields
