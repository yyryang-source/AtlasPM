[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[A-Za-z0-9 _-]{1,60}$')]
    [string] $TaskName,

    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[A-Z0-9][A-Z0-9.\-]{0,14}$')]
    [string[]] $Symbols,

    [ValidateSet('daily', 'month-end')]
    [string] $Schedule = 'daily',

    [ValidateRange(0, 240)]
    [int] $DelayMinutes = 20,

    [ValidateRange(60, 3600)]
    [int] $PollSeconds = 900,

    [string] $Directory = 'local_data\market_close',

    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')]
    [string] $DraftAccount,

    [switch] $DedicatedCashEquities,

    [ValidateSet('equal', 'quant')]
    [string] $Allocation = 'equal',

    [string] $GatewayCaFile,

    [switch] $DefinitionOnly,

    [switch] $ExportStartupShortcut,

    [switch] $StartNow
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing virtual environment: $python"
}
if (($DefinitionOnly -and $ExportStartupShortcut) -or
    ($StartNow -and ($DefinitionOnly -or $ExportStartupShortcut))) {
    throw 'Choose task registration, -DefinitionOnly, or -ExportStartupShortcut.'
}
if (-not $DefinitionOnly -and -not $ExportStartupShortcut -and
    (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw "A scheduled task already uses the name: $TaskName"
}
$localData = [IO.Path]::GetFullPath((Join-Path $root 'local_data')).TrimEnd('\') + '\'
$marketDirectory = if ([IO.Path]::IsPathRooted($Directory)) { $Directory } else { Join-Path $root $Directory }
$marketDirectory = [IO.Path]::GetFullPath($marketDirectory).TrimEnd('\')
if (-not $marketDirectory.StartsWith($localData, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The close data directory must be inside the repository local_data folder.'
}
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$argument = "-m atlaspm.market_close --watch --schedule $Schedule --delay-minutes $DelayMinutes --poll-seconds $PollSeconds"
$argument += ' --directory "' + $marketDirectory + '" --symbols ' + ($Symbols -join ' ')
if ($DraftAccount) {
    if (-not $DedicatedCashEquities) {
        throw 'Automatic draft collection requires -DedicatedCashEquities.'
    }
    $argument += " --draft-account $DraftAccount --dedicated-cash-equities --allocation $Allocation"
    if ($GatewayCaFile) {
        $ca = (Resolve-Path -LiteralPath $GatewayCaFile).Path
        $argument += ' --gateway-ca-file "' + $ca + '"'
    }
}
if ($ExportStartupShortcut) {
    $backgroundPython = Join-Path $root '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $backgroundPython -PathType Leaf)) {
        throw "Missing Windows background interpreter: $backgroundPython"
    }
    $null = [IO.Directory]::CreateDirectory($marketDirectory)
    $linkPath = Join-Path $marketDirectory ($TaskName + '.lnk')
    if (Test-Path -LiteralPath $linkPath) {
        throw "A startup shortcut already exists: $linkPath"
    }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($linkPath)
    $link.TargetPath = $backgroundPython
    $link.Arguments = $argument
    $link.WorkingDirectory = $root
    $link.Description = 'AtlasPM NYSE market-close watcher'
    $link.Save()
    return Get-Item -LiteralPath $linkPath
}
$action = New-ScheduledTaskAction -Execute $python -Argument $argument -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([timespan]::Zero) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval ([timespan]::FromMinutes(2))
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
if ($DefinitionOnly) {
    return $definition
}
Register-ScheduledTask -TaskName $TaskName -InputObject $definition
if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
}
