<#
.SYNOPSIS
    Register the cron registry liveness check as a Windows scheduled task.

.DESCRIPTION
    Creates (or replaces) task "Cron-Registry-Check" that runs every 30 minutes
    via:

        powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\cron_registry.ps1

    The wrapper runs ``python -m scripts.cron_registry --send`` and keeps
    stdout/stderr in logs/cron_registry_YYYY-MM-DD.(out|err).log. A digest is
    sent to Telegram only when task health changed since the previous run.

.PARAMETER DryRun
    Print the schtasks command that would be created, without registering.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_cron_registry_cron.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_cron_registry_cron.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$TaskName = "Cron-Registry-Check",
    [string]$RegistryPs1 = "C:\Users\user\.openclaw\workspace\scripts\cron_registry.ps1",
    [int]$IntervalMinutes = 30,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $RegistryPs1)) {
    Write-Warning "Wrapper not found at: $RegistryPs1 (task will still be registered)"
}

$tr = "powershell -ExecutionPolicy Bypass -File `"$RegistryPs1`""

$createArgs = @(
    "/Create",
    "/TN", $TaskName,
    "/TR", $tr,
    "/SC", "MINUTE",
    "/MO", "$IntervalMinutes",
    "/RL", "LIMITED",
    "/F"
)

Write-Host "Registering scheduled task:"
Write-Host "  Name : $TaskName"
Write-Host "  When : every $IntervalMinutes minutes"
Write-Host "  Run  : $tr"
Write-Host "  Logs : logs\cron_registry_<date>.out.log / .err.log"

if ($DryRun) {
    Write-Host "[DryRun] schtasks $($createArgs -join ' ')"
    Write-Host "[DryRun] nothing was created."
    exit 0
}

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& schtasks @createArgs 2>&1 | ForEach-Object { Write-Host $_ }
$exit = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction

if ($exit -ne 0) {
    Write-Error "Failed to register task '$TaskName' (exit $exit)"
    exit $exit
}

Write-Host "OK: task '$TaskName' registered. Verify with:"
Write-Host "  schtasks /Query /TN `"$TaskName`" /V /FO LIST"
Write-Host "Optional: add '$TaskName' to data\cron_registry.json so it watches itself."
exit 0
