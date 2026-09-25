"""Batch processing of estimate / bill folders, independent of any UI.

`on_progress(done, total)` is called after each PDF so a UI can show progress.

Each run also returns an `issues` list: problems a person should look at
(failed extractions, bills with no matching estimate, name mismatches).
Every issue is a dict with keys severity ("error" | "warning"), file, mrd,
patient, problem (short title) and detail (what to check)."""
import logging
from pathlib import Path

from .excel_store import normalize_name
from .pdf_estimate import extract_estimate_fields, EstimateExtractionError
from .pdf_bill import extract_bill_fields, BillExtractionError

logger = logging.getLogger(__name__)

ERROR = "error"
WARNING = "warning"


def pdf_files(folder):
    return sorted(Path(folder).glob("*.pdf"))


def _noop(_done, _total):
    pass


def _issue(severity, file, problem, detail, mrd=None, patient=None):
    return {"severity": severity, "file": file, "mrd": mrd, "patient": patient,
            "problem": problem, "detail": detail}


def process_estimate_folder(store, folder, on_progress=_noop) -> dict:
    files = pdf_files(folder)
    on_progress(0, len(files))
    added, skipped, failed = 0, 0, 0
    issues = []
    for i, f in enumerate(files, 1):
        try:
            fields = extract_estimate_fields(f)
            mrd, name = fields["mrd_number"], fields.get("patient_name")
            if store.has_mrd(mrd):
                skipped += 1
                logger.info("Estimate skipped (MRD already present): %s", f.name)
                existing = store.patient_name(mrd)
                if existing and name and normalize_name(existing) != normalize_name(name):
                    logger.warning("Estimate %s: MRD %s already belongs to '%s', not '%s'",
                                   f.name, mrd, existing, name)
                    issues.append(_issue(
                        ERROR, f.name, "MRD already used by another patient",
                        f"MRD {mrd} is on file for '{existing}', but this estimate is for "
                        f"'{name}'. The estimate was not added -- check the MRD Number.",
                        mrd, name))
            else:
                store.add_estimate(fields)
                added += 1
                logger.info("Estimate added: %s (MRD %s)", f.name, mrd)
        except EstimateExtractionError as e:
            failed += 1
            logger.error("Estimate extraction FAILED for %s: %s", f.name, e)
            issues.append(_issue(ERROR, f.name, "Could not read estimate",
                                 f"{e}. Enter this patient manually or check the PDF."))
        except Exception as e:
            failed += 1
            logger.exception("Unexpected error reading estimate %s", f.name)
            issues.append(_issue(ERROR, f.name, "Could not read estimate",
                                 f"Unexpected error: {e}. See reconciliation_log.txt."))
        on_progress(i, len(files))
    return {"files": len(files), "added": added, "skipped": skipped, "failed": failed,
            "issues": issues}


def process_billing_folder(store, folder, on_progress=_noop) -> dict:
    files = pdf_files(folder)
    on_progress(0, len(files))
    run_rows = []
    issues = []
    matched, unmatched, failed = 0, 0, 0
    for i, f in enumerate(files, 1):
        try:
            fields = extract_bill_fields(f)
            mrd, name = fields["mrd_number"], fields.get("patient_name")
            if not fields.get("annexure_sanity_ok", True):
                logger.warning(
                    "Annexure sum mismatch in %s (MRD %s): annexure sum=%.2f vs summary total=%.2f",
                    f.name, mrd, fields["annexure_charge_sum"], fields["billed_amount"],
                )
                issues.append(_issue(
                    WARNING, f.name, "Bill total doesn't add up",
                    f"Annexure charges sum to {fields['annexure_charge_sum']:.2f} but the "
                    f"Summary of Transactions total is {fields['billed_amount']:.2f}.",
                    mrd, name))
            result = store.apply_bill(fields)
            if result is None:
                unmatched += 1
                logger.warning(
                    "No matching MRD %s in Excel for bill %s -- flagged for manual review",
                    mrd, f.name,
                )
                same_name = store.find_mrds_by_name(name)
                if same_name:
                    detail = (f"No estimate has MRD {mrd}, but an estimate for '{name}' is on file "
                              f"under MRD {', '.join(same_name)}. Check the MRD on both documents.")
                else:
                    detail = (f"No estimate has MRD {mrd}. Add the patient's estimate and run "
                              f"again, or check the MRD Number on the bill.")
                issues.append(_issue(ERROR, f.name, "No matching estimate", detail, mrd, name))
            else:
                matched += 1
                logger.info(
                    "Bill matched: %s (MRD %s) -> status %s", f.name, mrd, result["status"]
                )
                run_rows.append(result)
                if result["name_mismatch"]:
                    issues.append(_issue(
                        WARNING, f.name, "Patient name differs",
                        f"Matched on MRD {mrd}, but the estimate says '{result['patient_name']}' "
                        f"and the bill says '{name}'. Confirm it is the same patient.",
                        mrd, name))
        except BillExtractionError as e:
            failed += 1
            logger.error("Bill extraction FAILED for %s: %s", f.name, e)
            issues.append(_issue(ERROR, f.name, "Could not read bill",
                                 f"{e}. Enter the billed amount manually or check the PDF."))
        except Exception as e:
            failed += 1
            logger.exception("Unexpected error reading bill %s", f.name)
            issues.append(_issue(ERROR, f.name, "Could not read bill",
                                 f"Unexpected error: {e}. See reconciliation_log.txt."))
        on_progress(i, len(files))
    return {"files": len(files), "matched": matched, "unmatched": unmatched,
            "failed": failed, "run_rows": run_rows, "issues": issues}
