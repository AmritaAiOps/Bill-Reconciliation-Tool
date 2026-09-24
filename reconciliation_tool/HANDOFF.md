# Estimate vs Bill Reconciliation Tool — Session Handoff

Context dump for continuing this work in a new session. Everything needed to
pick up without re-reading the source PDFs is below.

---

## 1. What this tool is

A Windows desktop tool that checks whether each patient's final hospital bill
matches the amount quoted in their estimate.

- Reads **Estimate Performa PDFs** from a folder → creates one row per patient in Excel.
- Reads **IP Bill PDFs** from a second folder → adds the billed amount to the
  matching patient row and sets a status.
- Shows a dashboard of results after each billing run.

**MRD Number is the primary match key** across both document types (patient
names wrap across lines and differ in formatting, so names are only a secondary check).

Working directory: `C:\Users\hp\Downloads\INTERNSHIP\Bill samples\`
Source spec: `Estimate vs Bill Reconciliation Tool – Project Document.pdf`
Sample inputs: `SAMPLE estimate.pdf`, `IP bill Sample.pdf`
Code lives in: `reconciliation_tool\`

---

## 2. Spec as agreed (supersedes the PDF doc where they differ)

### Fields to extract from Estimate Performa PDFs
MRD Number, Patient Name, Admitting Doctor, Speciality, Bed Type,
Duration of Stay, Expected D.O.A, Estimation (label, e.g. "Estimation 2"),
Total Estimate (final amount at bottom of the Particulars table).

New patients are added as new rows keyed on MRD Number; existing MRDs are skipped.

### Fields to extract from IP Bill PDFs
MRD Number, Patient Name, Billed Amount.

Billed Amount = the **Total** from the "Summary of Transactions" section on
**page 1 only**. It appears under both `Charges(Rs)` and `Credits(Rs)` and the
two should match — **use the Charges total**.

Annexure pages (Annexure-1/2/3) are itemised breakdowns and are **not**
extracted individually — only used to sanity-check that the parts sum to the
summary Total.

### Status logic
| Condition | Status |
|---|---|
| Billed = Estimate | Closed |
| Billed < Estimate | Less Amount |
| Billed > Estimate | **Excess Amount** (resolves the open question in the reference doc) |
| No bill found yet | Pending |

`Difference = Estimate Amount − Billed Amount` (negative when billed exceeds estimate).

### Excel output columns
`MRD Number | Patient Name | Admitting Doctor | Speciality | Bed Type | Expected D.O.A | Estimate Amount | Billed Amount | Difference | Status`

**Deviation actually implemented (not yet signed off):** two extra trailing
columns, **Duration of Stay** and **Estimation**, were kept because the spec
asks to extract them in step 2 but omits them from the column list in step 5.
Current implemented order is:

```
MRD Number | Patient Name | Admitting Doctor | Speciality | Bed Type |
Duration of Stay | Estimation | Expected D.O.A | Estimate Amount |
Billed Amount | Difference | Status
```

### Dashboard
- After each billing run: "Closed in this run" and "Less Amount in this run"
  lists (Name, MRD, Estimate, Billed, Difference).
- Overall counts across the whole Excel file: Total Patients, Closed,
  Less Amount, Excess Amount, Pending.

### Technical notes from spec
Use pdfplumber for extraction, openpyxl/pandas for Excel I/O, a progress
indicator for batch processing, and log any PDF that fails extraction or has
no MRD match for manual review.

---

## 3. Key PDF-layout findings (this took the most effort — don't re-derive)

### pdfplumber `extract_tables()` returns **0 tables** on these PDFs
The visible boxes/borders are not real ruling lines. Table detection is useless here.

**What works:** `page.extract_text(layout=True)` — it preserves column
alignment with spaces and produces clean, regex-friendly text. This is the
basis of both parsers.

### Estimate PDF — page 1 layout text (real sample)
```
     M R D Number : 317251                   Patient Name : ALAALDEN KHALIFA
                                                          OSMAN ALTAHER
     Admitting Doctor : Anil Kumar Murarka   Duration of Stay : 2
     Speciality   : Plastic-Reconstructive Surgery Expected D.O.A : 15/09/2026
     Bed Type     : IPS CatB_ V1_Semi Private Estimation : Estimation 2

    Estimated Expenditure:

     Particulars            Type               Quantity        Amount (Rs)
     Burn contracture release- Single Service  1.0             74360.00
     region
     ...
    Total Estimate                                             254310.00
