<#
.SYNOPSIS
    Wrapper for the daily Yandex Metrika anomaly detector.

.DESCRIPTION
    Sets PYTHONIOENCODING=utf-8, invokes ``python -m scripts.metrika_anomaly
    --send-alert --update-history``, and redirects stdout/stderr into
    logs/metrika_anomaly_YYYY-MM-DD.(out|err).log under the workspace root.
    Designed for Windows Task Scheduler (see register_metrika_anomaly_cron.ps1).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\metrika_anomaly.ps1
#>
[CmdletBinding()]
param(
    [string]$WorkspaceRoot = "",
    [string]$PythonExe = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $WorkspaceRoot) {
    $candidate = "C:\Users\user\.openclaw\workspace"
    if (Test-Path -LiteralPath $candidate) {
        $WorkspaceRoot = $candidate
    }
    else {
        $WorkspaceRoot = Split-Path -Parent $ScriptDir
    }
}

$LogDir = Join-Path $WorkspaceRoot "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Stamp = [DateTime]::UtcNow.ToString("yyyy-MM-dd")
$OutLog = Join-Path $LogDir "metrika_anomaly_$Stamp.out.log"
$ErrLog = Join-Path $LogDir "metrika_anomaly_$Stamp.err.log"

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$pyArgs = @(
    "-m", "scripts.metrika_anomaly",
    "--send-alert",
    "--update-history",
    "--root", $WorkspaceRoot
)

$timestamp = [DateTime]::UtcNow.ToString("o")
Add-Content -LiteralPath $OutLog -Value "`n==== $timestamp START ====`n"
Add-Content -LiteralPath $ErrLog -Value "`n==== $timestamp START ====`n"

$exitCode = 0
Push-Location $WorkspaceRoot
try {
    & $PythonExe @pyArgs 1>> $OutLog 2>> $ErrLog
    $exitCode = $LASTEXITCODE
}
catch {
    Add-Content -LiteralPath $ErrLog -Value $_.Exception.ToString()
    $exitCode = 1
}
finally {
    Pop-Location
}

if ($null -eq $exitCode) {
    $exitCode = 0
}

Add-Content -LiteralPath $OutLog -Value "`n==== END exit=$exitCode ====`n"
exit $exitCode
