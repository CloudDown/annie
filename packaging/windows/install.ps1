# Installation complete Annie sur Windows (PowerShell 5.1+).
# Installe Python, uv, fzf et mpv si necessaire, puis configure la commande annie.
#
# Usage :
#   powershell -ExecutionPolicy Bypass -File packaging\windows\install.ps1
param(
    [switch]$SkipOptional
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

$script:PythonExe = $null

function Write-Utf8BomFile {
    param(
        [string]$Path,
        [string[]]$Lines
    )
    $content = ($Lines -join "`r`n") + "`r`n"
    $utf8Bom = New-Object System.Text.UTF8Encoding $true
    [System.IO.File]::WriteAllText($Path, $content, $utf8Bom)
}

function Refresh-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if ($machine) { $parts += $machine.Split(";") }
    if ($user) { $parts += $user.Split(";") }
    foreach ($ver in @("313", "312", "311")) {
        $base = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$ver"
        if (Test-Path $base) {
            $parts = @($base, (Join-Path $base "Scripts")) + $parts
        }
    }
    $uvBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path $uvBin) {
        $parts = @($uvBin) + $parts
    }
    $env:Path = (($parts | Where-Object { $_ -and $_.Trim() }) -join ";")
}

function Test-Winget {
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Install-WingetPackage {
    param(
        [string]$Id,
        [string]$Label
    )
    if (-not (Test-Winget)) {
        Write-Warning "winget absent - impossible d installer $Label automatiquement."
        return $false
    }
    Write-Host "==> Installation $Label ($Id) via winget..."
    winget install --id $Id -e --accept-package-agreements --accept-source-agreements --disable-interactivity --scope user 2>$null
    $code = $LASTEXITCODE
    if ($code -ne 0 -and $code -ne -1978335189 -and $code -ne 2316632107) {
        winget install --id $Id -e --accept-package-agreements --accept-source-agreements --disable-interactivity 2>$null
        $code = $LASTEXITCODE
    }
    if ($code -eq 0 -or $code -eq -1978335189 -or $code -eq 2316632107) {
        return $true
    }
    Write-Warning "winget $Id code sortie $code"
    return $false
}

function Test-PythonRunnable {
    param([string]$Exe)
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) {
        return $false
    }
    if ($Exe -match '\\Microsoft\\WindowsApps\\') {
        return $false
    }
    $output = & $Exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    $text = ($output | Out-String).Trim()
    if (-not $text -or $text -match "was not found|Microsoft Store") {
        return $false
    }
    return $text -match '^\d+\.\d+$'
}

function Find-PythonExe {
    Refresh-SessionPath

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($flag in @("-3.12", "-3.11", "-3")) {
            $candidate = & py $flag -c "import sys; print(sys.executable)" 2>$null
            if ($candidate) {
                $candidate = ($candidate | Out-String).Trim()
                if (Test-PythonRunnable $candidate) {
                    return $candidate
                }
            }
        }
    }

    $search = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python313\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    foreach ($candidate in $search) {
        if (Test-PythonRunnable $candidate) {
            return $candidate
        }
    }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and (Test-PythonRunnable $cmd.Source)) {
        return $cmd.Source
    }

    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-PythonRunnable $venvPy) {
        return $venvPy
    }

    return $null
}

function Get-PythonVersion {
    param([string]$Exe)
    $output = & $Exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    $text = ($output | Out-String).Trim()
    if ($text -match '^(\d+)\.(\d+)$') {
        return @{ Major = [int]$Matches[1]; Minor = [int]$Matches[2]; Text = $text }
    }
    return $null
}

function Install-Python {
    if (-not (Test-Winget)) {
        Write-Error @"
Python 3.11+ introuvable et winget absent.
Installez Python : https://www.python.org/downloads/  (cochez Add to PATH)
Ou installez App Installer depuis le Microsoft Store pour obtenir winget.
Desactivez aussi les alias Python dans :
  Parametres > Applications > Parametres avances > Alias d execution d applications
"@
    }

    Write-Host "==> Python introuvable - installation automatique..."
    $installed = $false
    foreach ($id in @("Python.Python.3.12", "Python.Python.3.11")) {
        if (Install-WingetPackage -Id $id -Label "Python") {
            $installed = $true
            break
        }
    }
    if (-not $installed) {
        Write-Error "Echec installation Python via winget."
    }

    Refresh-SessionPath
    Start-Sleep -Seconds 2
}

