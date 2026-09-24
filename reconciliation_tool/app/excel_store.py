"""Excel-backed patient store: one row per patient, keyed on MRD Number."""
import logging
from pathlib import Path

from openpyxl import Workbook, load_workbook

logger = logging.getLogger(__name__)

COLUMNS = [
    "MRD Number", "Patient Name", "Admitting Doctor", "Speciality", "Bed Type",
    "Duration of Stay", "Estimation", "Expected D.O.A",
    "Estimate Amount", "Billed Amount", "Difference", "Status",
]
COL_INDEX = {name: i + 1 for i, name in enumerate(COLUMNS)}

STATUS_CLOSED = "Closed"
STATUS_LESS = "Less Amount"
STATUS_EXCESS = "Excess Amount"
STATUS_PENDING = "Pending"

_ALL_STATUSES = (STATUS_CLOSED, STATUS_LESS, STATUS_EXCESS, STATUS_PENDING)


def compute_status(estimate_amount, billed_amount):
    """Return (status, difference). difference is Estimate - Billed."""
    if billed_amount is None:
        return STATUS_PENDING, None
    diff = round((estimate_amount or 0) - billed_amount, 2)
    if abs(diff) < 0.005:
        return STATUS_CLOSED, diff
    if diff > 0:
        return STATUS_LESS, diff
    return STATUS_EXCESS, diff


class ExcelStore:
    """Wraps a single-sheet workbook with one row per patient (MRD Number key)."""

    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            self.wb = load_workbook(self.path)
            self.ws = self.wb.active
        else:
            self.wb = Workbook()
            self.ws = self.wb.active
            self.ws.title = "Reconciliation"
            self.ws.append(COLUMNS)
        self._index_rows()

    def _index_rows(self):
        self.mrd_row = {}
        for r in range(2, self.ws.max_row + 1):
            mrd = self.ws.cell(row=r, column=COL_INDEX["MRD Number"]).value
            if mrd:
                self.mrd_row[str(mrd)] = r

    def has_mrd(self, mrd_number) -> bool:
        return str(mrd_number) in self.mrd_row

    def add_estimate(self, fields: dict) -> bool:
        """Add a new patient row from extracted estimate fields.
        Returns False (no-op) if the MRD Number is already present."""
        mrd = str(fields["mrd_number"])
        if mrd in self.mrd_row:
            return False
        row = [None] * len(COLUMNS)
        row[COL_INDEX["MRD Number"] - 1] = mrd
        row[COL_INDEX["Patient Name"] - 1] = fields.get("patient_name", "")
        row[COL_INDEX["Admitting Doctor"] - 1] = fields.get("admitting_doctor", "")
        row[COL_INDEX["Speciality"] - 1] = fields.get("speciality", "")
        row[COL_INDEX["Bed Type"] - 1] = fields.get("bed_type", "")
        row[COL_INDEX["Duration of Stay"] - 1] = fields.get("duration_of_stay", "")
        row[COL_INDEX["Estimation"] - 1] = fields.get("estimation_label", "")
        row[COL_INDEX["Expected D.O.A"] - 1] = fields.get("expected_doa", "")
        row[COL_INDEX["Estimate Amount"] - 1] = fields.get("estimate_amount")
        row[COL_INDEX["Status"] - 1] = STATUS_PENDING
        self.ws.append(row)
        self.mrd_row[mrd] = self.ws.max_row
        return True

    def apply_bill(self, fields: dict):
        """Match a bill's fields to an existing patient row by MRD Number and
        set Billed Amount / Difference / Status.

        Returns a result dict on success, or None if the MRD Number has no
        matching estimate row yet."""
        mrd = str(fields["mrd_number"])
        if mrd not in self.mrd_row:
            return None
        r = self.mrd_row[mrd]

        excel_name = self.ws.cell(row=r, column=COL_INDEX["Patient Name"]).value or ""
        bill_name = fields.get("patient_name", "")
        if excel_name and bill_name and excel_name.strip().lower() != bill_name.strip().lower():
            logger.warning(
                "Patient name mismatch for MRD %s: Excel has '%s', bill has '%s'",
                mrd, excel_name, bill_name,
            )

        estimate_amount = self.ws.cell(row=r, column=COL_INDEX["Estimate Amount"]).value
        billed_amount = fields["billed_amount"]
        status, diff = compute_status(estimate_amount, billed_amount)

        self.ws.cell(row=r, column=COL_INDEX["Billed Amount"], value=billed_amount)
        self.ws.cell(row=r, column=COL_INDEX["Difference"], value=diff)
        self.ws.cell(row=r, column=COL_INDEX["Status"], value=status)

        return {
            "mrd_number": mrd,
            "patient_name": excel_name or bill_name,
            "estimate_amount": estimate_amount,
            "billed_amount": billed_amount,
            "difference": diff,
            "status": status,
        }

    def save(self):
        self.wb.save(self.path)

    def overall_counts(self) -> dict:
        counts = {"Total Patients": 0, STATUS_CLOSED: 0, STATUS_LESS: 0, STATUS_EXCESS: 0, STATUS_PENDING: 0}
        for r in range(2, self.ws.max_row + 1):
            mrd = self.ws.cell(row=r, column=COL_INDEX["MRD Number"]).value
            if not mrd:
                continue
            counts["Total Patients"] += 1
            status = self.ws.cell(row=r, column=COL_INDEX["Status"]).value
            if status in _ALL_STATUSES:
                counts[status] += 1
        return counts
