# AtlasPM current-US-listed stock data expansion: research data ONLY.
# Runs existing stock-universe/stock-sync-market and the NYSE calendar audit.
# No AI retraining, quant execution, orders, or brokerage access.
#
# Example (PowerShell 5.1):
# powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\selection\Invoke-USMarketDataExpansion.ps1 -AsOf 2026-11-20 -MaxSymbols 50
#
# Re-run with -SkipUniverseRefresh -MaxSymbols 0 to attempt remaining tickers.
# End date is computed as as-of +1 day because yfinance uses exclusive end.

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string] $AsOf,

    [string] $Start = "2024-01-01",
    [int] $MaxSymbols = 50,
    [int] $Workers = 2,
    [int] $Attempts = 2,
    [switch] $SkipUniverseRefresh,

    [string] $Universe = "local_data\us_equity_universe.csv",
    [string] $UniverseReport = "local_data\us_equity_universe_report.json",
    [string] $CacheDir = "local_data\us_equity_cache",
    [string] $Prices = "local_data\us_equity_prices.csv",
    [string] $CoverageReport = "local_data\us_equity_coverage.json",
    [string] $AuditReport = "local_data\us_market_data_audit.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing .venv Python: $python"
}
if ($AsOf -cnotmatch '^\d{4}-\d{2}-\d{2}$') {
    throw "AsOf must be YYYY-MM-DD (completed actual NYSE session)"
}
if ($Start -cnotmatch '^\d{4}-\d{2}-\d{2}$') {
    throw "Start must be YYYY-MM-DD"
}
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$decision = [datetime]::ParseExact($AsOf, "yyyy-MM-dd", $culture)
$begin = [datetime]::ParseExact($Start, "yyyy-MM-dd", $culture)
if ($begin -gt $decision) { throw "Start cannot be after AsOf" }
if ($MaxSymbols -lt 0 -or $MaxSymbols -gt 100000) {
    throw "MaxSymbols must be in 0..100000"
}
if ($Workers -lt 1 -or $Workers -gt 8) { throw "Workers must be in 1..8" }
if ($Attempts -lt 1 -or $Attempts -gt 3) { throw "Attempts must be in 1..3" }
$endExclusive = $decision.AddDays(1).ToString("yyyy-MM-dd", $culture)

function RunAtlas {
    param([string[]] $Args)
    & $python -m atlaspm.cli @Args
    if ($LASTEXITCODE -ne 0) {
        throw ("AtlasPM failed with exit code {0}" -f $LASTEXITCODE)
    }
}

Push-Location $root
try {
    if (-not $SkipUniverseRefresh) {
        Write-Host "1/3 Discover current US-listed equity universe (not historical PIT membership)"
        RunAtlas @(
            "stock-universe", "--output", $Universe,
            "--report", $UniverseReport
        )
    } else {
        if (-not (Test-Path -LiteralPath $Universe -PathType Leaf)) {
            throw "Skipping universe refresh requires an existing manifest at $Universe"
        }
        Write-Host "1/3 Reusing existing current listing manifest"
    }

    Write-Host ("2/3 Sync quote history for up to {0} missing ticker(s); 0 means all" -f $MaxSymbols)
    RunAtlas @(
        "stock-sync-market",
        "--universe", $Universe,
        "--start", $Start, "--end", $endExclusive,
        "--max-symbols", "$MaxSymbols",
        "--workers", "$Workers", "--attempts", "$Attempts",
        "--cache-dir", $CacheDir,
        "--output", $Prices,
        "--report", $CoverageReport
    )

    Write-Host "3/3 Audit actual NYSE decision close, price history and market scope"
    RunAtlas @(
        "stock-market-audit",
        "--universe", $Universe,
        "--prices", $Prices,
        "--as-of", $AsOf,
        "--min-history-sessions", "253",
        "--recent-sessions", "63",
        "--min-recent-coverage-pct", "85",
        "--min-month-end-coverage-pct", "90",
        "--min-eligible-coverage-pct", "75",
        "--min-eligible-names", "500",
        "--output", $AuditReport
    )

    Write-Host "Stock data expansion research files saved inside local_data; no trading enabled."
    Write-Host "An audit marked REVIEW_REQUIRED means market data remains incomplete or unsafe for an all-US claim."
} finally {
    Pop-Location
}
