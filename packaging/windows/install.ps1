# Installation Annie sur Windows (appele par install-windows.bat).
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
2. Relancez install-windows.bat
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
        '  call "%VENV_PY%" "%ROOT%\annie.py" %*'
        '  exit /b %ERRORLEVEL%'
        ')'
        "set `"PY=$PythonEsc`""
        'if exist "%PY%" ('
        '  call "%PY%" "%ROOT%\annie.py" %*'
        '  exit /b %ERRORLEVEL%'
        ')'
        'echo Python introuvable. Relancez install-windows.bat'
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
    }
    if ($env:Path.Split(";") -notcontains $binDir) {
        $env:Path = "$binDir;$env:Path"
    }
    Write-Host "==> Commande annie : $binDir\annie.cmd"
    if ($userPath.Split(";") -notcontains $binDir) {
        Write-Host "    (ajoutee au PATH utilisateur)"
    } else {
        Write-Host "    (deja dans le PATH utilisateur)"
    }
}

function Invoke-AnnieUvPython {
    param([string]$Code)
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        & uv run python -c $Code
    } else {
        Invoke-AnniePython -PythonArgs @("-c", $Code)
    }
    if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        return $false
    }
    return $true
}

function Add-UserPathEntry {
    param([string]$Directory)
    if (-not $Directory -or -not (Test-Path -LiteralPath $Directory)) {
        return
    }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { $userPath = "" }
    if ($userPath.Split(";") -contains $Directory) {
        if ($env:Path.Split(";") -notcontains $Directory) {
            $env:Path = "$Directory;$env:Path"
        }
        return
    }
    [Environment]::SetEnvironmentVariable("Path", "$Directory;$userPath", "User")
    if ($env:Path.Split(";") -notcontains $Directory) {
        $env:Path = "$Directory;$env:Path"
    }
    Write-Host "==> Ajoute au PATH utilisateur : $Directory"
}

function Test-ProgramRunnable {
    param([string]$Exe)
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) {
        return $false
    }
    & $Exe --version 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { return $true }
    & $Exe -version 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Get-ProgramSearchDirs {
    param([string]$Name)
    $stem = $Name.ToLower().Replace(".exe", "")
    $dirs = @()
    switch ($stem) {
        "mpv" {
            $dirs = @(
                "$env:ProgramFiles\mpv",
                "$env:ProgramFiles\MPV Player",
                "C:\mpv",
                "$env:LOCALAPPDATA\Programs\mpv"
            )
        }
        "vlc" {
            $dirs = @(
                "$env:ProgramFiles\VideoLAN\VLC",
                "${env:ProgramFiles(x86)}\VideoLAN\VLC"
            )
        }
        "ffplay" {
            $dirs = @(
                "$env:ProgramFiles\ffmpeg\bin",
                "${env:ProgramFiles(x86)}\ffmpeg\bin",
                "C:\ffmpeg\bin",
                "$env:LOCALAPPDATA\Microsoft\WinGet\Links"
            )
        }
        "fzf" {
            $dirs = @(
                "$env:LOCALAPPDATA\Microsoft\WinGet\Links",
                "$env:USERPROFILE\scoop\shims"
            )
        }
    }
    foreach ($root in @($env:LOCALAPPDATA, $env:USERPROFILE)) {
        if (-not $root) { continue }
        $dirs += @(
            (Join-Path $root "Programs\$stem"),
            (Join-Path $root "Programs\$stem\bin"),
            (Join-Path $root "scoop\apps\$stem\current"),
            (Join-Path $root "scoop\shims")
        )
    }
    $dirs += @(
        "C:\ProgramData\chocolatey\bin",
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"),
        (Join-Path $env:LOCALAPPDATA "Programs")
    )
    return $dirs | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique
}

function Find-ProgramExe {
    param([string]$Name)
    Refresh-SessionPath
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source)) {
        return $cmd.Source
    }
    $exeName = if ($Name.ToLower().EndsWith(".exe")) { $Name } else { "$Name.exe" }
    foreach ($dir in (Get-ProgramSearchDirs -Name $Name)) {
        if (-not (Test-Path -LiteralPath $dir)) { continue }
        $direct = Join-Path $dir $exeName
        if (Test-Path -LiteralPath $direct) {
            return (Resolve-Path -LiteralPath $direct).Path
        }
        if (Test-Path -LiteralPath $dir -PathType Container) {
            $found = Get-ChildItem -Path $dir -Filter $exeName -Recurse -Depth 5 -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($found) {
                return $found.FullName
            }
        }
    }
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
        $found = Get-ChildItem -Path $root -Filter $exeName -Recurse -Depth 4 -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) {
            return $found.FullName
        }
    }
    return $null
}

function Find-BestMediaPlayer {
    foreach ($name in @("mpv", "vlc", "ffplay")) {
        $exe = Find-ProgramExe -Name $name
        if ($exe -and (Test-ProgramRunnable -Exe $exe)) {
            return @{ Name = $name; Exe = $exe }
        }
    }
    return $null
}

function Install-WingetPackages {
    param(
        [string]$Label,
        [string[]]$Ids
    )
    foreach ($id in $Ids) {
        if (Install-WingetPackage -Id $id -Label $Label) {
            Start-Sleep -Seconds 2
            Refresh-SessionPath
            return $true
        }
    }
    return $false
}

function Install-ViaChocolatey {
    param([string]$Package)
    if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
        return $false
    }
    Write-Host "==> Installation $Package via Chocolatey..."
    choco install $Package -y --no-progress 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Refresh-SessionPath
        return $true
    }
    return $false
}

function Install-ViaScoop {
    param([string]$Package)
    if (-not (Get-Command scoop -ErrorAction SilentlyContinue)) {
        return $false
    }
    Write-Host "==> Installation $Package via Scoop..."
    scoop install $Package 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Refresh-SessionPath
        return $true
    }
    return $false
}

function Ensure-ProgramOnPath {
    param(
        [string]$Name,
        [string[]]$WingetIds,
        [string[]]$ChocolateyPackages = @(),
        [string[]]$ScoopPackages = @()
    )
    Refresh-SessionPath
    $exe = Find-ProgramExe -Name $Name
    if ($exe) {
        Write-Host "==> $Name deja installe : $exe"
        Add-UserPathEntry -Directory (Split-Path $exe -Parent)
        return $exe
    }
    if ($WingetIds -and (Install-WingetPackages -Label $Name -Ids $WingetIds)) {
        $exe = Find-ProgramExe -Name $Name
        if ($exe) {
            Write-Host "==> $Name installe : $exe"
            Add-UserPathEntry -Directory (Split-Path $exe -Parent)
            return $exe
        }
    }
    foreach ($pkg in $ChocolateyPackages) {
        if (Install-ViaChocolatey -Package $pkg) {
            $exe = Find-ProgramExe -Name $Name
            if ($exe) {
                Write-Host "==> $Name installe (Chocolatey) : $exe"
                Add-UserPathEntry -Directory (Split-Path $exe -Parent)
                return $exe
            }
        }
    }
    foreach ($pkg in $ScoopPackages) {
        if (Install-ViaScoop -Package $pkg) {
            $exe = Find-ProgramExe -Name $Name
            if ($exe) {
                Write-Host "==> $Name installe (Scoop) : $exe"
                Add-UserPathEntry -Directory (Split-Path $exe -Parent)
                return $exe
            }
        }
    }
    return $null
}

function Ensure-MediaPlayer {
    $player = Find-BestMediaPlayer
    if ($player) {
        Write-Host "==> Lecteur detecte : $($player.Name) -> $($player.Exe)"
        Add-UserPathEntry -Directory (Split-Path $player.Exe -Parent)
        return $player
    }

    Write-Host "==> Aucun lecteur detecte - installation automatique..."
    $null = Ensure-ProgramOnPath -Name "mpv" -WingetIds @(
        "shinchiro.mpv",
        "mpv.mpv",
        "mpv-player.mpv-CI.MSVC",
        "zhongfly.mpv"
    ) -ChocolateyPackages @("mpv") -ScoopPackages @("mpv")
    $player = Find-BestMediaPlayer
    if ($player) {
        Write-Host "==> Lecteur installe : $($player.Name) -> $($player.Exe)"
        return $player
    }

    $null = Ensure-ProgramOnPath -Name "vlc" -WingetIds @(
        "VideoLAN.VLC"
    ) -ChocolateyPackages @("vlc") -ScoopPackages @("vlc")
    $player = Find-BestMediaPlayer
    if ($player) {
        Write-Host "==> Lecteur de repli : $($player.Name) -> $($player.Exe)"
        return $player
    }

    $null = Ensure-ProgramOnPath -Name "ffplay" -WingetIds @(
        "Gyan.FFmpeg",
        "ffmpeg"
    ) -ChocolateyPackages @("ffmpeg") -ScoopPackages @("ffmpeg")
    $player = Find-BestMediaPlayer
    if ($player) {
        Write-Host "==> Lecteur de repli : $($player.Name) -> $($player.Exe)"
        return $player
    }

    return $null
}

function Configure-AnnieMediaPlayer {
    $code = @'
from annie.user_config import ensure_media_player_config
from annie.stream import resolve_player
exe = ensure_media_player_config(force=True)
if not exe:
    raise SystemExit("no player")
kind = resolve_player()
print(kind, exe)
'@
    if (-not (Invoke-AnnieUvPython -Code $code)) {
        return $false
    }
    return $true
}

function Test-MediaPlayerReady {
    $code = @'
from annie.stream import resolve_player
from annie.paths import find_best_media_player
kind = resolve_player()
found = find_best_media_player()
print(kind, found[1] if found else "")
'@
    if (-not (Invoke-AnnieUvPython -Code $code)) {
        Write-Warning "Lecteur video non configure."
        Write-Warning "  Installez mpv : winget install -e --id shinchiro.mpv"
        Write-Warning "  Puis relancez install-windows.bat"
        return $false
    }
    Write-Host "==> Lecteur video : OK"
    return $true
}

function Install-OptionalTools {
    if ($SkipOptional) {
        Write-Host "==> Outils optionnels ignores (-SkipOptional)"
        return
    }
    Write-Host "==> Outils interactifs (fzf, lecteur video)"
    Ensure-ProgramOnPath -Name "fzf" -WingetIds @(
        "junegunn.fzf"
    ) -ChocolateyPackages @("fzf") -ScoopPackages @("fzf") | Out-Null
    $player = Ensure-MediaPlayer
    if ($player) {
        if (-not (Configure-AnnieMediaPlayer)) {
            Write-Warning "Lecteur trouve mais configuration Annie echouee."
        }
    } else {
        Write-Warning "Aucun lecteur video installe (mpv, vlc ou ffmpeg/ffplay)."
        Write-Warning "Annie fonctionnera mais ne pourra pas lire les episodes."
    }
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
Test-MediaPlayerReady

Write-Host ""
Write-Host "========================================"
Write-Host " Installation terminee."
Write-Host ""
Write-Host " Lancer Annie tout de suite :"
Write-Host "   annie"
Write-Host "   .\annie.cmd"
Write-Host ""
Write-Host " Nouveau terminal : fermez celui-ci, rouvrez, puis : annie"
Write-Host "========================================"
