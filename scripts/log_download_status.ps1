<#
.SYNOPSIS
    Check download status of ComfyUI model files.

.DESCRIPTION
    Verifies that required ComfyUI model files exist and reports their sizes.
    Used by the 'Undress pipeline — статус качалок' cron task (hourly).

    Expected models:
      - Pony Diffusion v6 XL
      - XLabs FLUX IP-Adapter
      - InstantX FLUX IP-Adapter
      - inswapper_128.onnx

.PARAMETER ComfyUIRoot
    Root directory of the ComfyUI installation.

.PARAMETER JsonOutput
    Emit machine-readable JSON instead of human-friendly text.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\log_download_status.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\log_download_status.ps1 -JsonOutput
#>
[CmdletBinding()]
param(
    [string]$ComfyUIRoot = "C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI",
    [switch]$JsonOutput
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$models = @(
    @{
        Name   = "Pony Diffusion v6 XL"
        Paths  = @(
            (Join-Path $ComfyUIRoot "models\checkpoints\ponyDiffusionV6XL*.safetensors"),
            (Join-Path $ComfyUIRoot "models\checkpoints\*pony*v6*.safetensors")
        )
    },
    @{
        Name   = "XLabs FLUX IP-Adapter"
        Paths  = @(
            (Join-Path $ComfyUIRoot "models\xlabs\ip_adapter\*flux*ip*adapter*"),
            (Join-Path $ComfyUIRoot "models\ipadapter\*xlabs*flux*")
        )
    },
    @{
        Name   = "InstantX FLUX IP-Adapter"
        Paths  = @(
            (Join-Path $ComfyUIRoot "models\ipadapter\*instantx*flux*"),
            (Join-Path $ComfyUIRoot "models\xlabs\ip_adapter\*instantx*")
        )
    },
    @{
        Name   = "inswapper_128.onnx"
        Paths  = @(
            (Join-Path $ComfyUIRoot "models\reactor\faces\inswapper_128.onnx"),
            (Join-Path $ComfyUIRoot "models\insightface\inswapper_128.onnx")
        )
    }
)

$results = @()

foreach ($model in $models) {
    $found    = $false
    $filePath = ""
    $sizeMB   = 0

    foreach ($pattern in $model.Paths) {
        $matches = @(Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue)
        if ($matches.Count -gt 0) {
            $found    = $true
            $filePath = $matches[0].FullName
            $sizeMB   = [math]::Round($matches[0].Length / 1MB, 2)
            break
        }
    }

    $status = if ($found) { "OK" } else { "MISSING" }
    $results += @{
        Name   = $model.Name
        Status = $status
        Path   = $filePath
        SizeMB = $sizeMB
    }

    if (-not $JsonOutput) {
        if ($found) {
            Write-Host "[OK]      $($model.Name): $sizeMB MB ($filePath)"
        }
        else {
            Write-Host "[MISSING] $($model.Name): not found"
        }
    }
}

if ($JsonOutput) {
    $jsonResults = $results | ForEach-Object {
        [PSCustomObject]@{
            name    = $_.Name
            status  = $_.Status
            path    = $_.Path
            size_mb = $_.SizeMB
        }
    }
    $jsonResults | ConvertTo-Json -Depth 3 | Write-Host
}

$missing = @($results | Where-Object { $_.Status -eq "MISSING" })
if ($missing.Count -gt 0) {
    if (-not $JsonOutput) {
        Write-Host ""
        Write-Host "WARNING: $($missing.Count) model(s) missing"
    }
    exit 1
}

if (-not $JsonOutput) {
    Write-Host ""
    Write-Host "All models present"
}
exit 0
