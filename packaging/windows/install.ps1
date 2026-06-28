# Installation Annie sur Windows (PowerShell 5.1+).
# Usage : powershell -ExecutionPolicy Bypass -File packaging\windows\install.ps1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

function Require-Python {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        Write-Error "Python 3.11+ requis. Installez-le depuis https://www.python.org/downloads/"
    }
    $version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    $parts = $version.Split(".")
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
        Write-Error "Python 3.11+ requis (trouvé $version)."
    }
}

function Install-Annie {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        Write-Host "==> uv sync"
        uv sync
        uv run python -c "from annie.user_config import ensure_user_config; ensure_user_config()"
        Write-Host ""
        Write-Host "Lancement : .\annie.py"
        Write-Host "Ou après activation du venv : annie"
        return
    }

    Write-Host "==> pip install (uv non trouvé)"
    python -m pip install --upgrade pip build
    python -m build --wheel
    $wheel = Get-ChildItem dist\*.whl | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    python -m pip install --force-reinstall $wheel.FullName
    python -c "from annie.user_config import ensure_user_config; ensure_user_config()"
    Write-Host ""
    Write-Host "Lancement : annie"
}

function Warn-OptionalTools {
    $missing = @()
    foreach ($tool in @("fzf", "mpv")) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            $missing += $tool
        }
    }
    if ($missing.Count -gt 0) {
        Write-Warning "Outils manquants dans le PATH : $($missing -join ', ')"
        Write-Host "  fzf : winget install junegunn.fzf  (ou choco install fzf)"
        Write-Host "  mpv : winget install mpv.mpv       (ou choco install mpv)"
    }
}

Require-Python
Install-Annie
Warn-OptionalTools
