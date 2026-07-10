<#
.SYNOPSIS
    Plain-assert (no Pester) tests for scripts/backup_custom_nodes.ps1.

.DESCRIPTION
    Three test cases:
      1. Creates a backup directory matching the _backup_<timestamp> pattern.
      2. Mirrors custom_nodes/ contents (recursive file count matches).
      3. Prunes to keep only the N most-recent backups.

    Prints "all tests passed" and exits 0 on success, or exits 1 on failure.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:Passed = 0
$script:Failed = 0
$BackupScript = Join-Path $PSScriptRoot "..\scripts\backup_custom_nodes.ps1"

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) {
        $script:Passed++
        Write-Host "  [PASS] $Message"
    }
    else {
        $script:Failed++
        Write-Host "  [FAIL] $Message" -ForegroundColor Red
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Message)
    Assert-True ($Expected -eq $Actual) "$Message (expected=$Expected, actual=$Actual)"
}

function New-TempWorkspace {
    $root = Join-Path ([System.IO.Path]::GetTempPath()) ("wfvcs_" + [System.Guid]::NewGuid().ToString("N"))
    $comfy = Join-Path $root "ComfyUI"
    $customNodes = Join-Path $comfy "custom_nodes"
    $backupRoot = Join-Path $root "comfyui"
    New-Item -ItemType Directory -Path $customNodes -Force | Out-Null
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null

    # Seed representative custom nodes.
    New-Item -ItemType Directory -Path (Join-Path $customNodes "rgthree-comfy") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $customNodes "ComfyUI-Impact-Pack\modules") -Force | Out-Null
    Set-Content -Path (Join-Path $customNodes "rgthree-comfy\__init__.py") -Value "# rgthree"
    Set-Content -Path (Join-Path $customNodes "ComfyUI-Impact-Pack\__init__.py") -Value "# impact"
    Set-Content -Path (Join-Path $customNodes "ComfyUI-Impact-Pack\modules\core.py") -Value "core = 1"
    Set-Content -Path (Join-Path $customNodes "README.md") -Value "# custom nodes"

    return [pscustomobject]@{
        Root        = $root
        ComfyRoot   = $comfy
        CustomNodes = $customNodes
        BackupRoot  = $backupRoot
    }
}

function Get-FileCount {
    param([string]$Path)
    return @(Get-ChildItem -LiteralPath $Path -Recurse -File).Count
}

function Get-BackupDirs {
    param([string]$BackupRoot)
    return @(Get-ChildItem -LiteralPath $BackupRoot -Directory |
        Where-Object { $_.Name -like "_backup_*" } |
        Sort-Object Name)
}

# --- Test 1: backup directory name pattern ------------------------------
Write-Host "Test 1: creates backup directory with expected name pattern"
$ws = New-TempWorkspace
try {
    & $BackupScript -ComfyUIRoot $ws.ComfyRoot -BackupRoot $ws.BackupRoot -KeepCount 5 | Out-Null
    Assert-Equal 0 $LASTEXITCODE "script exits 0"
    $backups = @(Get-BackupDirs -BackupRoot $ws.BackupRoot)
    Assert-Equal 1 $backups.Count "exactly one backup created"
    Assert-True ($backups[0].Name -match '^_backup_\d{8}_\d{6}$') "name matches _backup_<yyyyMMdd_HHmmss>"
}
finally {
    Remove-Item -LiteralPath $ws.Root -Recurse -Force -ErrorAction SilentlyContinue
}

# --- Test 2: mirrors custom_nodes contents ------------------------------
Write-Host "Test 2: mirrors custom_nodes/ contents (file count)"
$ws = New-TempWorkspace
try {
    & $BackupScript -ComfyUIRoot $ws.ComfyRoot -BackupRoot $ws.BackupRoot -KeepCount 5 | Out-Null
    $backups = @(Get-BackupDirs -BackupRoot $ws.BackupRoot)
    $mirror = Join-Path $backups[0].FullName "custom_nodes"
    $srcCount = Get-FileCount -Path $ws.CustomNodes
    $dstCount = Get-FileCount -Path $mirror
    Assert-Equal $srcCount $dstCount "mirrored file count matches source"
    Assert-True (Test-Path (Join-Path $mirror "ComfyUI-Impact-Pack\modules\core.py")) "nested file mirrored"
}
finally {
    Remove-Item -LiteralPath $ws.Root -Recurse -Force -ErrorAction SilentlyContinue
}

# --- Test 3: prunes to keep N most-recent backups -----------------------
Write-Host "Test 3: prunes to keep the N most-recent backups"
$ws = New-TempWorkspace
try {
    # Pre-seed 6 older backups (timestamp-sorted names).
    foreach ($d in @("20200101_000001", "20200102_000001", "20200103_000001",
                     "20200104_000001", "20200105_000001", "20200106_000001")) {
        $old = Join-Path $ws.BackupRoot "_backup_$d\custom_nodes"
        New-Item -ItemType Directory -Path $old -Force | Out-Null
        Set-Content -Path (Join-Path $old "placeholder.txt") -Value "old"
    }
    & $BackupScript -ComfyUIRoot $ws.ComfyRoot -BackupRoot $ws.BackupRoot -KeepCount 5 | Out-Null
    $backups = @(Get-BackupDirs -BackupRoot $ws.BackupRoot)
    Assert-Equal 5 $backups.Count "retains exactly KeepCount backups"
    Assert-True (-not (Test-Path (Join-Path $ws.BackupRoot "_backup_20200101_000001"))) "oldest backup pruned"
    Assert-True (Test-Path (Join-Path $ws.BackupRoot "_backup_20200106_000001")) "recent pre-seeded backup retained"
}
finally {
    Remove-Item -LiteralPath $ws.Root -Recurse -Force -ErrorAction SilentlyContinue
}

# --- Summary ------------------------------------------------------------
Write-Host ""
Write-Host "Results: $script:Passed passed, $script:Failed failed"
if ($script:Failed -gt 0) {
    Write-Host "TESTS FAILED" -ForegroundColor Red
    exit 1
}
Write-Host "all tests passed"
exit 0
