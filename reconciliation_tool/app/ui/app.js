"use strict";

const STATUS = {
  "Closed":        { c: "var(--green)", cs: "var(--green-soft)" },
  "Less Amount":   { c: "var(--amber)", cs: "var(--amber-soft)" },
  "Excess Amount": { c: "var(--red)",   cs: "var(--red-soft)" },
  "Pending":       { c: "var(--slate)", cs: "var(--slate-soft)" },
};
const ALL = "All";
const ANIMATE_ROWS = 30;   // only the first rows get the entrance animation, keeps big tables fast

const PATIENT_COLUMNS = [
  { key: "MRD Number", label: "MRD" },
  { key: "Patient Name", label: "Patient" },
  { key: "Doctor Name", label: "Doctor" },
  { key: "Speciality", label: "Speciality" },
  { key: "Surgery", label: "Surgery" },
  { key: "Bed Type", label: "Bed type" },
  { key: "Duration of Stay (Days)", label: "Days" },
  { key: "Estimate Amount", label: "Estimate", num: true },
  { key: "Billed Amount", label: "Billed", num: true },
  { key: "Difference", label: "Difference", num: true, diff: true },
  { key: "Status", label: "Status", status: true },
];
const RUN_COLUMNS = [
  { key: "mrd_number", label: "MRD" },
  { key: "patient_name", label: "Patient" },
  { key: "estimate_amount", label: "Estimate", num: true },
  { key: "billed_amount", label: "Billed", num: true },
  { key: "difference", label: "Difference", num: true, diff: true },
  { key: "status", label: "Status", status: true },
];

const ISSUE_COLUMNS = ["", "File", "MRD", "Patient", "Problem", "What to check"];

const state = {
  folders: { estimate: null, billing: null },
  rows: [],
  filter: ALL,
  sort: { col: null, desc: false },
  running: false,
};

const $ = (sel) => document.querySelector(sel);
const api = () => window.pywebview.api;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

// ------------------------------------------------------------ formatting --
function fmtAmount(v) {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  return Number.isFinite(n)
    ? n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : String(v);
}
const money = (v) => "₹ " + fmtAmount(v || 0);

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function badge(status) {
  if (!status) return "";
  const s = STATUS[status] || STATUS.Pending;
  return `<span class="badge" style="--c:${s.c};--cs:${s.cs}">${esc(status)}</span>`;
}

