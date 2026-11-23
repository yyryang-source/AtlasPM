# AtlasPM all-symbol historical HF OHLCV-1m archive backfill on the user's PC.
# Each source month contains all symbols published for that month.
# One month per Python process; resumes from completed monthly Parquet + QC audits.
# No broker access, training, secrets, or market-data uploads.
#
# Request window default: 2000-01 through 2026-11. This HF publisher currently
# only provides files through 2026-03. The later 2026 months are NOT fabricated.
#
# From Windows PowerShell:
#   & .\scripts\data\Invoke-HFAllMarketBackfill.ps1 -DryRun
#   & .\scripts\data\Invoke-HFAllMarketBackfill.ps1 -AcknowledgeRights
#
# Licensing: public hosting does not grant the Finnhub source-data rights.
[CmdletBinding()]
param(
    [string] $StartMonth = "2000-01",
    [string] $EndMonth = "2026-11",
    [ValidateSet("rth","all")]
    [string] $Session = "rth",
    [int] $MaxMonths = 0,
    [switch] $DryRun,
    [switch] $AcknowledgeRights,
    [switch] $RemoveRaw,
    [switch] $AuditEachMonth,
    [switch] $StopOnError
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$format = "yyyy-MM"
$culture = [System.Globalization.CultureInfo]::InvariantCulture

function ParseMonth([string] $value) {
    if ($value -cnotmatch '^\d{4}-(0[1-9]|1[0-2])$') {
        throw "Month must be YYYY-MM: $value"
    }
    return [datetime]::ParseExact($value, $format, $culture)
}

$first = ParseMonth $StartMonth
$requestedLast = ParseMonth $EndMonth
$providerFirst = ParseMonth "1992-01"
$providerLast = ParseMonth "2026-03"
if ($first -gt $requestedLast) { throw "StartMonth must not be later than EndMonth" }
if ($MaxMonths -lt 0) { throw "MaxMonths must be >= 0 (0 means all)" }

$effectiveFirst = if ($first -gt $providerFirst) { $first } else { $providerFirst }
$effectiveLast = if ($requestedLast -lt $providerLast) { $requestedLast } else { $providerLast }
$months = New-Object 'System.Collections.Generic.List[string]'
if ($effectiveFirst -le $effectiveLast) {
    for ($d = $effectiveFirst; $d -le $effectiveLast; $d = $d.AddMonths(1)) {
        $months.Add($d.ToString($format, $culture))
        if ($MaxMonths -gt 0 -and $months.Count -ge $MaxMonths) { break }
    }
}
$missingAfter = @()
if ($requestedLast -gt $providerLast) {
    $gapStart = $providerLast.AddMonths(1)
    $from = if ($first -gt $gapStart) { $first } else { $gapStart }
    for ($d = $from; $d -le $requestedLast; $d = $d.AddMonths(1)) {
        $missingAfter += $d.ToString($format, $culture)
    }
}
$missingBefore = @()
if ($first -lt $providerFirst) {
    $stop = if ($requestedLast -lt $providerFirst) { $requestedLast } else { $providerFirst.AddMonths(-1) }
    for ($d = $first; $d -le $stop; $d = $d.AddMonths(1)) {
        $missingBefore += $d.ToString($format, $culture)
    }
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$summary = [ordered]@{
    schema = "atlaspm_hf_all_market_bulk_v1"
    provider = "mito0o852/OHLCV-1m"
    requested_start_month = $StartMonth
    requested_end_month = $EndMonth
    advertised_archive_start = "1992-01"
    advertised_archive_end = "2026-03"
    effective_start_month = if ($months.Count) { $months[0] } else { $null }
    effective_end_month = if ($months.Count) { $months[$months.Count-1] } else { $null }
    months_selected = $months.Count
    unavailable_requested_months = @($missingBefore) + @($missingAfter)
    includes_all_source_tickers_per_month = $true
    historical_universe_complete = $false
    live_execution_enabled = $false
    research_only = $true
}
if ($DryRun) {
    $summary["mode"] = "dry_run"
    $summary["network_downloads"] = 0
    $summary | ConvertTo-Json -Depth 6
    return
}
if (-not $AcknowledgeRights) {
    throw "Verify Finnhub-derived data usage rights, then specify -AcknowledgeRights. Public hosting is NOT an open-data license."
}
if (-not $months.Count) { throw "No files in the provider's advertised date range" }
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing Windows .venv Python: $python"
}
$logdir = Join-Path $root "local_data\stock_5m\huggingface\bulk"
New-Item -ItemType Directory -Path $logdir -Force | Out-Null
$logfile = Join-Path $logdir "progress.jsonl"
$summaryfile = Join-Path $logdir "latest_summary.json"
$completed = 0
$skipped = 0
$failed = New-Object 'System.Collections.Generic.List[string]'
$reported = 0
Write-Host ("HF full-market archive: {0} to {1} ({2} month files)" -f $summary.effective_start_month, $summary.effective_end_month, $months.Count)
Write-Warning "The dataset stops at 2026-03. Requested later months will remain explicitly unavailable."
Write-Warning "No published full-US point-in-time universe, corporate action certification or rights verification is implied."
Write-Warning "KeepRaw is the default: the original 1m files and the new 5m files both require substantial local disk space."
function RunLocalAudit([string] $month) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $python -m atlaspm.cli market-db hf-5m-audit --month $month --session $Session | Out-Host
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

Push-Location $root
try {
    foreach ($month in $months) {
        $reported++
        $year,$mm = $month.Split("-")
        $out = Join-Path $root ("local_data\stock_5m\huggingface\parquet\session={0}\year={1}\month={2}\ohlcv_{3}.parquet" -f $Session,$year,$mm,$month)
        $qc = Join-Path $root ("local_data\stock_5m\huggingface\quality\session={0}\year={1}\month={2}\ohlcv_{3}.quarantine.json" -f $Session,$year,$mm,$month)
        $existing = ((Test-Path -LiteralPath $out -PathType Leaf) -and (Test-Path -LiteralPath $qc -PathType Leaf))
        if ($existing) {
            $f = Get-Item -LiteralPath $out
            if ($f.Length -gt 100) {
                Write-Host ("[{0}/{1}] {2} already converted and QC metadata exists: skip" -f $reported,$months.Count,$month)
                if ($AuditEachMonth) {
                    $auditExit = RunLocalAudit $month
                    if ($auditExit -ne 0) {
                        $failed.Add($month)
                        Write-Warning ("Local audit failed for preexisting {0}" -f $month)
                        if ($StopOnError) { break }
                        continue
                    }
                }
                $skipped++
                continue
            }
        }
        $driveRoot = [System.IO.Path]::GetPathRoot($root)
        $freeGiB = [math]::Round(([System.IO.DriveInfo]::new($driveRoot).AvailableFreeSpace / 1GB),2)
        if ($freeGiB -lt 5) {
            Write-Warning ("Remaining free disk {0} GiB is below 5 GiB; stopping to prevent a partial file" -f $freeGiB)
            $failed.Add($month)
            break
        }
        Write-Host ("[{0}/{1}] Processing all tickers in {2}; free disk {3} GiB" -f $reported,$months.Count,$month,$freeGiB)
        $cmdargs = @("-m","atlaspm.cli","market-db","hf-5m-backfill",
                     "--start-month",$month,"--end-month",$month,
                     "--max-months","1","--session",$Session,
                     "--acknowledge-rights")
        if ($RemoveRaw) { $cmdargs += "--remove-raw" }
        $previousErrorPreference = $ErrorActionPreference
        $code = 1
        try {
            # Preserve progress and stderr from the native process; no capture
            # of private price rows and no GitHub/local-data uploads.
            $ErrorActionPreference = "Continue"
            & $python @cmdargs
            $code = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorPreference
        }
        $qcOk = (Test-Path -LiteralPath $qc -PathType Leaf)
        $outOk = (Test-Path -LiteralPath $out -PathType Leaf)
        $entry = [ordered]@{
            month = $month
            timestamp_utc = [datetime]::UtcNow.ToString("o")
            state = if ($code -eq 0 -and $qcOk -and $outOk) { "converted" } else { "failed_or_qc_missing" }
            exit_code = $code
        }
        ($entry | ConvertTo-Json -Compress) | Add-Content -LiteralPath $logfile -Encoding UTF8
        if ($entry.state -eq "converted") {
            if ($AuditEachMonth) {
                $auditExit = RunLocalAudit $month
                if ($auditExit -ne 0) {
                    $entry.state = "converted_audit_failed"
                    $failed.Add($month)
                    Write-Warning ("Month {0} converted but its local audit could not complete" -f $month)
                    if ($StopOnError) { break }
                    continue
                }
            }
            $completed++
        } else {
            $failed.Add($month)
            Write-Warning ("Month {0} failed or lacks QC. Raw file is preserved when conversion fails." -f $month)
            if ($StopOnError) { break }
        }
    }
} finally {
    Pop-Location
    $summary["mode"] = "backfill"
    $summary["converted_in_this_run"] = $completed
    $summary["previously_converted_skipped"] = $skipped
    $summary["failed_months"] = @($failed.ToArray())
    $summary["months_attempted_or_skipped"] = $reported
    $summary["progress_log"] = $logfile
    $summary["local_summary"] = $summaryfile
    $summary["all_requested_months_available"] = ($summary.unavailable_requested_months.Count -eq 0)
    $tmp = $summaryfile + ".part"
    try {
        ($summary | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $tmp -Encoding UTF8
        Move-Item -LiteralPath $tmp -Destination $summaryfile -Force
    } finally {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
    }
    $summary | ConvertTo-Json -Depth 6
}
if ($failed.Count) {
    throw ("Bulk backfill incomplete: {0} month(s) failed. Rerun same command to resume from completed months." -f $failed.Count)
}
