"""Extraction of patient/billed-amount fields from IP Bill PDFs.

The summary page ("In Patient Collection & Appropriation" / "Summary of
Transactions") is read for the main match -- normally page 1, but every page
is checked so a cover page or a re-ordered print still works. The annexure
pages (itemised hospital / pharmacy charges, advances) are not parsed
individually -- the per-source subtotals that the summary page already lists
(e.g. "Amrita Institute of Medical Science [Annexure-1] : 95916.41") are used
only as a sanity check against the Summary of Transactions Total.

Amounts are located by word position, not by a single text line: on these
bills the figures sit a point or two lower than their row label, and with a
slightly different font they would fall onto a separate line of
``extract_text(layout=True)`` output. A word counts as being on a row when
its vertical centre is within the row label's height, and an amount belongs
to the Charges(Rs) / Credits(Rs) column whose header it sits nearest to.
"""
import logging
import re
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)

_FIELD_LABELS = [
    "Visit Number", "Category", "Admitting Doctor", "Speciality", "Payer",
    "Patient Age", "Cell Phone", "Address", "Date", "Bed", "Sponsor",
    "D.O.A", "D.O.D", "Summary",
]

# Upper-cased, whitespace-collapsed page text that marks the summary page.
_SUMMARY_MARKERS = (
    "SUMMARY OF TRANSACTIONS", "COLLECTION & APPROPRIATION", "SUMMARY BILL",
)

_MRD_RE = r"M\s*R\s*D\s*(?:Number|No\.?)\s*:\s*(\S+)"
# One amount token: 1,06,934.51 / 106934 / -500.00 / (500.00) / Rs.500.00
_AMOUNT_TOKEN = re.compile(r"^(?:Rs\.?|₹)?\(?-?\d[\d,]*(?:\.\d+)?\)?$")
_AMOUNT_TEXT = r"-?\d[\d,]*(?:\.\d+)?"


class BillExtractionError(Exception):
    """Raised when a required field could not be found in an IP Bill PDF."""


def _is_new_field_line(line: str) -> bool:
    return any(label in line for label in _FIELD_LABELS)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _to_amount(token: str):
    """'1,06,934.51' -> 106934.51, '(500.00)' -> -500.0; None if not numeric."""
    negative = token.startswith("(") and token.endswith(")") or "-" in token
    try:
        value = float(re.sub(r"Rs\.?|₹|[,()\-\s]", "", token))
    except ValueError:
        return None
    return -value if negative else value


def _mid(word) -> float:
    return (word["top"] + word["bottom"]) / 2


def _row_amounts(words, anchor) -> list:
    """Amount words to the right of ``anchor`` on the same visual row,
    left to right."""
    tol = (anchor["bottom"] - anchor["top"]) * 0.75
    row = [w for w in words
           if w["x0"] > anchor["x1"] and abs(_mid(w) - _mid(anchor)) <= tol]
    return sorted((w for w in row if _AMOUNT_TOKEN.match(w["text"])),
                  key=lambda w: w["x0"])


def _row_has_only_amounts(words, anchor) -> bool:
    """True when everything right of ``anchor`` on its row is ':' or an
    amount (so 'Total Amount Due In Words' style rows are not mistaken for
    the Total row)."""
    tol = (anchor["bottom"] - anchor["top"]) * 0.75
    for w in words:
        if w["x0"] > anchor["x1"] and abs(_mid(w) - _mid(anchor)) <= tol:
            if w["text"] != ":" and not _AMOUNT_TOKEN.match(w["text"]):
                return False
    return True


def _split_columns(amounts, charges_hdr, credits_hdr):
    """Return (charges_word, credits_word) for one row's amount words.

    Amounts are right-aligned under their header, so each goes to the header
    whose right edge is nearest. Without headers, the first amount is
    Charges and the second Credits."""
    if charges_hdr is None or credits_hdr is None:
        return (amounts[0] if amounts else None,
                amounts[1] if len(amounts) > 1 else None)
    charges = credits = None
    for w in amounts:
        if abs(w["x1"] - charges_hdr["x1"]) <= abs(w["x1"] - credits_hdr["x1"]):
            charges = charges or w
        else:
            credits = credits or w
    return charges, credits


def summary_amounts_from_words(words) -> dict:
    """Find the Summary of Transactions figures from pdfplumber word dicts
    (keys text, x0, x1, top, bottom). Pure function, testable without a PDF.

    Returns {"charges_total", "credits_total", "annexure_charges"}; the
    totals are None when not found.
    """
    charges_hdr = next((w for w in words if re.match(r"Charges", w["text"], re.I)), None)
    credits_hdr = next((w for w in words if re.match(r"Credits", w["text"], re.I)), None)
    below = charges_hdr["bottom"] if charges_hdr else float("-inf")

    total_rows = [w for w in words
                  if re.fullmatch(r"Total\s*:?", w["text"], re.I)
                  and w["top"] >= below and _row_amounts(words, w)]
    # Prefer a clean "Total : <amounts>" row over e.g. "Total Amount 500.00".
    total_rows.sort(key=lambda w: (not _row_has_only_amounts(words, w), w["top"]))

    charges_total = credits_total = None
    if total_rows:
        ch, cr = _split_columns(_row_amounts(words, total_rows[0]), charges_hdr, credits_hdr)
        charges_total = _to_amount(ch["text"]) if ch else None
        credits_total = _to_amount(cr["text"]) if cr else None

    annexure_charges = []
    for w in words:
        if re.search(r"Annexure-?\s*\d+\]?", w["text"], re.I):
            ch, _ = _split_columns(_row_amounts(words, w), charges_hdr, credits_hdr)
            if ch:
                annexure_charges.append(_to_amount(ch["text"]))

    return {"charges_total": charges_total, "credits_total": credits_total,
            "annexure_charges": annexure_charges}


