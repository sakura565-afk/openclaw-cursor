<#
.SYNOPSIS
    Register the daily HuggingFace trending watcher as a Windows scheduled task.

.DESCRIPTION
    Creates (or replaces) task "HF-Trending-Daily" that runs every day at
    08:00 MSK via:

        powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\hf_trending.ps1

    MSK is UTC+3. This script sets the task start time to 08:00 and applies
    the Russian Standard Time zone when the host supports /TZ.

.PARAMETER DryRun
    Print the schtasks command that would be created, without registering.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_hf_trending_cron.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_hf_trending_cron.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$TaskName = "HF-Trending-Daily",
    [string]$WatcherPs1 = "C:\Users\user\.openclaw\workspace\scripts\hf_trending.ps1",
    [string]$StartTime = "08:00",
    [string]$TimeZone = "Russian Standard Time",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $WatcherPs1)) {
    Write-Warning "Watcher wrapper not found at: $WatcherPs1 (task will still be registered)"
}

$tr = "powershell -ExecutionPolicy Bypass -File `"$WatcherPs1`""

$createArgs = @(
    "/Create",
    "/TN", $TaskName,
    "/TR", $tr,
    "/SC", "DAILY",
    "/ST", $StartTime,
    "/RL", "LIMITED",
    "/F"
)

$createArgsWithTz = $createArgs + @("/TZ", $TimeZone)

Write-Host "Registering scheduled task:"
Write-Host "  Name : $TaskName"
Write-Host "  When : daily at $StartTime ($TimeZone / MSK)"
Write-Host "  Run  : $tr"

if ($DryRun) {
    Write-Host "[DryRun] schtasks $($createArgsWithTz -join ' ')"
    exit 0
}

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& schtasks @createArgsWithTz 2>&1 | ForEach-Object { Write-Host $_ }
$exit = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction

if ($exit -ne 0) {
    Write-Warning "schtasks with /TZ failed (exit $exit); retrying without /TZ"
    & schtasks @createArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to register task '$TaskName' (exit $LASTEXITCODE)"
        exit $LASTEXITCODE
    }
}

Write-Host "OK: task '$TaskName' registered. Verify with:"
Write-Host "  schtasks /Query /TN `"$TaskName`" /V /FO LIST"
exit 0
