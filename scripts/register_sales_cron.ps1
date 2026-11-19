<#
.SYNOPSIS
    Register the sales report scheduled tasks as Windows scheduled tasks.

.DESCRIPTION
    Creates (or replaces) two tasks that deliver the furniture sales reports
    to Telegram:

        Sales-Daily-Telegram   daily 08:30 MSK
        Sales-Weekly-Digest    weekly, Monday 09:00 MSK

    Both call cmd wrappers under workspace\scripts. The wrappers pin the full
    path to python.exe: schtasks /TR 'python ...' does not search PATH and
    fails with 0x80070002 (file not found).

    The source of truth for the sales numbers is the sales_tools package in
    this repository (furniture_sales_database\sales_tools).

.PARAMETER DryRun
    Print the schtasks commands that would be created, without registering.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_sales_cron.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_sales_cron.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$TaskNameDaily = "Sales-Daily-Telegram",
    [string]$TaskNameWeekly = "Sales-Weekly-Digest",
    [string]$DailyCmd = "C:\Users\user\.openclaw\workspace\scripts\_sales_daily_task.cmd",
    [string]$WeeklyCmd = "C:\Users\user\.openclaw\workspace\scripts\_sales_weekly_task.cmd",
    [string]$DailyTime = "08:30",
    [string]$WeeklyTime = "09:00",
    [string]$TimeZone = "Russian Standard Time",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

foreach ($wrapper in @($DailyCmd, $WeeklyCmd)) {
    if (-not (Test-Path -LiteralPath $wrapper)) {
        Write-Error "Wrapper not found: $wrapper"
        exit 1
    }
}

function Register-Task {
    param(
        [string]$Name,
        [string]$Command,
        [string[]]$ScheduleArgs
    )

    $createArgs = @("/Create", "/TN", $Name, "/TR", $Command) + $ScheduleArgs + @("/RL", "LIMITED", "/F")
    $withTz = $createArgs + @("/TZ", $TimeZone)

    Write-Host "Registering: $Name"
    Write-Host "  Run  : $Command"
    Write-Host "  When : $($ScheduleArgs -join ' ') ($TimeZone / MSK)"

    if ($DryRun) {
        Write-Host "[DryRun] schtasks $($withTz -join ' ')"
        return
    }

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & schtasks @withTz 2>&1 | ForEach-Object { Write-Host $_ }
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $previous

    if ($exit -ne 0) {
        Write-Warning "schtasks with /TZ failed (exit $exit); retrying without /TZ"
        & schtasks @createArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to register '$Name' (exit $LASTEXITCODE)"
            exit $LASTEXITCODE
        }
    }
}

Register-Task -Name $TaskNameDaily `
    -Command "cmd /c `"$DailyCmd`"" `
    -ScheduleArgs @("/SC", "DAILY", "/ST", $DailyTime)

Register-Task -Name $TaskNameWeekly `
    -Command "cmd /c `"$WeeklyCmd`"" `
    -ScheduleArgs @("/SC", "WEEKLY", "/D", "MON", "/ST", $WeeklyTime)

if (-not $DryRun) {
    Write-Host "OK. Verify with:"
    Write-Host "  schtasks /Query /TN `"$TaskNameDaily`" /V /FO LIST"
    Write-Host "  schtasks /Query /TN `"$TaskNameWeekly`" /V /FO LIST"
}
exit 0