// Smoothly count a number up/down to its new value.
function countTo(el, target, format = (n) => String(Math.round(n))) {
  const from = Number(el.dataset.value || 0);
  el.dataset.value = target;
  if (reducedMotion || from === target) { el.textContent = format(target); return; }
  const start = performance.now(), dur = 600;
  const step = (t) => {
    const p = Math.min((t - start) / dur, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = format(from + (target - from) * eased);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// --------------------------------------------------------- status/progress --
function showStatus(text, kind = "ok") {
  const footer = document.querySelector("footer");
  $("#status").textContent = text;
  footer.classList.toggle("error", kind === "error");
  footer.classList.toggle("busy", kind === "busy");
  footer.classList.remove("flash");
  void footer.offsetWidth;   // restart the fade
  footer.classList.add("flash");
}

function onProgress(phase, done, total) {
  $("#progress-bar").style.transform = `scaleX(${total ? done / total : 1})`;
  const what = phase === "estimate" ? "estimate" : "bill";
  $("#progress-label").textContent = total
    ? `Reading ${what} ${Math.min(done + 1, total)} of ${total}`
    : `No PDFs in the ${phase} folder`;
}
// Called from Python via evaluate_js.
window.onProgress = onProgress;
window.setStatus = (text) => showStatus(text, "busy");

// ---------------------------------------------------------------- folders --
function renderFolder(kind) {
  const box = $(`#folder-${kind}`);
  const f = state.folders[kind];
  const path = box.querySelector(".path");
  box.classList.toggle("set", !!f);
  if (f) {
    box.title = f.path;
    // bdi keeps the text in order while the rtl trick shows the end of long paths.
    path.innerHTML = `<bdi>${esc(f.path)} · <span class="count">${f.pdf_count} PDF${f.pdf_count === 1 ? "" : "s"}</span></bdi>`;
  } else {
    box.title = "";
    path.textContent = kind === "estimate" ? "Choose Estimate Performa PDFs" : "Choose IP Bill PDFs";
  }
  updateRunButton();
}

function updateRunButton() {
  const any = state.folders.estimate || state.folders.billing;
  $("#run").disabled = !any || state.running;
  document.querySelectorAll(".folder").forEach((b) => (b.disabled = state.running));
  if (!state.running) {
    $("#progress-label").textContent = any ? "Ready to run" : "Choose at least one folder";
  }
}

async function chooseFolder(kind) {
  if (state.running) return;
  try {
    const res = await api().pick_folder(kind);
    if (!res) return;
    state.folders[kind] = res;
    renderFolder(kind);
    const name = kind === "estimate" ? "Estimate" : "Billing";
    if (res.pdf_count === 0) showStatus(`No PDF files found in ${res.path}`, "error");
    else showStatus(`${name} folder selected · ${res.pdf_count} PDF${res.pdf_count === 1 ? "" : "s"}`);
  } catch (e) {
    showStatus("Could not open the folder picker: " + (e.message || e), "error");
  }
}

// -------------------------------------------------------------------- run --
async function run() {
  if (state.running) return;
  state.running = true;
  updateRunButton();
  const progress = $(".progress");
  progress.classList.remove("done");
  progress.classList.add("busy");
  $("#progress-bar").style.transform = "scaleX(0)";
  $("#run").classList.add("busy");
  showStatus("Starting…", "busy");

  let label = "Run failed";
  try {
    const res = await api().run(
      state.folders.estimate ? state.folders.estimate.path : null,
      state.folders.billing ? state.folders.billing.path : null,
    );
    if (res.summary) applySummary(res.summary, true);
    if (res.billing) renderRun(res.billing);
    if (res.ok) renderIssues(res.issues || []);
    const hasErrors = (res.issues || []).some((i) => i.severity === "error");
    showStatus(res.message, res.ok && !hasErrors ? "ok" : "error");
    if (res.ok) {
      // Clear so the same folders aren't re-run by accident.
      state.folders = { estimate: null, billing: null };
      label = "Done · choose folders for the next run";
      progress.classList.add("done");
      $("#progress-bar").style.transform = "scaleX(1)";
    } else {
      label = "Not saved · fix the problem and run again";
    }
  } catch (e) {
    showStatus("Run failed: " + (e.message || e), "error");
  } finally {
    state.running = false;
    progress.classList.remove("busy");
    $("#run").classList.remove("busy");
    renderFolder("estimate");
    renderFolder("billing");
    $("#progress-label").textContent = label;
  }
}

// ---------------------------------------------------------------- summary --
function buildKpis() {
  const cards = [[ALL, "Total patients", "var(--blue)", "var(--blue-soft)"]].concat(
    Object.entries(STATUS).map(([s, v]) => [s, s, v.c, v.cs]),
  );
  $("#kpis").innerHTML = cards.map(([key, label, c, cs], i) => `
    <button class="kpi" data-key="${esc(key)}" style="--c:${c};--cs:${cs};--i:${i}">
      <div class="label">${esc(label)}</div>
      <div class="value">0</div>
      <div class="pct">—</div>
      <div class="bar"><i></i></div>
    </button>`).join("");
  document.querySelectorAll(".kpi").forEach((el) => {
    el.addEventListener("click", () => {
      const k = el.dataset.key;
      setFilter(state.filter === k ? ALL : k);
      showTab("all");
    });
  });
}

function applySummary(s, animateRows = false) {
  const counts = s.counts;
  const total = counts["Total Patients"] || 0;
  document.querySelectorAll(".kpi").forEach((el) => {
    const k = el.dataset.key;
    const n = k === ALL ? total : counts[k] || 0;
    countTo(el.querySelector(".value"), n);
    el.querySelector(".pct").textContent =
      k === ALL ? "in the workbook" : total ? `${Math.round((n / total) * 100)}% of patients` : "—";
    el.querySelector(".bar i").style.transform = `scaleX(${total ? n / total : 0})`;
  });

  countTo($("#m-estimate"), s.total_estimate || 0, money);
  countTo($("#m-billed"), s.total_billed || 0, money);
  const net = s.net_difference || 0;
  const netEl = $("#m-net");
  countTo(netEl, net, money);
  if (total === (counts["Pending"] || 0)) netEl.style.color = "var(--text)";
  else if (Math.abs(net) < 0.005) netEl.style.color = "var(--green)";
  else if (net > 0) netEl.style.color = "var(--amber)";
  else netEl.style.color = "var(--red)";

  state.rows = s.rows;
  renderPatients(animateRows);
}

// ----------------------------------------------------------------- tables --
function headerHtml(columns, sortable) {
  return "<tr>" + columns.map((c) => {
    const sorted = sortable && state.sort.col === c.key;
    const cls = [c.num ? "num" : "", sortable ? "sortable" : "", sorted ? "sorted" : ""].join(" ");
    const arrow = sorted ? `<span class="arrow ${state.sort.desc ? "desc" : ""}">▲</span>` : "";
    return `<th class="${cls}" data-key="${esc(c.key)}">${esc(c.label)}${arrow}</th>`;
  }).join("") + "</tr>";
}

function cellHtml(c, v) {
  if (c.status) return `<td>${badge(v)}</td>`;
  if (!c.num) return `<td>${esc(v)}</td>`;
  let cls = "num";
  if (c.diff && v !== null && v !== undefined && v !== "") {
    const n = Number(v);
    cls += Math.abs(n) < 0.005 ? " diff-zero" : n > 0 ? " diff-pos" : " diff-neg";
  }
  return `<td class="${cls}">${fmtAmount(v)}</td>`;
}

function rowsHtml(columns, rows, animate) {
  return rows.map((r, i) => {
    const a = animate && i < ANIMATE_ROWS && !reducedMotion ? ` class="anim" style="--r:${i}"` : "";
    return `<tr${a}>` + columns.map((c) => cellHtml(c, r[c.key])).join("") + "</tr>";
  }).join("");
}

function renderPatients(animate = false) {
  const q = $("#search").value.trim().toLowerCase();
  let rows = state.rows.filter((r) =>
    (state.filter === ALL || r["Status"] === state.filter) &&
    (!q || String(r["MRD Number"] ?? "").toLowerCase().includes(q) ||
           String(r["Patient Name"] ?? "").toLowerCase().includes(q)));

  const { col, desc } = state.sort;
  if (col) {
    const numeric = PATIENT_COLUMNS.find((c) => c.key === col)?.num;
    rows = rows.slice().sort((a, b) => {
      const va = a[col], vb = b[col];
      const na = va === null || va === undefined || va === "";
      const nb = vb === null || vb === undefined || vb === "";
      if (na !== nb) return na ? 1 : -1;   // blanks always last
      const cmp = numeric ? Number(va) - Number(vb)
                          : String(va).toLowerCase().localeCompare(String(vb).toLowerCase());
      return desc ? -cmp : cmp;
    });
  }

  const table = $("#patients");
  table.tHead.innerHTML = headerHtml(PATIENT_COLUMNS, true);
  table.tBodies[0].innerHTML = rowsHtml(PATIENT_COLUMNS, rows, animate);
  $("#patients-empty").hidden = state.rows.length > 0;
  $("#count").textContent = `${rows.length} of ${state.rows.length}`;
}

function setFilter(status) {
  state.filter = status;
  document.querySelectorAll(".kpi").forEach((el) =>
    el.classList.toggle("active", el.dataset.key === status && status !== ALL));
  const chip = $("#filter-label");
  if (status === ALL) {
    chip.hidden = true;
  } else {
    const s = STATUS[status];
    chip.hidden = false;
    chip.style.cssText = `--c:${s.c};--cs:${s.cs}`;
    chip.innerHTML = `${esc(status)}<button title="Clear filter"><svg><use href="#i-x"/></svg></button>`;
    chip.querySelector("button").addEventListener("click", () => setFilter(ALL));
  }
  renderPatients(true);
}

function renderRun(bill) {
  const rows = bill.run_rows;
  const table = $("#run-table");
  table.tHead.innerHTML = headerHtml(RUN_COLUMNS, false);
  table.tBodies[0].innerHTML = rowsHtml(RUN_COLUMNS, rows, true);

  const byStatus = {};
  rows.forEach((r) => (byStatus[r.status] = (byStatus[r.status] || 0) + 1));
  const parts = ["Closed", "Less Amount", "Excess Amount"]
    .filter((s) => byStatus[s]).map((s) => `${byStatus[s]} ${s.toLowerCase()}`);
  if (bill.unmatched) parts.push(`${bill.unmatched} unmatched (no estimate on file)`);
  if (bill.failed) parts.push(`${bill.failed} failed`);
  $("#run-label").textContent =
    `Latest billing run: ${rows.length} matched` + (parts.length ? " · " + parts.join(" · ") : "");
  $("#tab-run").textContent = `This run (${rows.length})`;
  showTab("run");
}

function renderIssues(issues) {
  const errors = issues.filter((i) => i.severity === "error").length;
  const warnings = issues.length - errors;
  // Errors first; within a severity keep file order.
  const sorted = issues.slice().sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "error" ? -1 : 1));
  const sev = (s) => s === "error"
    ? `<span class="sev" style="--c:var(--red)"><svg><use href="#i-alert"/></svg>Error</span>`
    : `<span class="sev" style="--c:var(--amber)"><svg><use href="#i-alert"/></svg>Check</span>`;

  const table = $("#issues-table");
  table.tHead.innerHTML = "<tr>" + ISSUE_COLUMNS.map((c) => `<th>${esc(c)}</th>`).join("") + "</tr>";
  table.tBodies[0].innerHTML = sorted.map((i, n) => {
    const a = n < ANIMATE_ROWS && !reducedMotion ? ` class="anim" style="--r:${n}"` : "";
    return `<tr${a}><td>${sev(i.severity)}</td><td>${esc(i.file)}</td><td>${esc(i.mrd)}</td>` +
      `<td>${esc(i.patient)}</td><td class="problem">${esc(i.problem)}</td>` +
      `<td class="detail">${esc(i.detail)}</td></tr>`;
  }).join("");
  table.hidden = issues.length === 0;
  $("#issues-empty").hidden = issues.length > 0;

  const count = $("#issues-count");
  count.hidden = issues.length === 0;
  count.textContent = issues.length;
  count.classList.toggle("warn", errors === 0);
  const parts = [];
  if (errors) parts.push(`${errors} error${errors === 1 ? "" : "s"}`);
  if (warnings) parts.push(`${warnings} to double-check`);
  $("#issues-label").textContent = issues.length
    ? `Latest run: ${parts.join(" · ")}. The Excel rows for these patients may be incomplete or wrong.`
    : "Latest run: no problems found.";
  if (issues.length) showTab("issues");
  else moveInk();   // tab label width changed
}

function moveInk() {
  const active = document.querySelector(".tab.active");
  const ink = $("#tab-ink");
  ink.style.width = active.offsetWidth - 24 + "px";
  ink.style.transform = `translateX(${active.offsetLeft + 12}px)`;
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("#panel-all").hidden = name !== "all";
  $("#panel-run").hidden = name !== "run";
  $("#panel-issues").hidden = name !== "issues";
  $("#toolbar-all").hidden = name !== "all";
  moveInk();
}

// ------------------------------------------------------------------- init --
function wireUi() {
  buildKpis();
  $("#run-table").tHead.innerHTML = headerHtml(RUN_COLUMNS, false);

  ["estimate", "billing"].forEach((kind) => {
    const box = $(`#folder-${kind}`);
    box.addEventListener("click", () => chooseFolder(kind));
    box.querySelector(".clear").addEventListener("click", (e) => {
      e.stopPropagation();
      state.folders[kind] = null;
      renderFolder(kind);
    });
  });
  $("#run").addEventListener("click", run);

  let searchTimer;
  $("#search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => renderPatients(false), 60);
  });
  // One delegated listener for sorting instead of rebinding on every render.
  $("#patients").tHead.addEventListener("click", (e) => {
    const th = e.target.closest("th");
    if (!th) return;
    const col = th.dataset.key;
    state.sort = { col, desc: state.sort.col === col ? !state.sort.desc : false };
    renderPatients(false);
  });

  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
  window.addEventListener("resize", moveInk);
  $("#open-excel").addEventListener("click", async () => {
    const err = await api().open_excel();
    if (err) showStatus(err, "error");
  });
  $("#open-log").addEventListener("click", async () => {
    const err = await api().open_log();
    if (err) showStatus(err, "error");
  });
  wireSettings();
  moveInk();
}

