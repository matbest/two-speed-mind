# Kaineros installer for Windows PowerShell.
#
#   iex (irm https://raw.githubusercontent.com/matbest/two-speed-mind/main/install.ps1)
#
# Installs Kaineros into its own private environment - your system Python, PATH quirks, and
# other tools' venvs are never touched:
#   %LOCALAPPDATA%\kaineros\venv   the app's own Python environment
#   %LOCALAPPDATA%\kaineros\bin    the `kaineros` command (added to your user PATH)
#
# Options (set before invoking):
#   $env:KAINEROS_SOURCE = "C:\path\to\checkout"   install from a local checkout (editable)
#   $env:KAINEROS_NO_PATH = "1"                    skip the user-PATH update

$ErrorActionPreference = "Stop"

$AppDir = Join-Path $env:LOCALAPPDATA "kaineros"
$VenvDir = Join-Path $AppDir "venv"
$BinDir = Join-Path $AppDir "bin"
$Source = if ($env:KAINEROS_SOURCE) { $env:KAINEROS_SOURCE }
          else { "git+https://github.com/matbest/two-speed-mind.git" }

Write-Host "Kaineros installer" -ForegroundColor Cyan
Write-Host "  target: $AppDir"

# -- 1. find a Python >= 3.11 (the installer's only prerequisite) -------------------------------
$Python = $null
foreach ($candidate in @("py -3.13", "py -3.12", "py -3.11", "python3.13", "python3.12",
                         "python3.11", "python3", "python")) {
    try {
        $parts = $candidate.Split(" ")
        $v = & $parts[0] $parts[1..($parts.Length)] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($v -and [version]$v -ge [version]"3.11") { $Python = $candidate; break }
    } catch {}
}
if (-not $Python) {
    Write-Host "error: no Python 3.11+ found. Install one first:" -ForegroundColor Red
    Write-Host "  winget install Python.Python.3.13"
    exit 1
}
Write-Host "  python: $Python (found)"

# -- 2. private venv ----------------------------------------------------------------------------
if (-not (Test-Path $VenvDir)) {
    Write-Host "  creating environment..."
    $parts = $Python.Split(" ")
    & $parts[0] $parts[1..($parts.Length)] -m venv $VenvDir
}
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"

# -- 3. install / upgrade kaineros into it ------------------------------------------------------
Write-Host "  installing kaineros from $Source ..."
if ($env:KAINEROS_SOURCE) {
    & $VenvPy -m pip install --quiet --upgrade -e $Source
} else {
    & $VenvPy -m pip install --quiet --upgrade $Source
}
if ($LASTEXITCODE -ne 0) { Write-Host "error: pip install failed" -ForegroundColor Red; exit 1 }

# -- 4. the `kaineros` command ------------------------------------------------------------------
New-Item -ItemType Directory -Force $BinDir | Out-Null
@"
@echo off
"$VenvPy" -m kaineros %*
"@ | Out-File -FilePath (Join-Path $BinDir "kaineros.cmd") -Encoding ascii

# -- 5. user PATH -------------------------------------------------------------------------------
if (-not $env:KAINEROS_NO_PATH) {
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($UserPath -notlike "*$BinDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$UserPath;$BinDir", "User")
        Write-Host "  added to PATH: $BinDir"
        $PathNote = "Open a NEW terminal, then:"
    } else {
        $PathNote = "Run:"
    }
} else {
    $PathNote = "Run (PATH update skipped):"
}

Write-Host ""
Write-Host "Kaineros installed." -ForegroundColor Green
Write-Host "$PathNote"
Write-Host "  kaineros --openrouter     chat (asks for your OpenRouter key on first run)"
Write-Host "  kaineros                  chat on the free deterministic fakes"