function Ensure-Python {
    $script:PythonExe = Find-PythonExe
    if (-not $script:PythonExe) {
        Install-Python
        $script:PythonExe = Find-PythonExe
    }
    if (-not $script:PythonExe) {
        Write-Error @"
Python toujours introuvable apres installation.
1. Fermez ce terminal, rouvrez PowerShell
2. Relancez packaging\windows\install.ps1
3. Si le message Microsoft Store apparait, desactivez les alias python.exe dans
   Parametres > Applications > Alias d execution d applications
"@
    }

    $ver = Get-PythonVersion $script:PythonExe
    if (-not $ver) {
        Write-Error "Python detecte mais ne repond pas : $script:PythonExe"
    }
    if ($ver.Major -lt 3 -or ($ver.Major -eq 3 -and $ver.Minor -lt 11)) {
        Write-Error "Python 3.11+ requis (trouve $($ver.Text))."
    }
    Write-Host "==> Python $($ver.Text) : $script:PythonExe"
}

function Invoke-AnniePython {
    param([string[]]$PythonArgs)
    & $script:PythonExe @PythonArgs
    if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Test-PythonStdlib {
    $prefix = & $script:PythonExe -c "import sys; print(sys.base_prefix)" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Impossible de lire sys.base_prefix."
        return
    }
    $prefix = ($prefix | Out-String).Trim()
    $encodings = Join-Path $prefix "Lib\encodings\__init__.py"
    if (-not (Test-Path -LiteralPath $encodings)) {
        Write-Warning @"
Python corrompu (Lib\encodings manquant dans $prefix).
Reinstallez : winget uninstall --id Python.Python.3.12
              winget install --id Python.Python.3.12
"@
    }
}

function Repair-BrokenRootVenv {
    $rootCfg = Join-Path $Root "pyvenv.cfg"
    if (-not (Test-Path -LiteralPath $rootCfg)) {
        return
    }
    Write-Warning "pyvenv.cfg a la racine - suppression (Annie utilise .venv\)..."
    foreach ($name in @("pyvenv.cfg", "Lib", "Scripts", "Include", "share")) {
        $path = Join-Path $Root $name
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}

function Ensure-Uv {
    Refresh-SessionPath
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        Write-Host "==> uv : $($uv.Source)"
        return
    }

    if (Install-WingetPackage -Id "astral-sh.uv" -Label "uv") {
        Refresh-SessionPath
        $uv = Get-Command uv -ErrorAction SilentlyContinue
        if ($uv) {
            Write-Host "==> uv installe : $($uv.Source)"
            return
        }
    }

    Write-Host "==> Installation uv via pip..."
    Invoke-AnniePython -PythonArgs @("-m", "pip", "install", "--upgrade", "uv")
    Refresh-SessionPath
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        $scripts = Join-Path (Split-Path $script:PythonExe -Parent) "Scripts"
        $env:Path = "$scripts;$env:Path"
        $uv = Get-Command uv -ErrorAction SilentlyContinue
    }
    if (-not $uv) {
        Write-Warning "uv non trouve - repli sur pip."
    } else {
        Write-Host "==> uv : $($uv.Source)"
    }
}

function Install-Annie {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        Write-Host "==> uv sync"
        & uv sync
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & uv run python -c "from annie.user_config import ensure_user_config; ensure_user_config()"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        return
    }

    Write-Host "==> pip install (sans uv)"
    Invoke-AnniePython -PythonArgs @("-m", "pip", "install", "--upgrade", "pip", "build")
    Invoke-AnniePython -PythonArgs @("-m", "build", "--wheel")
    $wheel = Get-ChildItem dist\*.whl | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $wheel) {
        Write-Error "Wheel introuvable dans dist\"
    }
    Invoke-AnniePython -PythonArgs @("-m", "pip", "install", "--force-reinstall", $wheel.FullName)
    Invoke-AnniePython -PythonArgs @("-c", "from annie.user_config import ensure_user_config; ensure_user_config()")
}

