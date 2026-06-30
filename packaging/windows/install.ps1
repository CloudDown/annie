# Installation Annie sur Windows (PowerShell 5.1+).
# Usage : powershell -ExecutionPolicy Bypass -File packaging\windows\install.ps1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

function Require-Python {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        Write-Error @"
Python 3.11+ requis.
Installez-le depuis https://www.python.org/downloads/
Cochez "Add python.exe to PATH" pendant l installation.
"@
    }
    $version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    $parts = $version.Split(".")
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
        Write-Error "Python 3.11+ requis (trouve $version)."
    }
}

function Repair-BrokenRootVenv {
    $rootCfg = Join-Path $Root "pyvenv.cfg"
    $dotVenvCfg = Join-Path $Root ".venv\pyvenv.cfg"
    if (-not (Test-Path $rootCfg)) {
        return
    }
    if (Test-Path $dotVenvCfg) {
        Write-Warning "pyvenv.cfg a la racine du depot (en plus de .venv) - peut casser Python."
    } else {
        Write-Warning "Environnement virtuel incorrect a la racine (pyvenv.cfg + Lib/Scripts)."
        Write-Warning "Annie utilise .venv\ - suppression des artefacts a la racine..."
    }
    foreach ($name in @("pyvenv.cfg", "Lib", "Scripts", "Include", "share")) {
        $path = Join-Path $Root $name
        if (Test-Path $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}

function Install-Annie {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        Write-Host "==> uv sync"
        uv sync
        uv run python -c 'from annie.user_config import ensure_user_config; ensure_user_config()'
        return
    }

    Write-Host "==> pip install (uv non trouve - installez uv pour une meilleure experience)"
    python -m pip install --upgrade pip build
    python -m build --wheel
    $wheel = Get-ChildItem dist\*.whl | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    python -m pip install --force-reinstall $wheel.FullName
    python -c 'from annie.user_config import ensure_user_config; ensure_user_config()'
}

function Install-AnnieCommand {
    $binDir = Join-Path $env:LOCALAPPDATA "Programs\Annie\bin"
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null

    $launcher = Join-Path $binDir "annie.cmd"
    $rootEsc = $Root.Replace('"', '""')
    $cmdLines = @(
        '@echo off'
        'setlocal EnableExtensions'
        "set `"ROOT=$rootEsc`""
        'set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"'
        'if exist "%VENV_PY%" ('
        '  "%VENV_PY%" "%ROOT%\annie.py" %*'
        '  exit /b %ERRORLEVEL%'
        ')'
        'where python >nul 2>&1'
        'if errorlevel 1 ('
        "  echo Python introuvable. Relancez packaging\windows\install.ps1 depuis $rootEsc"
        '  exit /b 1'
        ')'
        'python "%ROOT%\annie.py" %*'
        'exit /b %ERRORLEVEL%'
    )
    $cmdLines | Set-Content -LiteralPath $launcher -Encoding ASCII

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) {
        $userPath = ""
    }
    if ($userPath.Split(";") -notcontains $binDir) {
        [Environment]::SetEnvironmentVariable("Path", "$binDir;$userPath", "User")
        $env:Path = "$binDir;$env:Path"
        Write-Host "==> Commande annie ajoutee au PATH utilisateur : $binDir"
        Write-Host "    Fermez et rouvrez le terminal, puis tapez : annie"
    } else {
        Write-Host "==> Commande annie deja dans le PATH : $binDir"
    }
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

function Test-AnnieLaunch {
    $pyCheck = 'from annie.cli import main; from annie.paths import config_dir; print("ok", config_dir())'
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        uv run python -c $pyCheck
    } else {
        python -c $pyCheck
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Import Annie echoue - voir les messages ci-dessus."
    }
    Write-Host "==> Import Annie : OK"
}

Require-Python
Repair-BrokenRootVenv
Install-Annie
Install-AnnieCommand
Test-AnnieLaunch
Warn-OptionalTools

Write-Host ""
Write-Host "Installation terminee."
Write-Host "  Depuis ce dossier : .\annie.cmd   ou   .\annie.py"
Write-Host "  Partout (nouveau terminal) : annie"
