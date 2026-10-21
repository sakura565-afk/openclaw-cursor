<#
.SYNOPSIS
    Wrapper for the cron registry liveness check.

.DESCRIPTION
    Sets PYTHONIOENCODING=utf-8, invokes ``python -m scripts.cron_registry
    --send`` from the repository root, and redirects stdout/stderr into
    logs/cron_registry_YYYY-MM-DD.(out|err).log under the workspace root.
    Designed for Windows Task Scheduler (see register_cron_registry_cron.ps1).

    The workspace root defaults to the parent directory of this script, so the
    wrapper works from whichever checkout the scheduled task points at.

.PARAMETER NoSend
    Run the check without sending the Telegram digest (useful for a manual run).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\cron_registry.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\cron_registry.ps1 -NoSend
#>
[CmdletBinding()]
param(
    [string]$WorkspaceRoot = "",
    [string]$PythonExe = "python",
    [switch]$NoSend
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $WorkspaceRoot) {
    $WorkspaceRoot = Split-Path -Parent $ScriptDir
}

$LogDir = Join-Path $WorkspaceRoot "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Stamp = [DateTime]::UtcNow.ToString("yyyy-MM-dd")
$OutLog = Join-Path $LogDir "cron_registry_$Stamp.out.log"
$ErrLog = Join-Path $LogDir "cron_registry_$Stamp.err.log"

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$pyArgs = @("-m", "scripts.cron_registry", "--root", $WorkspaceRoot)
if ($NoSend) {
    $pyArgs += "--no-send"
}
else {
    $pyArgs += "--send"
}

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

# exit=0 all good, exit=1 stale/failing task, exit=2 registry itself is broken.
Add-Content -LiteralPath $OutLog -Value "`n==== END exit=$exitCode ====`n"
exit $exitCode