```
Note the **patient name wraps onto the next line** with no label — handled by a
continuation heuristic (append next line if it's non-empty, has no `:`, and
contains no known field label).

### IP Bill PDF — page 1 layout text (real sample)
```
   M R D Number   : 316148                   Patient    :FADIL ASSINE GABRIEL

   Visit Number   : IP0002                   Patient Age :7 Years
   Category       : IPS CAT_B                Cell Phone :91-9319081817

   Admitting Doctor : Dr. Pritish Singh      Patient    :Nampula, Nampula, Mozambique
                                             Address
   Speciality     : Paediatric Orthopaedics and Deformity
                   Correction
   Payer          :
                                             Date       :03/09/2026
   D.O.A          : 31/08/2026 15:24
                                             Bed        :Semi Private /3125
   D.O.D          : 03/09/2026 18:09
                                             Sponsor    :
     Summary  of Transactions
                                                     Charges(Rs)      Credits(Rs)
     Amrita Institute of Medical Science [Annexure-1] : 95916.41           0.00
     Sudhamayi Enterprises Pvt Ltd [Annexure-2] :       11018.10           0.00
     Advance Amount [Annexure-3]              :            0.00        106934.51
     Special Discount                         :            0.00
     Paid Amount                              :            0.00            0.00
     Refunded Amount                          :            0.00            0.00
                                        Total :        106934.51        106934.51
                                        Net Amount Due (Rs)                0.00
```

**Gotcha:** the label `Patient` is used **twice** — once for the patient *name*
(on the MRD row) and once for the patient *address* (on the Admitting Doctor
row, with `Address` continuing the label on the line below). Disambiguated by
anchoring the name regex to the same line as `M R D Number`:
`r"M\s*R\s*D\s*Number\s*:\s*\S+.*?Patient\s*:\s*(.+)"`

**Sanity check implemented:** sum the per-source `[Annexure-N]` charge figures
on page 1 (95916.41 + 11018.10 + 0.00) and compare to the Charges Total
(106934.51). Mismatch → logged as a warning, non-fatal.

### Expected extraction results from the two samples
```
ESTIMATE: mrd=317251, name='ALAALDEN KHALIFA OSMAN ALTAHER',
          doctor='Anil Kumar Murarka', speciality='Plastic-Reconstructive Surgery',
          bed_type='IPS CatB_ V1_Semi Private', duration='2',
          estimation='Estimation 2', doa='15/09/2026', estimate_amount=254310.0

BILL:     mrd=316148, name='FADIL ASSINE GABRIEL', visit='IP0002',
          doa='31/08/2026 15:24', dod='03/09/2026 18:09',
          billed_amount=106934.51, credits_total=106934.51,
          annexure_charge_sum=106934.51, annexure_sanity_ok=True
```
**The two samples are different patients** (317251 vs 316148), so they will
never match each other — a bill run over them correctly reports 1 unmatched
and leaves the estimate row as Pending. That is expected, not a bug.

---

## 4. Code structure (all built and tested)

```
reconciliation_tool/
├── main.py                  entry point; sets APP_DIR, wires logging + GUI
├── requirements.txt         pdfplumber, openpyxl
└── app/
    ├── __init__.py
    ├── pdf_estimate.py      extract_estimate_fields(path) -> dict
    ├── pdf_bill.py          extract_bill_fields(path) -> dict
    ├── excel_store.py       ExcelStore class + compute_status()
    ├── gui.py               Tkinter UI (ReconciliationApp, run_app)
    └── logger_setup.py      file + console logging
