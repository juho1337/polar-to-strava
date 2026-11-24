<#
.SYNOPSIS
  Reproducible one-folder Windows build of polar-to-strava-engine with PyInstaller.
.DESCRIPTION
  Builds in an isolated virtual environment so the development environment is untouched.
  Usage:  .\packaging\build_engine.ps1 -Work <scratch-dir>
  Output: <Work>\dist\polar-to-strava-engine\polar-to-strava-engine.exe
  See docs/packaging.md for measured size/startup and licensing notes.
#>
param([Parameter(Mandatory = $true)][string]$Work)

$ErrorActionPreference = 'Continue'  # native stderr output is not an error in Windows PowerShell 5.1
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}
$repo = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force $Work | Out-Null
$venv = Join-Path $Work 'venv'
Invoke-Checked python @('-m', 'venv', $venv)
Invoke-Checked "$venv\Scripts\python.exe" @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip')
# Build from a copy so pip never writes build/ or egg-info into the working tree.
$src = Join-Path $Work 'src'
Remove-Item -Recurse -Force $src -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $src | Out-Null
robocopy $repo $src /E /XD .git .venv build dist .mypy_cache .ruff_cache .pytest_cache .pytest_tmp __pycache__ /NFL /NDL /NJH /NJS | Out-Null
Invoke-Checked "$venv\Scripts\python.exe" @('-m', 'pip', 'install', '--quiet', $src, 'pyinstaller>=6,<7')
$entry = Join-Path $Work 'engine_entry.py'
Set-Content -Path $entry -Encoding utf8 -Value @'
import sys
from integration.engine import main

if __name__ == "__main__":
    sys.exit(main())
'@
Invoke-Checked "$venv\Scripts\pyinstaller.exe" @(
    '--noconfirm', '--clean', '--onedir', '--name', 'polar-to-strava-engine',
    '--distpath', (Join-Path $Work 'dist'), '--workpath', (Join-Path $Work 'build'),
    '--specpath', $Work,
    '--collect-submodules', 'fit_tool', '--collect-submodules', 'integration',
    '--collect-submodules', 'strava', '--collect-submodules', 'polar',
    '--collect-submodules', 'services', '--collect-submodules', 'domain',
    '--collect-submodules', 'fit', '--collect-submodules', 'core',
    '--collect-data', 'tzdata', $entry
)
Write-Host "Built: $(Join-Path $Work 'dist\polar-to-strava-engine\polar-to-strava-engine.exe')"
