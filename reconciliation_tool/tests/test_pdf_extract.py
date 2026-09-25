"""Tests for app.pdf_extract.

Text-level tests use layout text copied from the real samples, so they run
without the PDFs. The PDF tests look for the samples in the sibling
"Estimate Folder" / "Billing Folder" directories and skip if absent.

Run from reconciliation_tool/:  python -m pytest tests -q
"""
from pathlib import Path

import pytest

from app.pdf_extract import (
    extract_any,
    extract_estimate_performa,
    extract_summary_bill,
    parse_estimate_text,
    parse_summary_bill_text,
)

ESTIMATE_TEXT = """\
                            ESTIMATE     PERFORMA
     M R D Number : 317251                   Patient Name : ALAALDEN KHALIFA
                                                          OSMAN ALTAHER
     Admitting Doctor : Anil Kumar Murarka   Duration of Stay : 2
     Speciality   : Plastic-Reconstructive Surgery Expected D.O.A : 15/09/2026
     Bed Type     : IPS CatB_ V1_Semi Private Estimation : Estimation 2

    Estimated Expenditure:
     Particulars            Type               Quantity        Amount (Rs)
     Burn contracture release- Single Service  1.0             74360.00
     region
    Total Estimate                                             254310.00
"""

BILL_TEXT = """\
                       In Patient Collection & Appropriation
   M R D Number   : 316148                   Patient    :FADIL ASSINE GABRIEL

   Visit Number   : IP0002                   Patient Age :7 Years
   Category       : IPS CAT_B                Cell Phone :91-9319081817

   Admitting Doctor : Dr. Pritish Singh      Patient    :Nampula, Nampula, Mozambique
                                             Address
   Speciality     : Paediatric Orthopaedics and Deformity
                   Correction
   D.O.A          : 31/08/2026 15:24
                                             Bed        :Semi Private /3125
   D.O.D          : 03/09/2026 18:09
     Summary  of Transactions
                                                     Charges(Rs)      Credits(Rs)
     Advance Amount [Annexure-3]              :            0.00        106934.51
                                        Total :        106934.51        106934.51
                                        Net Amount Due (Rs)                0.00
"""


def test_estimate_text_all_fields():
    r = parse_estimate_text(ESTIMATE_TEXT)
    assert r["missing_fields"] == []
    assert r["mrd_number"] == "317251"
    assert r["patient_name"] == "ALAALDEN KHALIFA OSMAN ALTAHER"
    assert r["admitting_doctor"] == "Anil Kumar Murarka"
    assert r["speciality"] == "Plastic-Reconstructive Surgery"
    assert r["duration_of_stay"] == "2"
    assert r["expected_doa"] == "15/09/2026"
    assert r["bed_type"] == "IPS CatB_ V1_Semi Private"
    assert r["estimation_label"] == "Estimation 2"
    assert r["total_estimate"] == 254310.0


def test_bill_text_all_fields():
    r = parse_summary_bill_text(BILL_TEXT)
    assert r["missing_fields"] == []
    assert r["mrd_number"] == "316148"
    assert r["patient_name"] == "FADIL ASSINE GABRIEL"  # not the address
    assert r["visit_number"] == "IP0002"
    assert r["category"] == "IPS CAT_B"
    assert r["admitting_doctor"] == "Dr. Pritish Singh"
    assert r["doa"] == "31/08/2026 15:24"
    assert r["dod"] == "03/09/2026 18:09"
    assert r["total_billed"] == 106934.51
    assert r["net_amount_due"] == 0.0


def test_currency_formatting_stripped():
    est = ESTIMATE_TEXT.replace("254310.00", "Rs. 2,54,310.00")
    assert parse_estimate_text(est)["total_estimate"] == 254310.0
    bill = BILL_TEXT.replace(
        "Total :        106934.51        106934.51",
        "Total :       1,06,934.51      1,06,934.51",
    ).replace("Net Amount Due (Rs)                0.00", "Net Amount Due (Rs)  Rs. 1,250.50")
    r = parse_summary_bill_text(bill)
    assert r["total_billed"] == 106934.51
    assert r["net_amount_due"] == 1250.5


def test_total_billed_uses_charges_column_not_credits():
    bill = BILL_TEXT.replace(
        "Total :        106934.51        106934.51",
        "Total :        100000.00        106934.51",
    )
    assert parse_summary_bill_text(bill)["total_billed"] == 100000.0


def test_missing_labels_become_none_and_are_listed():
    est = ESTIMATE_TEXT.replace("Bed Type     :", "            ").replace(
        "Total Estimate", "Grand Sum"
    )
    r = parse_estimate_text(est)
    assert r["bed_type"] is None
    assert r["total_estimate"] is None
    assert set(r["missing_fields"]) == {"bed_type", "total_estimate"}
    assert r["estimation_label"] == "Estimation 2"  # neighbours unaffected

    r = parse_summary_bill_text("")
    assert r["missing_fields"] == [
        "mrd_number", "patient_name", "visit_number", "category",
        "admitting_doctor", "doa", "dod", "total_billed", "net_amount_due",
    ]


def test_single_line_name_does_not_swallow_next_row():
    est = ESTIMATE_TEXT.replace(
        "                                                          OSMAN ALTAHER\n", ""
    )
    assert parse_estimate_text(est)["patient_name"] == "ALAALDEN KHALIFA"


def test_linked_pair_mrd_matches():
    bill = BILL_TEXT.replace(": 316148", ": 317251")
    assert parse_estimate_text(ESTIMATE_TEXT)["mrd_number"] == \
        parse_summary_bill_text(bill)["mrd_number"]


# ---- Real sample PDFs ------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[3]
ESTIMATE_PDF = _ROOT / "Estimate Folder" / "SAMPLE estimate.pdf"
BILL_PDF = _ROOT / "Billing Folder" / "IP bill Sample.pdf"
needs_samples = pytest.mark.skipif(
    not (ESTIMATE_PDF.exists() and BILL_PDF.exists()), reason="sample PDFs not found"
)


@needs_samples
def test_sample_pdfs():
    e = extract_estimate_performa(ESTIMATE_PDF)
    b = extract_summary_bill(BILL_PDF)
    assert e["missing_fields"] == [] and b["missing_fields"] == []
    assert e["total_estimate"] == 254310.0
    assert b["total_billed"] == 106934.51
    # The two samples are different patients, so they must NOT reconcile.
    assert (e["mrd_number"], b["mrd_number"]) == ("317251", "316148")


@needs_samples
def test_auto_detection():
    assert extract_any(ESTIMATE_PDF)["document_type"] == "estimate_performa"
    assert extract_any(BILL_PDF)["document_type"] == "summary_bill"
