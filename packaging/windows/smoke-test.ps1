# Annie Windows smoke test (PowerShell 5.1+).
# Usage: powershell -ExecutionPolicy Bypass -File packaging\windows\smoke-test.ps1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

$failures = 0

function Check-Command($name) {
    if (Get-Command $name -ErrorAction SilentlyContinue) {
        Write-Host "[ok] $name : $(Get-Command $name | Select-Object -ExpandProperty Source)"
        return $true
    }
    Write-Host "[FAIL] $name not found on PATH" -ForegroundColor Red
    return $false
}

Write-Host "==> Python dependencies"
if (-not (Check-Command python)) { $failures++ }
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    Write-Host "[ok] uv : $($uv.Source)"
} else {
    Write-Host "[warn] uv missing - using pip/wheel instead"
}

Write-Host ""
Write-Host "==> Video player"
if (-not (Check-Command mpv)) {
    Write-Host "[warn] mpv not on PATH (Annie may use a configured path)"
}

Write-Host ""
Write-Host "==> Video player (Annie resolve)"
$playerCode = @'
from annie.stream import resolve_player
print(resolve_player())
'@
if ($uv) {
    uv run python -c $playerCode
} else {
    python -c $playerCode
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] no player resolved by Annie" -ForegroundColor Red
    $failures++
} else {
    Write-Host "[ok] video player"
}

Write-Host ""
Write-Host "==> Unit tests"
if ($uv) {
    uv run python -m unittest discover -s tests -q
} else {
    python -m unittest discover -s tests -q
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] unit tests" -ForegroundColor Red
    $failures++
} else {
    Write-Host "[ok] unit tests"
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
Write-Host "==> annie.cmd launcher"
if (-not (Test-Path (Join-Path $Root "bin\annie.cmd"))) {
    Write-Host "[FAIL] bin\annie.cmd not found" -ForegroundColor Red
    $failures++
} else {
    & cmd /c "bin\annie.cmd --help" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[warn] bin\annie.cmd --help failed"
    } else {
        Write-Host "[ok] bin\annie.cmd"
    }
}

Write-Host ""
if ($failures -gt 0) {
    Write-Host "Smoke test: $failures failure(s). Fix and rerun." -ForegroundColor Red
    exit 1
}
Write-Host "Automated smoke test: OK."
Write-Host "Manual step: run annie (or .\bin\annie.cmd), pick an anime, verify mpv playback + Ctrl-O (magnet)."
