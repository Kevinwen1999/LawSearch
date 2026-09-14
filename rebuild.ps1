<#
.SYNOPSIS
Rebuild all LawSearch data from the public sources, in dependency order.

.DESCRIPTION
Steps (in order):
  migrate         apply SQL migrations
  cases           A2AJ federal decisions -> chunks + embeddings + indexes (~5 h on an RTX 3090; resumes)
  citations       case->case citation graph (~30 s)
  legislation     Justice Laws acts, regulations + Constitution -> sections (clears statute links)
  statute-links   case->statute references from decision text (~13 min)
  briefs          re-verify cached FILAC briefs against the new sections (no model calls)

Every step is safe to re-run. After "legislation", always run "statute-links" too.

.EXAMPLE
.\rebuild.ps1                                    # everything
.\rebuild.ps1 -From legislation                  # legislation, statute-links, briefs
.\rebuild.ps1 -From citations -To citations      # one step
.\rebuild.ps1 -From cases -Yes                   # no confirmation prompt
#>
param(
    [ValidateSet("migrate", "cases", "citations", "legislation", "statute-links", "briefs")]
    [string]$From = "migrate",
    [ValidateSet("migrate", "cases", "citations", "legislation", "statute-links", "briefs")]
    [string]$To = "briefs",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
. .\scripts\common.ps1

$steps = [ordered]@{
    "migrate"       = @("scripts.migrate")
    "cases"         = @("scripts.ingest_a2aj", "--federal")
    "citations"     = @("scripts.load_citations")
    "legislation"   = @("scripts.ingest_legislation")
    "statute-links" = @("scripts.link_statutes")
    "briefs"        = @("scripts.filac_cli", "--reverify-all")
}
$names = @($steps.Keys)
if ($names.IndexOf($From) -gt $names.IndexOf($To)) { throw "-From $From comes after -To $To" }
$selected = $names[$names.IndexOf($From)..$names.IndexOf($To)]

Write-Host "Steps to run: $($selected -join ', ')"
if ($selected -contains "legislation" -and $selected -notcontains "statute-links") {
    Write-Warning "legislation clears case->statute links; run statute-links afterwards."
}
if (-not $Yes) {
    $answer = Read-Host "Continue? [y/N]"
    if ($answer -notmatch "^[yY]") { return }
}

Start-Database

$total = [Diagnostics.Stopwatch]::StartNew()
foreach ($name in $selected) {
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    $step = [Diagnostics.Stopwatch]::StartNew()
    & $Python -m @($steps[$name])
    if ($LASTEXITCODE -ne 0) {
        throw "Step '$name' failed (exit $LASTEXITCODE). Fix it, then resume with: .\rebuild.ps1 -From $name"
    }
    Write-Host ("=== {0} done in {1:hh\:mm\:ss} ===" -f $name, $step.Elapsed) -ForegroundColor Cyan
}
Write-Host ("`nRebuild finished in {0:hh\:mm\:ss}" -f $total.Elapsed) -ForegroundColor Green
