# Shared helpers for run.ps1 and rebuild.ps1 (dot-sourced).

$Python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "No virtualenv at .venv - follow Setup in README.md first."
}

function Start-Database {
    docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed - is Docker Desktop running?" }

    Write-Host "Waiting for Postgres to be healthy..."
    $deadline = (Get-Date).AddMinutes(2)
    while ((docker inspect -f "{{.State.Health.Status}}" lawsearch-db) -ne "healthy") {
        if ((Get-Date) -gt $deadline) { throw "lawsearch-db is not healthy after 2 minutes; check: docker logs lawsearch-db" }
        Start-Sleep -Seconds 2
    }
}
