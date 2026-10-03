# Builds dist\EstimateVsBillReconciliation.exe from a clean venv, with a
# progress bar at the top of the window. Run via build.bat (double-click).
# Full output is also saved to build_log.txt.

$ExeName = "EstimateVsBillReconciliation"
$Venv    = ".venv-build"
$LogFile = "build_log.txt"

Set-Location $PSScriptRoot
Set-Content $LogFile "Build started $(Get-Date)" -Encoding utf8

$script:percent = 0
$script:status  = "Starting"

function Show-Progress([double]$pct, [string]$status) {
    $script:percent = [math]::Min([math]::Max($pct, $script:percent), 100)
    if ($status) { $script:status = $status }
    $p = [int]$script:percent
    Write-Progress -Activity "Building $ExeName.exe" -Status "$p% - $script:status" -PercentComplete $p
    $Host.UI.RawUI.WindowTitle = "$p% - Building $ExeName"
}

function Fail([string]$message) {
    Write-Progress -Activity "Building $ExeName.exe" -Completed
    $Host.UI.RawUI.WindowTitle = "BUILD FAILED - $ExeName"
    Write-Host ""
    Write-Host "BUILD FAILED: $message" -ForegroundColor Red
    Write-Host "Full output: $PSScriptRoot\$LogFile"
    exit 1
}

# Runs a command, echoing each output line (dimmed) and to the log. For each
# line, $onLine gets the text and can call Show-Progress. Returns the exit code.
function Invoke-Logged([string]$exe, [string[]]$arguments, [scriptblock]$onLine) {
    $ErrorActionPreference = "Continue"  # PyInstaller and pip log to stderr
    & $exe @arguments 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line -ForegroundColor DarkGray
        Add-Content $LogFile $line -Encoding utf8
        if ($onLine) { & $onLine $line }
    }
    return $LASTEXITCODE
}

# ---- 1. Is the old exe still running? (0-2%) ------------------------------
Show-Progress 0 "Checking that the app is closed"
if (Get-Process -Name $ExeName -ErrorAction SilentlyContinue) {
    Fail "$ExeName.exe is running. Close it and run build.bat again."
}

# ---- 2. Build environment, first run only (2-30%) --------------------------
# Never build with the global Python: it drags in torch/pandas/etc. and the
# exe balloons to ~284 MB.
$python = "$Venv\Scripts\python.exe"
# A venv whose project folder was moved/renamed keeps working python.exe, but
# its Scripts\*.exe launchers still point at the old path, so tools are always
# run as "python -m". If the venv cannot import what the build needs (base
# Python removed, half-finished install), start it again from scratch.
if (Test-Path $python) {
    $ErrorActionPreference = "Continue"
    & $python -c "import PyInstaller, pdfplumber, openpyxl, webview" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Add-Content $LogFile "Build environment $Venv is broken; recreating it." -Encoding utf8
        Write-Host "Build environment is broken; recreating it." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $Venv
    }
}
if (-not (Test-Path $python)) {
    Show-Progress 2 "Creating build environment (first run only)"
    if ((Invoke-Logged "python" @("-m", "venv", $Venv)) -ne 0) { Fail "Could not create $Venv. Is Python installed?" }

    Show-Progress 8 "Installing libraries (first run only)"
    $code = Invoke-Logged $python @("-m", "pip", "install", "-r", "requirements.txt", "pyinstaller") {
        param($line)
        if ($line -match "^(Collecting|Downloading|Installing)") { Show-Progress ($script:percent + 0.6) }
    }
    if ($code -ne 0) { Fail "Library install failed." }
}

# ---- 3. PyInstaller (30-100%) ----------------------------------------------
# Jump to a milestone when PyInstaller announces a stage; creep forward on
# every other line so the bar keeps moving during the long analysis.
$milestones = @(
    @{ Match = "Analyzing modules for base_library"; Pct = 35; Status = "Analyzing Python standard library" },
    @{ Match = "Analyzing .*main\.py";               Pct = 45; Status = "Analyzing app code and libraries" },
    @{ Match = "Looking for dynamic libraries";      Pct = 65; Status = "Collecting DLLs" },
    @{ Match = "Building PYZ";                       Pct = 72; Status = "Packing Python modules" },
    @{ Match = "Building PKG";                       Pct = 80; Status = "Packing data files" },
    @{ Match = "Building EXE";                       Pct = 90; Status = "Writing the exe" },
    @{ Match = "Build complete";                     Pct = 100; Status = "Done" }
)
$script:cap = 35  # the creep never passes the next milestone

Show-Progress 30 "Starting PyInstaller"
$code = Invoke-Logged $python @(
    "-m", "PyInstaller", "--name", $ExeName, "--onefile", "--windowed", "--clean", "--noconfirm",
    "--add-data", "app/ui;app/ui", "main.py"
) {
    param($line)
    for ($i = 0; $i -lt $milestones.Count; $i++) {
        if ($line -match $milestones[$i].Match) {
            Show-Progress $milestones[$i].Pct $milestones[$i].Status
            $script:cap = if ($i + 1 -lt $milestones.Count) { $milestones[$i + 1].Pct - 1 } else { 100 }
            return
        }
    }
    if ($script:percent -lt $script:cap) { Show-Progress ([math]::Min($script:percent + 0.15, $script:cap)) }
}
if ($code -ne 0) { Fail "PyInstaller exited with code $code (see the output above)." }

Show-Progress 100 "Done"
Write-Progress -Activity "Building $ExeName.exe" -Completed
$Host.UI.RawUI.WindowTitle = "100% - Build complete - $ExeName"
$size = [math]::Round((Get-Item "dist\$ExeName.exe").Length / 1MB, 1)
Write-Host ""
Write-Host "BUILD COMPLETE: $PSScriptRoot\dist\$ExeName.exe ($size MB)" -ForegroundColor Green
exit 0
