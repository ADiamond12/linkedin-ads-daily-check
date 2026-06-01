param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $SkipTests) {
    python -m unittest discover -s .\tests -v
}

Write-Host ""
Write-Host "Generating deterministic LinkedIn Ads daily check from the committed fixture"
python .\linkedin_ads_monitor.py --config .\config.example.json

New-Item -ItemType Directory -Force -Path "docs\sample-output" | Out-Null
Copy-Item -Path "output\latest_report.html" -Destination "docs\sample-output\daily-check-sample-report.html" -Force

Write-Host ""
Write-Host "Open this report first:"
Write-Host "docs/sample-output/daily-check-sample-report.html"
Write-Host ""
Write-Host "Generated local artifacts:"
Write-Host "output/latest_report.html"
Write-Host "output/latest_report.json"
Write-Host "output/latest_summary.md"