// --------------------------------------------------------------- settings --
function renderLocations(loc) {
  $("#loc-output").textContent = loc.output_dir;
  $("#loc-log").textContent = loc.log_dir;
  const isDefault = loc.output_dir === loc.default_output_dir && loc.log_dir === loc.default_log_dir;
  $("#loc-reset").disabled = isDefault;
  $("#loc-reset").style.visibility = isDefault ? "hidden" : "visible";
}

async function applyLocationChange(call) {
  if (state.running) return;
  const res = await call();
  if (!res) return;   // folder dialog cancelled
  renderLocations(res.locations);
  const msg = $("#loc-msg");
  msg.textContent = res.message;
  msg.classList.toggle("error", !res.ok);
  const info = await api().get_info();
  $("#workbook").textContent = info.workbook;
  $("#workbook").title = info.workbook_path;
  applySummary(await api().get_summary(), true);   // may be a different workbook now
  showStatus(res.message, res.ok ? "ok" : "error");
}

function wireSettings() {
  const dlg = $("#settings");
  $("#open-settings").addEventListener("click", async () => {
    $("#loc-msg").textContent = "";
    renderLocations(await api().get_locations());
    dlg.showModal();
  });
  $("#settings-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });   // backdrop
  dlg.querySelectorAll("[data-change]").forEach((b) => b.addEventListener("click", () =>
    applyLocationChange(() => b.dataset.change === "output" ? api().change_output_dir() : api().change_log_dir())));
  dlg.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", async () => {
    const err = await api().open_folder(b.dataset.open);
    if (err) showStatus(err, "error");
  }));
  $("#loc-reset").addEventListener("click", () => applyLocationChange(() => api().reset_locations()));
}

async function init() {
  try {
    const info = await api().get_info();
    $("#workbook").textContent = info.workbook;
    $("#workbook").title = info.workbook_path;
    applySummary(await api().get_summary(), true);
    showStatus("Ready · choose the estimate and/or billing folder, then click Run");
  } catch (e) {
    showStatus("Could not load the workbook: " + (e.message || e), "error");
  }
}

wireUi();
if (window.pywebview && window.pywebview.api) init();
else window.addEventListener("pywebviewready", init);
