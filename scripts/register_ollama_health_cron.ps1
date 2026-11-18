<#
.SYNOPSIS
    Register the daily Ollama health smoke as a Windows scheduled task.

.DESCRIPTION
    Creates (or replaces) task "Ollama-Health-Daily" that runs every day at
    -StartTime (06:00 MSK by default) via:

        powershell -ExecutionPolicy Bypass -File <SmokePs1>

    Both the wrapper path and the start time are parameters: by default the
    wrapper is taken from the same scripts/ directory as this registrar, so a
    fresh clone registers its own copy instead of a hardcoded host path.

    MSK is UTC+3. The task start time is applied in the Russian Standard Time
    zone when the host supports /TZ.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_ollama_health_cron.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_ollama_health_cron.ps1 -DryRun

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_ollama_health_cron.ps1 -SmokePs1 D:\repo\scripts\ollama_daily_smoke.ps1 -StartTime 04:30
#>
[CmdletBinding()]
param(
    [string]$TaskName = "Ollama-Health-Daily",
    # Пусто = обёртка рядом с этим скриптом. Так регистратор не привязан к
    # конкретной машине и работает из любого клона; конкретный путь можно
    # задать параметром.
    [string]$SmokePs1 = "",
    [string]$StartTime = "06:00",
    [string]$TimeZone = "Russian Standard Time",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $SmokePs1) {
    $SmokePs1 = Join-Path $ScriptDir "ollama_daily_smoke.ps1"
}

if (-not (Test-Path -LiteralPath $SmokePs1)) {
    Write-Warning "Smoke wrapper not found at: $SmokePs1 (task will still be registered)"
}

$tr = "powershell -ExecutionPolicy Bypass -File `"$SmokePs1`""

# Prefer schtasks with explicit timezone (Windows 8+/Server 2012+).
$createArgs = @(
    "/Create",
    "/TN", $TaskName,
    "/TR", $tr,
    "/SC", "DAILY",
    "/ST", $StartTime,
    "/RL", "LIMITED",
    "/F"
)

# /TZ is supported on modern Windows; ignore if the OS rejects it.
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
