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

Write-Host "==> Dependances Python"
if (-not (Check-Command python)) { $failures++ }
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    Write-Host "[ok] uv : $($uv.Source)"
} else {
    Write-Host "[warn] uv absent - pip/wheel utilise a la place"
}

Write-Host ""
Write-Host "==> Outils interactifs (requis pour le menu fzf)"
if (-not (Check-Command fzf)) {
    $failures++
    Write-Host "       winget install junegunn.fzf"
}
if (-not (Check-Command mpv)) {
    Write-Host "[warn] mpv absent - lecture et barres de buffer indisponibles"
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
$pyImport = 'from annie.cli import main; from annie.paths import config_dir; print("config:", config_dir())'
if ($uv) {
    uv run python -c $pyImport
} else {
    python -c $pyImport
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] import Annie" -ForegroundColor Red
    $failures++
} else {
    Write-Host "[ok] import Annie"
}

Write-Host ""
Write-Host "==> Lanceur annie.cmd"
if (-not (Test-Path (Join-Path $Root "annie.cmd"))) {
    Write-Host "[FAIL] annie.cmd introuvable" -ForegroundColor Red
    $failures++
} else {
    & cmd /c "annie.cmd --help" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[warn] annie.cmd --help a echoue (peut etre normal sans fzf)"
    } else {
        Write-Host "[ok] annie.cmd"
    }
}

Write-Host ""
if ($failures -gt 0) {
    Write-Host "Smoke test : $failures echec(s). Corrigez puis relancez." -ForegroundColor Red
    exit 1
}
Write-Host "Smoke test automatise : OK."
Write-Host "Etape manuelle : lancer annie (ou .\annie.cmd), choisir un anime et verifier la lecture mpv + Ctrl-O (magnet)."
