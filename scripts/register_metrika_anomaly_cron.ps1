<#
.SYNOPSIS
    Register the daily Yandex Metrika anomaly detector as a Windows scheduled task.

.DESCRIPTION
    Creates (or replaces) task "Metrika-Anomaly-Daily" that runs every day at
    09:30 MSK via:

        powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\metrika_anomaly.ps1

    MSK is UTC+3. Scheduled after Metrika-Daily-Telegram (09:00 MSK) so cache
    data is fresh.

.PARAMETER DryRun
    Print the schtasks command that would be created, without registering.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_metrika_anomaly_cron.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_metrika_anomaly_cron.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$TaskName = "Metrika-Anomaly-Daily",
    [string]$WatcherPs1 = "C:\Users\user\.openclaw\workspace\scripts\metrika_anomaly.ps1",
    [string]$StartTime = "09:30",
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
