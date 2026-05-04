# go-binary prerequisites installer for Windows (PowerShell 5.1+).
#
# Installs Google Cloud SDK (if missing) and runs interactive auth so that
# gcp-appliance-status finds Application Default Credentials when launched.
#
# Does NOT download the gcp-appliance-status binary itself — fetch
# gcp-appliance-status-windows-amd64.exe from
# https://github.com/webgress/gcp_bulk_user/releases/latest separately.
#
# Usage (one-shot):
#   iwr -useb https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/go-binary/install.ps1 | iex
# or:
#   .\install.ps1
#
# If execution policy blocks the script:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# gcloud SDK on Windows is shipped as a self-contained zip — no .exe installer required.
$GcloudZipUrl = 'https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-windows-x86_64.zip'
$GcloudHome   = Join-Path $env:USERPROFILE 'google-cloud-sdk'
$ReleasePage  = 'https://github.com/webgress/gcp_bulk_user/releases/latest'

function Say { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok  { param($m) Write-Host "  v $m" -ForegroundColor Green }
function Die { param($m) Write-Host "  x $m" -ForegroundColor Red; exit 1 }

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
        $bat = Join-Path $GcloudHome 'install.bat'
        & $bat --quiet --usage-reporting=false --command-completion=true --path-update=true
        $env:Path = "$GcloudHome\bin;$env:Path"
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
    if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
        Die "gcloud install completed but 'gcloud' is still not on PATH. Open a new PowerShell window and re-run."
    }
    Ok "Installed gcloud: $(gcloud --version | Select-Object -First 1)"
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
    Write-Host 'v Prerequisites complete' -ForegroundColor Green
    Write-Host ''
    Write-Host 'Next:'
    Write-Host "  1. Download the Windows binary from:"
    Write-Host "       $ReleasePage"
    Write-Host "     File:  gcp-appliance-status-windows-amd64.exe"
    Write-Host '  2. Move it somewhere on your PATH (e.g. C:\Tools\) and rename if you like.'
    Write-Host '  3. Run:'
    Write-Host '       gcp-appliance-status-windows-amd64.exe --org-id YOUR_ORG_ID'
    Write-Host ''
    Write-Host "If 'gcloud' is missing in a new shell, add $GcloudHome\bin to your user PATH or restart PowerShell."
    Write-Host ''
}

Say 'go-binary prerequisites installer (Windows)'
Install-Gcloud
Invoke-Auth
Show-NextSteps
