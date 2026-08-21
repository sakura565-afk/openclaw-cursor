<#
.SYNOPSIS
    Wrapper for the daily Ollama chain health smoke test.

.DESCRIPTION
    Invokes scripts/ollama_daily_smoke.py and redirects stdout/stderr into
    logs/ollama_health_YYYY-MM-DD.(out|err).log under the workspace root.
    Designed for Windows Task Scheduler (see register_ollama_health_cron.ps1).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\ollama_daily_smoke.ps1
#>
[CmdletBinding()]
param(
    [string]$WorkspaceRoot = "",
    [string]$PythonExe = "python",
    [switch]$NoAlert
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

$PyScript = Join-Path $ScriptDir "ollama_daily_smoke.py"
if (-not (Test-Path -LiteralPath $PyScript)) {
    $PyScript = Join-Path $WorkspaceRoot "scripts\ollama_daily_smoke.py"
}

$LogDir = Join-Path $WorkspaceRoot "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Stamp = [DateTime]::UtcNow.ToString("yyyy-MM-dd")
$OutLog = Join-Path $LogDir "ollama_health_$Stamp.out.log"
$ErrLog = Join-Path $LogDir "ollama_health_$Stamp.err.log"

$pyArgs = @($PyScript, "--root", $WorkspaceRoot)
if ($NoAlert) {
    $pyArgs += "--no-alert"
}

$timestamp = [DateTime]::UtcNow.ToString("o")
Add-Content -LiteralPath $OutLog -Value "`n==== $timestamp START ====`n"
Add-Content -LiteralPath $ErrLog -Value "`n==== $timestamp START ====`n"

$exitCode = 0
try {
    # Native redirection keeps stdout/stderr in separate daily log files.
    & $PythonExe @pyArgs 1>> $OutLog 2>> $ErrLog
    $exitCode = $LASTEXITCODE
}
catch {
    Add-Content -LiteralPath $ErrLog -Value $_.Exception.ToString()
    $exitCode = 1
}

if ($null -eq $exitCode) {
    $exitCode = 0
}

Add-Content -LiteralPath $OutLog -Value "`n==== END exit=$exitCode ====`n"
exit $exitCode