def _total_from_text(text: str):
    """Fallback: (charges, credits) from a 'Total : <amt> [<amt>]' text line."""
    m = re.search(rf"^\s*(?:Grand\s+)?Total\s*:?\s*({_AMOUNT_TEXT})(?:\s+({_AMOUNT_TEXT}))?\s*$",
                  text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return None, None
    return _to_amount(m.group(1)), (_to_amount(m.group(2)) if m.group(2) else None)


def _read_pages(pdf_path):
    """Return [(layout_text, words)] per page, with overprinted ("fake
    bold") duplicate characters removed."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            raise BillExtractionError("PDF has no pages")
        for page in pdf.pages:
            page = page.dedupe_chars()
            pages.append((page.extract_text(layout=True) or "", page.extract_words()))
    return pages


def _summary_page_index(pages) -> int:
    for i, (text, _) in enumerate(pages):
        if any(mk in _clean(text).upper() for mk in _SUMMARY_MARKERS):
            return i
    return 0


def extract_bill_fields(pdf_path) -> dict:
    """Extract MRD Number, Patient Name and Billed Amount from the summary
    page (normally page 1) of an IP Bill PDF.

    Billed Amount is the "Total" (Charges column) from the Summary of
    Transactions table -- the figure that also appears (and should match)
    under the Credits column. If the Charges cell is empty, the Credits
    total is used instead.

    Returns a dict with keys: mrd_number, patient_name, visit_number, doa,
    dod, billed_amount, credits_total (None if blank), annexure_charge_sum,
    annexure_sanity_ok, source_file.

    Raises BillExtractionError if a required field is missing.
    """
    pdf_path = Path(pdf_path)
    pages = _read_pages(pdf_path)
    if not any(text.strip() for text, _ in pages):
        raise BillExtractionError(
            "PDF has no readable text (it looks like a scanned image); "
            "use the system-generated PDF instead of a scan")

    idx = _summary_page_index(pages)
    text, words = pages[idx]
    all_text = "\n".join(t for t, _ in pages)
    lines = text.split("\n")
    fields: dict = {"source_file": pdf_path.name}

    m = re.search(_MRD_RE, text) or re.search(_MRD_RE, all_text)
    if not m:
        raise BillExtractionError("MRD Number not found")
    fields["mrd_number"] = m.group(1).strip()

    # The name is on the MRD line; a second "Patient :" further down is the address.
    m = re.search(_MRD_RE + r".*?Patient(?:\s+Name)?\s*:\s*(.+)", text) \
        or re.search(_MRD_RE + r".*?Patient(?:\s+Name)?\s*:\s*(.+)", all_text)
    if not m:
        raise BillExtractionError("Patient name not found")
    name = m.group(2).strip()
    label_idx = next((i for i, l in enumerate(lines) if re.search(_MRD_RE, l)), None)
    if label_idx is not None and label_idx + 1 < len(lines):
        nxt = lines[label_idx + 1].strip()
        if nxt and ":" not in nxt and not _is_new_field_line(nxt):
            name = f"{name} {nxt}".strip()
    fields["patient_name"] = _clean(name)

    m = re.search(r"Visit Number\s*:\s*(\S+)", text)
    fields["visit_number"] = m.group(1).strip() if m else ""

    m = re.search(r"D\.O\.A\s*:\s*([\d/]+\s+[\d:]+)", text)
    fields["doa"] = m.group(1).strip() if m else ""

    m = re.search(r"D\.O\.D\s*:\s*([\d/]+\s+[\d:]+)", text)
    fields["dod"] = m.group(1).strip() if m else ""

    summary = summary_amounts_from_words(words)
    charges_total, credits_total = summary["charges_total"], summary["credits_total"]
    if charges_total is None and credits_total is None:
        charges_total, credits_total = _total_from_text(text)
    if charges_total is None and credits_total is None:
        logger.warning("Summary of Transactions Total not found in %s (page %d). "
                       "Page text was:\n%s", pdf_path.name, idx + 1, text)
        raise BillExtractionError(f"Summary of Transactions Total not found (page {idx + 1})")
    if charges_total is None:
        logger.warning("%s: Charges total is blank; using Credits total %.2f",
                       pdf_path.name, credits_total)
        charges_total = credits_total
    fields["billed_amount"] = charges_total
    fields["credits_total"] = credits_total

    annexure_sum = round(sum(summary["annexure_charges"]), 2)
    fields["annexure_charge_sum"] = annexure_sum
    # No annexure rows found means nothing to check against, not a mismatch.
    fields["annexure_sanity_ok"] = (not summary["annexure_charges"]
                                    or abs(annexure_sum - charges_total) < 0.01)

    return fields
