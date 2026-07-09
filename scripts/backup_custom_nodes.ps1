<#
.SYNOPSIS
    Create timestamped, mirrored backups of a ComfyUI custom_nodes directory.

.DESCRIPTION
    Uses robocopy /MIR to mirror <ComfyUIRoot>\custom_nodes into
    <BackupRoot>\_backup_<yyyyMMdd_HHmmss>\custom_nodes, then prunes older
    backups so only the newest -KeepCount remain. Backups sort by name, which
    is timestamp-ordered. The script is non-interactive and cron/Task-Scheduler
    friendly: it never prompts and always exits with a status code.

.PARAMETER ComfyUIRoot
    Path to the ComfyUI install root containing custom_nodes.

.PARAMETER BackupRoot
    Directory under which _backup_<timestamp> folders are created.

.PARAMETER KeepCount
    Number of most-recent backups to retain (older ones are removed).

.PARAMETER DryRun
    Print the planned actions without copying or deleting anything.

.EXAMPLE
    powershell scripts\backup_custom_nodes.ps1 -DryRun

.EXAMPLE
    powershell scripts\backup_custom_nodes.ps1
#>
[CmdletBinding()]
param(
    [string]$ComfyUIRoot = "C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI",
    [string]$BackupRoot = "C:\Users\user\comfyui",
    [int]$KeepCount = 5,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-DirectorySizeMB {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    $bytes = (Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if (-not $bytes) { return 0 }
    return [math]::Round($bytes / 1MB, 2)
}

# 1. Validate source.
$source = Join-Path $ComfyUIRoot "custom_nodes"
if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    Write-Error "custom_nodes not found at: $source"
    exit 1
}

# 2. Timestamp + 3. backup target.
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = Join-Path $BackupRoot "_backup_$timestamp"
$target = Join-Path $backupDir "custom_nodes"

if ($DryRun) {
    Write-Host "[DryRun] Would mirror:"
    Write-Host "  from : $source"
    Write-Host "  to   : $target"
    Write-Host "[DryRun] Would run: robocopy `"$source`" `"$target`" /MIR /R:3 /W:5 /NP /NFL /NDL"
}
else {
    New-Item -ItemType Directory -Path $target -Force | Out-Null

    # 4. robocopy mirror. Exit codes 0-7 are success (8+ are failures).
    $roboArgs = @($source, $target, "/MIR", "/R:3", "/W:5", "/NP", "/NFL", "/NDL")
    & robocopy @roboArgs | Out-Null
    $roboExit = $LASTEXITCODE

    if ($roboExit -ge 8) {
        Write-Error "robocopy failed with exit code $roboExit"
        exit 1
    }
}

# 5. Retention: keep newest $KeepCount, prune older. Names are timestamp-sorted.
$allBackups = @(Get-ChildItem -LiteralPath $BackupRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "_backup_*" } |
    Sort-Object Name)

$retained = $allBackups
if ($allBackups.Count -gt $KeepCount) {
    $removeCount = $allBackups.Count - $KeepCount
    $toRemove = $allBackups | Select-Object -First $removeCount
    $retained = @($allBackups | Select-Object -Last $KeepCount)
    foreach ($old in $toRemove) {
        if ($DryRun) {
            Write-Host "[DryRun] Would remove old backup: $($old.FullName)"
        }
        else {
            Remove-Item -LiteralPath $old.FullName -Recurse -Force
        }
    }
}

# 6. Summary.
if ($DryRun) {
    $projected = if ($allBackups.Count -lt $KeepCount) { $allBackups.Count + 1 } else { $KeepCount }
    Write-Host "[DryRun] Backup that would be created: $backupDir"
    Write-Host "[DryRun] Total backups that would be retained: $projected"
}
else {
    $sizeMB = Get-DirectorySizeMB -Path $target
    Write-Host "Backup created: $backupDir | Size: $sizeMB MB | Total backups retained: $($retained.Count)"
}

exit 0
