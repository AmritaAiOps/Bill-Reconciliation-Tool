"""Tkinter GUI: folder selection, batch processing with progress, and the
after-run dashboard (this-run lists + overall counts)."""
import logging
import threading
from pathlib import Path
from tkinter import Tk, filedialog, StringVar, END
from tkinter import ttk

from .excel_store import ExcelStore, STATUS_CLOSED, STATUS_LESS
from .pdf_estimate import extract_estimate_fields, EstimateExtractionError
from .pdf_bill import extract_bill_fields, BillExtractionError

logger = logging.getLogger(__name__)


def _fmt_amount(v):
    if v is None:
        return ""
    return f"{v:,.2f}"


class ReconciliationApp:
    def __init__(self, root: Tk, excel_path, log_path):
        self.root = root
        self.log_path = Path(log_path)
        self.root.title("Estimate vs Bill Reconciliation Tool")
        self.root.geometry("1050x700")
        self.root.minsize(900, 600)

        self.store = ExcelStore(excel_path)

        self.status_var = StringVar(value="Ready.")
        self._buttons = []
        self._build_ui()
        self._refresh_counts()

    # ---------------------------------------------------------------- UI --
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        btn1 = ttk.Button(top, text="Select Estimate Folder", command=self.on_select_estimate_folder)
        btn1.pack(side="left", padx=5)
        btn2 = ttk.Button(top, text="Select Billing Folder", command=self.on_select_billing_folder)
        btn2.pack(side="left", padx=5)
        self._buttons = [btn1, btn2]

        self.progress = ttk.Progressbar(top, mode="determinate", length=300)
        self.progress.pack(side="left", padx=15, fill="x", expand=True)

        ttk.Label(self.root, textvariable=self.status_var, padding=(10, 0)).pack(anchor="w")

        counts_frame = ttk.LabelFrame(self.root, text="Overall Counts (whole Excel file)", padding=10)
        counts_frame.pack(fill="x", padx=10, pady=8)
        self.count_labels = {}
        for key in ["Total Patients", "Closed", "Less Amount", "Excess Amount", "Pending"]:
            cell = ttk.Frame(counts_frame)
            cell.pack(side="left", expand=True, fill="x")
            ttk.Label(cell, text=key, font=("Segoe UI", 9, "bold")).pack()
            var = StringVar(value="0")
            ttk.Label(cell, textvariable=var, font=("Segoe UI", 16)).pack()
            self.count_labels[key] = var

        run_frame = ttk.Frame(self.root, padding=10)
        run_frame.pack(fill="both", expand=True)

        closed_frame = ttk.LabelFrame(run_frame, text="Closed in this run", padding=5)
        closed_frame.pack(fill="both", expand=True, pady=5)
        self.closed_tree = self._make_tree(
            closed_frame, ["MRD Number", "Patient Name", "Estimate Amount", "Billed Amount"]
        )

        less_frame = ttk.LabelFrame(run_frame, text="Less Amount in this run", padding=5)
        less_frame.pack(fill="both", expand=True, pady=5)
        self.less_tree = self._make_tree(
            less_frame,
            ["MRD Number", "Patient Name", "Estimate Amount", "Billed Amount", "Difference"],
        )

    def _make_tree(self, parent, columns):
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=6)
        for c in columns:
            tree.heading(c, text=c)
            tree.column(c, width=160, anchor="center")
        tree.pack(fill="both", expand=True, side="left")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        scroll.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scroll.set)
        return tree

    # ------------------------------------------------------------ actions --
    def on_select_estimate_folder(self):
        folder = filedialog.askdirectory(title="Select Estimate Folder")
        if not folder:
            return
        self._set_buttons_enabled(False)
        threading.Thread(target=self._process_estimate_folder, args=(folder,), daemon=True).start()

    def on_select_billing_folder(self):
        folder = filedialog.askdirectory(title="Select Billing Folder")
        if not folder:
            return
        self._set_buttons_enabled(False)
        threading.Thread(target=self._process_billing_folder, args=(folder,), daemon=True).start()

    def _pdf_files(self, folder):
        return sorted(Path(folder).glob("*.pdf"))

    def _process_estimate_folder(self, folder):
        files = self._pdf_files(folder)
        self._set_progress(0, len(files))
        added, skipped, failed = 0, 0, 0
        for i, f in enumerate(files, 1):
            try:
                fields = extract_estimate_fields(f)
                if self.store.has_mrd(fields["mrd_number"]):
                    skipped += 1
                    logger.info("Estimate skipped (MRD already present): %s", f.name)
                else:
                    self.store.add_estimate(fields)
                    added += 1
                    logger.info("Estimate added: %s (MRD %s)", f.name, fields["mrd_number"])
            except EstimateExtractionError as e:
                failed += 1
                logger.error("Estimate extraction FAILED for %s: %s", f.name, e)
            except Exception:
                failed += 1
                logger.exception("Unexpected error reading estimate %s", f.name)
            self._set_progress(i, len(files))

        self.store.save()
        self._set_status(
            f"Estimate folder processed: {len(files)} PDFs, {added} new patients added, "
            f"{skipped} already present, {failed} failed extraction. See reconciliation_log.txt."
        )
        self._refresh_counts()
        self._set_buttons_enabled(True)

    def _process_billing_folder(self, folder):
        files = self._pdf_files(folder)
        self._set_progress(0, len(files))
        closed_rows, less_rows = [], []
        matched, unmatched, failed = 0, 0, 0
        for i, f in enumerate(files, 1):
            try:
                fields = extract_bill_fields(f)
                if not fields.get("annexure_sanity_ok", True):
                    logger.warning(
                        "Annexure sum mismatch in %s (MRD %s): annexure sum=%.2f vs summary total=%.2f",
                        f.name, fields["mrd_number"], fields["annexure_charge_sum"], fields["billed_amount"],
                    )
                result = self.store.apply_bill(fields)
                if result is None:
                    unmatched += 1
                    logger.warning(
                        "No matching MRD %s in Excel for bill %s -- flagged for manual review",
                        fields["mrd_number"], f.name,
                    )
                else:
                    matched += 1
                    logger.info(
                        "Bill matched: %s (MRD %s) -> status %s", f.name, fields["mrd_number"], result["status"]
                    )
                    if result["status"] == STATUS_CLOSED:
                        closed_rows.append(result)
                    elif result["status"] == STATUS_LESS:
                        less_rows.append(result)
            except BillExtractionError as e:
                failed += 1
                logger.error("Bill extraction FAILED for %s: %s", f.name, e)
            except Exception:
                failed += 1
                logger.exception("Unexpected error reading bill %s", f.name)
            self._set_progress(i, len(files))

        self.store.save()
        self._populate_tree(
            self.closed_tree, closed_rows, ["mrd_number", "patient_name", "estimate_amount", "billed_amount"]
        )
        self._populate_tree(
            self.less_tree,
            less_rows,
            ["mrd_number", "patient_name", "estimate_amount", "billed_amount", "difference"],
        )
        self._set_status(
            f"Billing folder processed: {len(files)} PDFs, {matched} matched, "
            f"{unmatched} unmatched (no estimate on file), {failed} failed extraction. "
            f"See reconciliation_log.txt."
        )
        self._refresh_counts()
        self._set_buttons_enabled(True)

    # -------------------------------------------------------- UI helpers --
    def _populate_tree(self, tree, rows, keys):
        def update():
            tree.delete(*tree.get_children())
            for row in rows:
                values = []
                for k in keys:
                    v = row.get(k)
                    values.append(_fmt_amount(v) if k in ("estimate_amount", "billed_amount", "difference") else v)
                tree.insert("", END, values=values)

        self.root.after(0, update)

    def _refresh_counts(self):
        counts = self.store.overall_counts()

        def update():
            for key, var in self.count_labels.items():
                var.set(str(counts.get(key, 0)))

        self.root.after(0, update)

    def _set_progress(self, value, maximum):
        def update():
            self.progress["maximum"] = max(maximum, 1)
            self.progress["value"] = value

        self.root.after(0, update)

    def _set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def _set_buttons_enabled(self, enabled: bool):
        def update():
            state = "normal" if enabled else "disabled"
            for b in self._buttons:
                b.configure(state=state)

        self.root.after(0, update)


def run_app(excel_path, log_path):
    root = Tk()
    ReconciliationApp(root, excel_path, log_path)
    root.mainloop()