```

Outputs written next to the app: `Reconciliation_Output.xlsx`, `reconciliation_log.txt`.

### Module contracts

**`pdf_estimate.extract_estimate_fields(path)`** → dict with keys:
`mrd_number, patient_name, admitting_doctor, duration_of_stay, speciality,
expected_doa, bed_type, estimation_label, estimate_amount, source_file`.
Raises `EstimateExtractionError` on a missing required field.

**`pdf_bill.extract_bill_fields(path)`** → dict with keys:
`mrd_number, patient_name, visit_number, doa, dod, billed_amount,
credits_total, annexure_charge_sum, annexure_sanity_ok, source_file`.
Raises `BillExtractionError` on a missing required field.

**`excel_store.ExcelStore`**: `has_mrd()`, `add_estimate(fields)` (returns
False if MRD already present), `apply_bill(fields)` (returns a result dict, or
None if no matching MRD), `save()`, `overall_counts()`.

**`gui.ReconciliationApp`**: two folder buttons → `_process_estimate_folder()`
/ `_process_billing_folder()` run on background threads; buttons disable during
a run to prevent concurrent Excel writes; all widget updates marshalled via
`root.after(0, ...)`.

---

## 5. Verified behaviour (tested against real Tk widgets, not just unit funcs)

- Estimate extraction on `SAMPLE estimate.pdf` — all 9 fields correct.
- Bill extraction on `IP bill Sample.pdf` — billed 106934.51, sanity check passed.
- Status logic — Closed / Less Amount / Excess Amount all correct via synthetic bills.
- Duplicate MRD — second run over the same folder correctly skips (0 added, 1 already present).
- Unmatched bill — logged as warning for manual review, row stays Pending.
- Excel written with correct headers and values; MRD stored as **text** (preserves leading zeros, keeps match keys type-stable).

---

## 6. Where things stand / what's left

### Done — packaging complete
**`dist\EstimateVsBillReconciliation.exe` — 34 MB, built and verified.**

Verified after building: the exe launches, writes `reconciliation_log.txt`
beside itself (confirming the frozen-path fix below), and — via a throwaway
console probe built from the same venv — correctly parses both sample PDFs,
writes Excel and computes status while frozen. That last check matters because
pdfplumber's binary deps (pypdfium2) can fail to bundle in ways that only
surface when a PDF is actually parsed, not at launch.

It is fully standalone: the recipient needs no Python, no PyInstaller and no
libraries. Expect a Windows SmartScreen warning on first run (unsigned binary)
and a slow first launch (it self-extracts to temp).

**Build from a clean venv — this matters a lot.** Building against the global
Python produced a **284 MB** exe, because that environment has the ML stack
installed and PyInstaller dragged in torch (378 MB), pyarrow (83 MB), scipy
(53 MB), matplotlib, pandas and torchvision — none of which this app uses.
Always build like this:

```
cd reconciliation_tool
python -m venv .venv-build
.venv-build\Scripts\python -m pip install pdfplumber openpyxl pyinstaller
.venv-build\Scripts\pyinstaller --name "EstimateVsBillReconciliation" ^
    --onefile --windowed --clean --noconfirm main.py
```
→ produces `dist\EstimateVsBillReconciliation.exe`.

(`.venv-build\` is a build artifact — don't ship it, don't commit it.)

**Bug fixed for the frozen build (already applied to `main.py`):** under
`--onefile`, `Path(__file__).parent` points into a temp extraction dir that is
deleted on exit, so the Excel output would vanish. `main.py` now does:
```python
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
```

### Planned next (user will do this themselves)
**Swap PDF reading to the Gemini API.** The user will plug in their own Gemini
API key later. The plug-in points are `extract_estimate_fields()` in
`app/pdf_estimate.py` and `extract_bill_fields()` in `app/pdf_bill.py` — as
long as they keep returning the same field dicts documented in §4, everything
downstream (Excel store, status logic, GUI, dashboard) keeps working unchanged.

### Open / unconfirmed
- The extra `Duration of Stay` / `Estimation` columns need user sign-off (§2).
- Never run interactively by a human clicking the real folder-picker dialogs.
  The processing code paths behind those buttons *have* been driven
  programmatically and pass, but the dialogs themselves are untested.
- Only ever tested against the two single sample PDFs, never a real multi-file batch.
- Parsers are built from a single sample of each document type; regexes are
  anchored to literal labels and should hold for the same EMR template, but
  have not been proven against varied real-world files.
