# python-gcloud installer for Windows (PowerShell 5.1+).
#
# Idempotent. Re-run any time to refresh source / re-auth.
#
# Usage (one-shot):
#   iwr -useb https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/python-gcloud/install.ps1 | iex
# or:
#   .\install.ps1
#
# If execution policy blocks the script:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1

[CmdletBinding()]
param(
    [string]$InstallParent = $env:USERPROFILE
)

$ErrorActionPreference = 'Stop'

$ArchiveUrl = 'https://github.com/webgress/gcp_bulk_user/archive/refs/heads/main.zip'
$InstallDir = Join-Path $InstallParent 'gcp_bulk_user-main\python-gcloud'

# gcloud SDK on Windows is shipped as a self-contained zip — no .exe installer required.
$GcloudZipUrl = 'https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-windows-x86_64.zip'
$GcloudHome  = Join-Path $env:USERPROFILE 'google-cloud-sdk'

function Say  { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok   { param($m) Write-Host "  v $m" -ForegroundColor Green }
function Warn { param($m) Write-Host "  ! $m" -ForegroundColor Yellow }
function Die  { param($m) Write-Host "  x $m" -ForegroundColor Red; exit 1 }

function Resolve-Python {
    foreach ($cmd in @('python', 'python3', 'py')) {
        $exe = (Get-Command $cmd -ErrorAction SilentlyContinue)
        if (-not $exe) { continue }
        $checkArgs = @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)')
        & $exe.Source @checkArgs 2>$null
        if ($LASTEXITCODE -eq 0) {
            $verArgs = @('-c', 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")')
            $version = & $exe.Source @verArgs
            Ok "Python: $version ($($exe.Source))"
            return $exe.Source
        }
    }
    Die 'Python 3.10+ not found. Install from https://www.python.org/downloads/ and re-run. Tick "Add Python to PATH" during setup.'
}

function Install-Gcloud {
    if (Get-Command gcloud -ErrorAction SilentlyContinue) {
        $first = (gcloud --version 2>$null | Select-Object -First 1)
        Ok "gcloud already installed: $first"
        return
    }
    Say "Installing Google Cloud SDK into $GcloudHome"
    $tmp = Join-Path $env:TEMP ("gcloud-" + [System.Guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp 'gcloud.zip'
        Invoke-WebRequest -Uri $GcloudZipUrl -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $env:USERPROFILE -Force
        # Run the bundled installer non-interactively. install.bat lives at $GcloudHome\install.bat.
        $bat = Join-Path $GcloudHome 'install.bat'
        & $bat --quiet --usage-reporting=false --command-completion=true --path-update=true
        # Make gcloud available in this PowerShell session.
        $env:Path = "$GcloudHome\bin;$env:Path"
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
    if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
        Die "gcloud install completed but 'gcloud' is still not on PATH. Open a new PowerShell window and re-run."
    }
    Ok "Installed gcloud: $(gcloud --version | Select-Object -First 1)"
}

function Install-Alpha {
    $state = (gcloud components list --filter="id:alpha" --format="value(state.name)" 2>$null)
    if ($state -match 'Installed') {
        Ok 'gcloud alpha component already installed'
        return
    }
    Say 'Installing gcloud alpha component'
    gcloud components install alpha --quiet
}

function Get-Source {
    Say "Downloading source to $InstallDir"
    if (-not (Test-Path $InstallParent)) {
        New-Item -ItemType Directory -Path $InstallParent | Out-Null
    }
    $tmp = Join-Path $env:TEMP ("gcpbulk-" + [System.Guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp 'src.zip'
        Invoke-WebRequest -Uri $ArchiveUrl -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $InstallParent -Force
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
    Ok "Source extracted to $InstallDir"
}

function Initialize-Venv {
    param([string]$PythonExe)
    Say 'Creating virtualenv and installing dependencies'
    Push-Location $InstallDir
    try {
        & $PythonExe -m venv .venv
        $venvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
        & $venvPython -m pip install --quiet --upgrade pip
        & $venvPython -m pip install --quiet -r requirements.txt
    } finally {
        Pop-Location
    }
    Ok "Dependencies installed in $InstallDir\.venv"
}

function Invoke-Auth {
    gcloud auth application-default print-access-token 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Ok 'Application Default Credentials already set'
        return
    }
    Say 'Launching gcloud auth (browser will open)'
    Write-Host @'

   When prompted, say YES to setting a quota project and pick a project you own.
   Without one, API calls fail with "User project specified in the request is invalid".

'@
    gcloud auth application-default login
}

function Show-NextSteps {
    Write-Host ''
    Write-Host 'v Install complete' -ForegroundColor Green
    Write-Host ''
    Write-Host 'To run the tool:'
    Write-Host ''
    Write-Host "  cd $InstallDir"
    Write-Host '  .\.venv\Scripts\Activate.ps1'
    Write-Host '  python -m gcp_appliance_status --org-id YOUR_ORG_ID'
    Write-Host ''
    Write-Host "If 'gcloud' is missing in a new shell, add $GcloudHome\bin to your user PATH or restart PowerShell."
    Write-Host ''
}

Say 'python-gcloud installer (Windows)'
$pythonExe = Resolve-Python
Install-Gcloud
Install-Alpha
Get-Source
Initialize-Venv -PythonExe $pythonExe
Invoke-Auth
Show-NextSteps
