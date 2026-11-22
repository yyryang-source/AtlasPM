# Backwards-compatible Windows entrypoint for the stock research module.
# Canonical implementation lives in scripts/selection/.
& (Join-Path $PSScriptRoot "selection\Invoke-MonthlyStockResearch.ps1") @args
exit $LASTEXITCODE
