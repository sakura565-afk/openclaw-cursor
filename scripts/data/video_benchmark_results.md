# ComfyUI video pipeline benchmark

_Generated: 2026-05-22 18:54:01 UTC_

## Hardware

- **Documented target:** NVIDIA RTX 3060 12GB VRAM (furniture catalog videos).
- **ComfyUI /system_stats devices:** unknown (dry-run)

## Summary tables

| model | input | output | time_sec | vram_peak_gb | quality_score |
|-------|-------|--------|----------|--------------|---------------|
| (dry-run) | — | — | 0.00 | 0.00 | 0.00 |

#### Row notes

- **(dry-run):** no ComfyUI calls

### Metric notes

- **RIFE rows:** `quality_score` = mean SSIM×100 between benchmark output frames and the **60 FPS reference** from `camera_motion_stable` (same motion, no neural interpolation). Higher is closer to the reference.
- **Naive 24→60 baseline:** duplicate/timed frames via ffmpeg `fps=60`; mean SSIM×100 vs reference is reported below when measured.
- **SUPIR rows:** `quality_score` = heuristic 0–100 from Laplacian sharpness vs bilinear upscale to the same pixel size (detail gain proxy).

## Best settings for furniture video (RTX 3060 12GB)

| Stage | Setting | Rationale |
|-------|---------|-----------|
| **Capture / motion** | `camera_motion_stable.py --effect pan --curve smoothstep --fps 60` | Smooth easing reduces judder; pan showcases depth on cabinets/sofas without extreme perspective drift. |
| **Base resolution** | 768×768 or 832×832 img2img before RIFE | Fits comfortably in 12GB with SUPIR headroom; raise only if SUPIR is off. |
| **RIFE** | Target **60 FPS**; source catalog motion at **24 FPS** is fine if RIFE runs last on baked MP4 | 24→60 improves motion continuity for web/social; use models from `models/frame_interpolation/`. |
| **SUPIR** | `SUPIR-v0Q_fp16.safetensors`, scale **1.25–1.5×** first | Sweet spot on 3060 12GB for furniture textures; 2× only for hero shots or shorter clips. |
| **SD img2img (when used)** | steps **24–32**, denoise **0.28–0.38**, CFG **5.5–7** | Preserves wood grain/upholstery; higher denoise blurs fine veneer lines. |
| **VRAM guard** | Run SUPIR after RIFE on **short segments** or **tile** if OOM | Peak memory spikes on high-res SUPIR; keep batch frame lists small. |