function Remove-ConflictingAnnieExe {
    $candidates = @()
    $venvExe = Join-Path $Root ".venv\Scripts\annie.exe"
    if (Test-Path -LiteralPath $venvExe) {
        $candidates += $venvExe
    }
    $scriptsExe = Join-Path (Split-Path $script:PythonExe -Parent) "Scripts\annie.exe"
    if (Test-Path -LiteralPath $scriptsExe) {
        $candidates += $scriptsExe
    }
    foreach ($exe in $candidates) {
        Remove-Item -LiteralPath $exe -Force
        Write-Host "==> Supprime $exe (utilisez annie.cmd)"
    }
}

function Annie-CmdLines {
    param([string]$RootEsc, [string]$PythonEsc)
    return @(
        '@echo off'
        'setlocal EnableExtensions'
        'chcp 65001 >nul'
        'set "PYTHONIOENCODING=utf-8"'
        'set "PYTHONUTF8=1"'
        "set `"ROOT=$RootEsc`""
        'set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"'
        'if exist "%VENV_PY%" ('
        '  "%VENV_PY%" "%ROOT%\annie.py" %*'
        '  exit /b %ERRORLEVEL%'
        ')'
        "set `"PY=$PythonEsc`""
        'if exist "%PY%" ('
        '  "%PY%" "%ROOT%\annie.py" %*'
        '  exit /b %ERRORLEVEL%'
        ')'
        'echo Python introuvable. Relancez packaging\windows\install.ps1'
        'exit /b 1'
    )
}

function Install-AnnieCommand {
    $binDir = Join-Path $env:LOCALAPPDATA "Programs\Annie\bin"
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null

    $launcher = Join-Path $binDir "annie.cmd"
    $rootEsc = $Root.Replace('"', '""')
    $pythonEsc = $script:PythonExe.Replace('"', '""')
    Write-Utf8BomFile -Path $launcher -Lines (Annie-CmdLines -RootEsc $rootEsc -PythonEsc $pythonEsc)

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { $userPath = "" }
    if ($userPath.Split(";") -notcontains $binDir) {
        [Environment]::SetEnvironmentVariable("Path", "$binDir;$userPath", "User")
        $env:Path = "$binDir;$env:Path"
        Write-Host "==> Commande annie ajoutee au PATH : $binDir"
    } else {
        Write-Host "==> Commande annie deja dans le PATH : $binDir"
    }
}

function Ensure-OptionalTool {
    param(
        [string]$Name,
        [string]$WingetId
    )
    Refresh-SessionPath
    if (Get-Command $Name -ErrorAction SilentlyContinue) {
        Write-Host "==> $Name deja installe"
        return
    }
    Install-WingetPackage -Id $WingetId -Label $Name | Out-Null
    Refresh-SessionPath
    if (Get-Command $Name -ErrorAction SilentlyContinue) {
        Write-Host "==> $Name installe"
    } else {
        Write-Warning "$Name toujours absent - installez manuellement : winget install $WingetId"
    }
}

function Install-OptionalTools {
    if ($SkipOptional) {
        Write-Host "==> Outils optionnels ignores (-SkipOptional)"
        return
    }
    Write-Host "==> Outils interactifs (fzf, mpv)"
    Ensure-OptionalTool -Name "fzf" -WingetId "junegunn.fzf"
    Ensure-OptionalTool -Name "mpv" -WingetId "mpv.mpv"
}

function Test-AnnieLaunch {
    $pyCheck = "from annie.cli import main; from annie.paths import config_dir; print('ok', config_dir())"
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        & uv run python -c $pyCheck
    } else {
        Invoke-AnniePython -PythonArgs @("-c", $pyCheck)
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Import Annie echoue."
    }
    Write-Host "==> Import Annie : OK"
}

Write-Host "========================================"
Write-Host " Installation Annie (Windows)"
Write-Host "========================================"
Write-Host ""

Refresh-SessionPath
Ensure-Python
Test-PythonStdlib
Repair-BrokenRootVenv
Ensure-Uv
Install-Annie
Remove-ConflictingAnnieExe
Install-AnnieCommand
Install-OptionalTools
Test-AnnieLaunch

Write-Host ""
Write-Host "========================================"
Write-Host " Installation terminee."
Write-Host " Fermez et rouvrez le terminal."
Write-Host " Puis tapez : annie"
Write-Host " Ou depuis ce dossier : .\annie.cmd"
Write-Host "========================================"
