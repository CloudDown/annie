# Smoke test Annie sur Windows (PowerShell 5.1+).
# Usage : powershell -ExecutionPolicy Bypass -File packaging\windows\smoke-test.ps1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

$failures = 0

function Check-Command($name) {
    if (Get-Command $name -ErrorAction SilentlyContinue) {
        Write-Host "[ok] $name : $(Get-Command $name | Select-Object -ExpandProperty Source)"
        return $true
    }
    Write-Host "[FAIL] $name introuvable dans le PATH" -ForegroundColor Red
    return $false
}

Write-Host "==> Dépendances Python"
if (-not (Check-Command python)) { $failures++ }
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    Write-Host "[ok] uv : $($uv.Source)"
} else {
    Write-Host "[warn] uv absent — pip/wheel utilisé à la place"
}

Write-Host ""
Write-Host "==> Outils interactifs (requis pour le menu fzf)"
if (-not (Check-Command fzf)) {
    $failures++
    Write-Host "       winget install junegunn.fzf"
}
if (-not (Check-Command mpv)) {
    Write-Host "[warn] mpv absent — lecture et barres de buffer indisponibles"
    Write-Host "       winget install mpv.mpv"
}

Write-Host ""
Write-Host "==> Tests unitaires"
if ($uv) {
    uv run python -m unittest discover -s tests -q
} else {
    python -m unittest discover -s tests -q
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] tests unitaires" -ForegroundColor Red
    $failures++
} else {
    Write-Host "[ok] tests unitaires"
}

Write-Host ""
Write-Host "==> Import Annie"
if ($uv) {
    uv run python -c "from annie.cli import main; from annie.paths import config_dir; print('config:', config_dir())"
} else {
    python -c "from annie.cli import main; from annie.paths import config_dir; print('config:', config_dir())"
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] import Annie" -ForegroundColor Red
    $failures++
} else {
    Write-Host "[ok] import Annie"
}

Write-Host ""
if ($failures -gt 0) {
    Write-Host "Smoke test : $failures échec(s). Corrigez puis relancez." -ForegroundColor Red
    exit 1
}
Write-Host "Smoke test automatisé : OK."
Write-Host "Étape manuelle : lancer .\annie.py dans PowerShell, choisir un anime et vérifier la lecture mpv + Ctrl-O (magnet)."
