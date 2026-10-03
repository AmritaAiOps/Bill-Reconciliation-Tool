"""Tests for the live bill parser (app.pdf_bill).

The summary-table tests feed pdfplumber-style word dicts, so layout quirks
seen on real bills (amounts offset from their label, blank Credits cell,
Indian digit grouping) can be reproduced without a PDF.

Run from reconciliation_tool/:  python -m pytest tests -q
"""
from pathlib import Path

import pytest

from app.pdf_bill import extract_bill_fields, summary_amounts_from_words


def w(text, x0, top, height=11.0):
    return {"text": text, "x0": x0, "x1": x0 + 5.5 * len(text),
            "top": top, "bottom": top + height}


def summary_words(total_dy=1.75, charges="106934.51", credits="106934.51"):
    """Summary of Transactions laid out like the sample bill. ``total_dy`` is
    how far below the 'Total' label the amounts sit."""
    words = [
        w("Summary", 34, 330), w("of", 80, 330), w("Transactions", 95, 330),
        w("Charges(Rs)", 340, 340), w("Credits(Rs)", 460, 340),
        w("[Annexure-1]", 200, 352), w(":", 334, 352),
        w("95916.41", 355, 353), w("0.00", 497, 353),
        w("[Annexure-2]", 200, 370), w(":", 334, 370),
        w("11018.10", 355, 371), w("0.00", 497, 371),
        w("Advance", 34, 388), w("[Annexure-3]", 200, 388), w(":", 334, 388),
        w("0.00", 377, 389), w("106934.51", 469, 389),
        w("Total", 289, 468.6), w(":", 316.8, 468.6),
        w("Total", 34, 508), w("Amount", 70, 508), w("Due", 110, 508),
        w("In", 135, 508), w("Words", 150, 508),
    ]
    if charges:
        words.append(w(charges, 412 - 5.5 * len(charges), 468.6 + total_dy))
    if credits:
        words.append(w(credits, 520.5 - 5.5 * len(credits), 468.6 + total_dy))
    return words


def test_sample_layout():
    r = summary_amounts_from_words(summary_words())
    assert r["charges_total"] == 106934.51
    assert r["credits_total"] == 106934.51
    assert round(sum(r["annexure_charges"]), 2) == 106934.51


def test_amounts_offset_beyond_layout_line_tolerance():
    # 4pt lower than the label: extract_text(layout=True) puts them on their
    # own line, which made the old single-line regex fail.
    r = summary_amounts_from_words(summary_words(total_dy=4.0))
    assert r["charges_total"] == 106934.51


def test_blank_credits_cell():
    r = summary_amounts_from_words(summary_words(credits=None))
    assert r["charges_total"] == 106934.51
    assert r["credits_total"] is None


def test_indian_grouping_and_no_decimals():
    r = summary_amounts_from_words(summary_words(charges="1,06,934", credits="1,06,934"))
    assert r["charges_total"] == 106934.0


def test_amount_only_in_credits_column_is_not_taken_as_charges():
    r = summary_amounts_from_words(summary_words(charges=None))
    assert r["charges_total"] is None
    assert r["credits_total"] == 106934.51


def test_no_total_row():
    words = [x for x in summary_words() if not (x["text"] == "Total" and x["top"] < 500)]
    words = [x for x in words if not (468 < x["top"] < 475)]
    assert summary_amounts_from_words(words)["charges_total"] is None


_ROOT = Path(__file__).resolve().parents[3]
BILL_PDF = _ROOT / "Billing Folder" / "IP bill Sample.pdf"


@pytest.mark.skipif(not BILL_PDF.exists(), reason="sample PDF not found")
def test_sample_bill_pdf():
    r = extract_bill_fields(BILL_PDF)
    assert r["mrd_number"] == "316148"
    assert r["patient_name"] == "FADIL ASSINE GABRIEL"
    assert r["billed_amount"] == 106934.51
    assert r["annexure_sanity_ok"] is True
