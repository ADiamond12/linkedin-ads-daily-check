param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Assert-NativeSuccess {
    param([string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

$outputDir = if ($env:LINKEDIN_ADS_OUTPUT_DIR) {
    $env:LINKEDIN_ADS_OUTPUT_DIR
} else {
    "output"
}

$sampleOutputDir = if ($env:LINKEDIN_ADS_SAMPLE_OUTPUT_DIR) {
    $env:LINKEDIN_ADS_SAMPLE_OUTPUT_DIR
} else {
    "docs\sample-output"
}

if (-not $SkipTests) {
    python -m unittest discover -s .\tests -v
    Assert-NativeSuccess "Run unittest"
}

Write-Host ""
Write-Host "Generating deterministic LinkedIn Ads daily check from the committed fixture"
python .\linkedin_ads_monitor.py --config .\config.example.json --output-dir $outputDir
Assert-NativeSuccess "Generate daily check report"

New-Item -ItemType Directory -Force -Path $sampleOutputDir | Out-Null
Copy-Item -Path (Join-Path $outputDir "latest_report.html") -Destination (Join-Path $sampleOutputDir "daily-check-sample-report.html") -Force

Write-Host ""
Write-Host "Open this report first:"
Write-Host (Join-Path $sampleOutputDir "daily-check-sample-report.html")
Write-Host ""
Write-Host "Generated local artifacts:"
Write-Host (Join-Path $outputDir "latest_report.html")
Write-Host (Join-Path $outputDir "latest_report.json")
Write-Host (Join-Path $outputDir "latest_summary.md")
