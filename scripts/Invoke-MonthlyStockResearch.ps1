# AtlasPM monthly candidate report for Windows PowerShell 5.1.
# This script only calls a stock ranking CLI, NEVER a broker or trade planner.
#
# Defaults to the PREVIOUS calendar month; intended for a local scheduled
# run on day 1 of the following month, after month-end market data are loaded.
#
# Usage:
# powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Invoke-MonthlyStockResearch.ps1 -Month 2026-10
#
# No automatic quote updates or retraining are done by this script.

[CmdletBinding()]
param(
    [string] $Month = "",
    [string] $Prices = "local_data\us_stocks_2000_2026.csv",
    [string] $BenchmarkPrices = "local_data\market_2000_2026.csv",
    [string] $Model = "local_data\atlas_stock_ranker_v2.txt",
    [string] $Universe = "",
    [int] $MinScored = 20,
    [double] $MinMarketCoveragePct = 90,
    [int] $MaxTrainingAgeDays = 365,
    [string] $OutputDir = "local_data\monthly_stock_research",
    [switch] $Overwrite
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "AtlasPM Python venv missing: $pythonPath"
}
if (-not $Month) {
    $Month = (Get-Date).AddMonths(-1).ToString("yyyy-MM")
}
if ($Month -notmatch '^20[0-9]{2}-(0[1-9]|1[0-2])$') {
    throw "Month must be YYYY-MM (2000..2099)"
}

Push-Location $projectRoot
try {
    $argv = @(
        "-m", "atlaspm.cli", "stock-ai-monthly",
        "--prices", $Prices,
        "--benchmark-prices", $BenchmarkPrices,
        "--model", $Model,
        "--month", $Month,
        "--min-scored", "$MinScored",
        "--min-market-coverage-pct", "$MinMarketCoveragePct",
        "--max-training-age-days", "$MaxTrainingAgeDays",
        "--output-dir", $OutputDir
    )
    if ($Universe) {
        $argv += @("--universe", $Universe)
    }
    if ($Overwrite) {
        $argv += "--overwrite"
    }
    Write-Host ("AtlasPM monthly research for {0}: no execution, stock ranking only." -f $Month)
    & $pythonPath @argv
    if ($LASTEXITCODE -ne 0) {
        throw ("AtlasPM monthly research failed with exit code {0}" -f $LASTEXITCODE)
    }
} finally {
    Pop-Location
}
