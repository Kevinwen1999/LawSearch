<#
.SYNOPSIS
Start LawSearch: database, API, then the UI in your browser.

.DESCRIPTION
Ctrl+C stops the UI and API. The database container keeps running (docker compose stop to stop it).
API output goes to logs\api.log.

.EXAMPLE
.\run.ps1            # database + API + UI (http://localhost:8501)
.\run.ps1 -NoUi      # database + API only (http://localhost:8000/docs)
#>
param(
    [switch]$NoUi,
    [int]$ApiPort = 8000,
    [int]$UiPort = 8501
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
. .\scripts\common.ps1

foreach ($port in @($ApiPort) + $(if ($NoUi) { @() } else { @($UiPort) })) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use - is LawSearch already running?"
    }
}

Start-Database

New-Item -ItemType Directory -Force logs | Out-Null
Write-Host "Starting API on port $ApiPort (loading models, ~1 min)..."
$api = Start-Process $Python -ArgumentList "-m", "uvicorn", "app.main:app", "--port", $ApiPort `
    -NoNewWindow -PassThru -RedirectStandardOutput logs\api.out.log -RedirectStandardError logs\api.log

try {
    $deadline = (Get-Date).AddMinutes(5)
    while ($true) {
        if ($api.HasExited) { throw "API exited during startup; see logs\api.log" }
        try {
            Invoke-RestMethod "http://localhost:$ApiPort/health" -TimeoutSec 5 | Out-Null
            break
        } catch {
            if ((Get-Date) -gt $deadline) { throw "API did not answer within 5 minutes; see logs\api.log" }
            Start-Sleep -Seconds 2
        }
    }
    Write-Host "API ready: http://localhost:$ApiPort/docs"

    if ($NoUi) {
        Write-Host "Press Ctrl+C to stop."
        Wait-Process -Id $api.Id
    } else {
        $env:LAWSEARCH_API = "http://localhost:$ApiPort"
        & $Python -m streamlit run ui\streamlit_app.py --server.port $UiPort
    }
} finally {
    if (-not $api.HasExited) {
        Stop-Process -Id $api.Id -Force
        Write-Host "API stopped."
    }
}
