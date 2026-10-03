# Make the app deployable on enterprise devices without the SmartScreen / admin prompt

## Context
Colleagues running `dist\EstimateVsBillReconciliation.exe` on enterprise devices get
"Windows protected your PC", and continuing needs admin rights. What we checked:
the exe's manifest is already `asInvoker` (it never asks for admin itself), so the
admin prompt comes from company policy that blocks bypassing SmartScreen for
**unsigned, downloaded** files. The code alone can't remove it. It goes away when the
app is **signed with a certificate the devices trust and/or approved and deployed by IT**.
The user doesn't know yet which route (IT signing/deployment, Trusted Signing,
bought certificate) is available. So this plan does the app-side work every route
needs, and writes a brief to take to IT.

Current build problems for enterprise use:
- `--onefile` unpacks DLLs into `%TEMP%\_MEI…` on every launch. AppLocker/WDAC
  commonly block running code from `%TEMP%`, and antivirus often flags it.
- There's no version/publisher info (Properties shows nothing and SmartScreen says
  "Unknown publisher"), which makes it harder for IT to allowlist.
- The Excel file and log are written **next to the exe** (`main.py` `APP_DIR`). If IT
  installs to `C:\Program Files\…`, users can't write there, so the app would fail.

## Changes

### 1. Write output to a per-user folder — `reconciliation_tool/main.py`
> **Done (2026-09-27), implemented differently from the text below:** the folder is
> `Documents\Bill Reconciliation\` (log in `...\Logs\`), both are changeable from
> Settings, and it applies when running from source too. See `app/settings.py` and
> HANDOFF.md §4. Also note `build.ps1` now exists (progress-bar onefile build);
> step 2 should extend it rather than replace it.

- When frozen, save `Reconciliation_Output.xlsx` and `reconciliation_log.txt` in
  `Documents\Estimate vs Bill Reconciliation\`. Find Documents with
  `SHGetFolderPathW(CSIDL_PERSONAL)` through `ctypes` so OneDrive-redirected Documents
  works, and fall back to `%LOCALAPPDATA%`. Create the folder if it's missing.
- One-time migration: if a workbook already sits next to the exe and none exists in
  the new folder, copy it over so existing users keep their data.
- Running from source (not frozen) keeps the current behaviour (next to `main.py`).
- `app/web_ui.py` `Api.get_info()` also returns the full workbook path. `app/ui/app.js`
  `init()` sets it as the `#workbook` label's tooltip. The existing Open Excel / Log
  buttons (`Api.open_excel/open_log`) already open files by path, so they need no change.

### 2. One-folder build with version info — new `reconciliation_tool/build.ps1` + `version_info.txt`
- `build.ps1` recreates or uses `.venv-build` and runs PyInstaller with `--onedir --windowed
  --clean --noconfirm --add-data "app/ui;app/ui" --version-file version_info.txt`. The
  output is `dist\EstimateVsBillReconciliation\` (exe + `_internal\`), zipped to
  `dist\EstimateVsBillReconciliation.zip` for handing over.
- Nothing runs from `%TEMP%`, and startup is faster.
- `version_info.txt`: ProductName "Estimate vs Bill Reconciliation", FileDescription,
  FileVersion/ProductVersion 1.1.0.0, OriginalFilename, and CompanyName as a clearly
  marked value the user or IT confirms (to be set to whoever the signing certificate
  is issued to).
- Keep the default `asInvoker` manifest. Don't use `--uac-admin`.
- Optional signing step: `-CertThumbprint <thumb>` (cert in the Windows store) or
  `-SignCommand "<custom>"` (for Trusted Signing / IT tooling). Runs `signtool sign /fd
  SHA256 /tr <timestamp> /td SHA256` on the exe and on any `.exe/.dll/.pyd` in the
  output whose `Get-AuthenticodeSignature` reports NotSigned, then verifies the result.
  With no signing arguments the script just builds.
- Delete the old `dist\EstimateVsBillReconciliation.exe` onefile build so there's no
  confusion. Leave the user's existing `dist\Reconciliation_Output.xlsx` and log alone
  (they're migrated by step 1 only when the exe runs next to them).

### 3. IT brief — new `reconciliation_tool/IT_DEPLOYMENT.md`
Short, for the hospital IT team:
- What the app does. It runs as a standard user (`asInvoker`), makes no network
  calls, reads only PDFs the user picks, and writes only to
  `Documents\Estimate vs Bill Reconciliation\`. It needs the Edge WebView2 Runtime.
- Options for IT, cheapest first: (a) sign `EstimateVsBillReconciliation.exe` with the
  enterprise code-signing certificate (the exact `signtool` / `build.ps1 -CertThumbprint`
  command); (b) allowlist it (AppLocker/WDAC publisher or hash rule, Defender
  "allow" indicator); (c) deploy it via Intune (Win32 app) / SCCM to `Program Files` with
  a Start-menu shortcut. Files installed this way carry no "downloaded from internet"
  mark, so SmartScreen doesn't appear and no admin is needed at run time.
- External alternatives if IT can't sign: Microsoft Trusted Signing (needs the
  organisation's identity verified) or a commercial OV certificate. Note that
  SmartScreen reputation for a new certificate takes time.

### 4. Docs
- `HANDOFF.md` §6: replace the onefile build instructions with `build.ps1`, and note
  the new output location and the IT brief.

## Verification
1. `python -m pytest tests -q` still passes (the extraction code is unchanged).
2. `.\build.ps1` builds cleanly. Check `(Get-Item dist\EstimateVsBillReconciliation\EstimateVsBillReconciliation.exe).VersionInfo`
   shows the product/version fields, and the manifest is still `asInvoker`.
3. Copy the build folder to a scratch location, launch it, and confirm
   `Documents\Estimate vs Bill Reconciliation\reconciliation_log.txt` gets "UI loaded"
   and nothing is written beside the exe. Also check migration: put a workbook beside
   the exe and confirm it's copied over on first launch.
4. Rerun the UI-driving probe (rebuilt as onedir) on the two sample folders and
   confirm the Needs review tab still shows the MRD 316148 error.
5. Signing path: create a throwaway self-signed cert in `Cert:\CurrentUser\My`, run
   `build.ps1 -CertThumbprint …`, and confirm `Get-AuthenticodeSignature` shows the
   signer (an untrusted root is expected). Then delete the cert.
6. Not verifiable from here: whether the warning actually disappears on the enterprise
   devices. That depends on IT signing/deploying it, and is the user's follow-up.
