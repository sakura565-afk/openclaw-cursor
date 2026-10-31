# I2V runners (H3 / FreeVideo / Qwen-Image-Edit)

These three CLIs are the production path for image-to-video work on this box. They used to
live only in `%LOCALAPPDATA%\hermes\cache\scratch`, which is pruned when idle for 24 h - they
now live in the repository so a render is reproducible after a reboot.

| script | engine | ComfyUI | notes |
|---|---|---|---|
| `scripts/h3_i2v_run.py` | MiniMax-H3 (FL2VA) | PROD `:8188` | LoRA stack, first-frame I2V, ambient audio mux |
| `scripts/freevideo_lora_run.py` | FreeVideo / Video DeltaNet | LAB `:8189` | LoRA stack, VRAM guard, render lock |
| `scripts/qwen_edit_run.py` | Qwen-Image-Edit | PROD `:8188` | edit-first: adds/changes a person in a photo |

## Paths (environment variables, with this box's values as defaults)

```
COMFYUI_PROD_URL        http://127.0.0.1:8188
COMFYUI_LAB_URL         http://127.0.0.1:8189
COMFYUI_INPUT_DIR       E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI\input
COMFYUI_PROD_OUTPUT     E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI\output
COMFYUI_PROD_OUTPUT_DIR same as COMFYUI_PROD_OUTPUT
FREEVIDEO_LAB_DIR       E:/ComfyUI_LAB_037/ComfyUI_windows_portable/ComfyUI
FREEVIDEO_OUT_DIR       G:/FreeVideo_output
HERMES_SCRATCH_DIR      C:/Users/user/AppData/Local/hermes/cache/scratch
FFMPEG_BIN / FFPROBE_BIN  path to ffmpeg / ffprobe
```

## Usage

```bash
# H3: photo -> 7 s clip, two LoRAs
python scripts/h3_i2v_run.py --image NAME.jpg --prompt-file prompt.txt \
    --loras-json '[{"n":"Perfect ASSets Baby Got Back - MinimaxH3 - shkzz....safetensors","sm":0.6,"sc":0.6}]' \
    --width 1056 --height 704 --length 175 --seed 575757 --steps 14 \
    --prefix h3run --audio ambient.wav --output out.mp4

# FreeVideo: photo -> 7 s clip with a camera move
python scripts/freevideo_lora_run.py --image src.jpg --width 704 --height 1056 --seconds 7 \
    --prompt prompt.txt --out out.mp4 \
    --lora "Panties Off! v1.1 - MinimaxH3 -.safetensors:0.8"

# Qwen: edit a photo (add a partner, change the pose)
python scripts/qwen_edit_run.py tag frame.jpg 12345 1.0 prompt.txt 32 1056 704
```

## Traps encoded in the code (and pinned by `tests/test_i2v_runners.py`)

1. **`LoraLoader` exposes CLIP as output 1, not 0.** Wiring `[node, 0]` makes ComfyUI answer
   HTTP 400 and the run dies before the first frame. The CLIPLoader's own CLIP is output 0.
2. **LoRA file names contain commas.** Never split a LoRA name on a comma - pass the whole
   file name. `Penis Lora v1.1 - MinimaxH3 - PENISLORA,realistic.safetensors` is one name.
3. **LoRA trigger words go at the very start of the positive prompt** (e.g. `PENISLORA`,
   `shkzz`). Without the trigger the model renders a flat, wrong-shaped version.
4. **Qwen needs the 2D VAE** (`qwen_image_vae_2d.safetensors`). The stock ComfyUI Qwen
   autoencoder is a 3D/video VAE and raises
   `size mismatch ... encoder.conv1.weight [96,4,1,3,3] vs [160,12,3,3,3]`.
5. **FreeVideo pauses `llama-server` and holds `render_vram.lock` for the whole render** - the
   local watchdog otherwise resurrects llama-server mid-render and the render slows 30-40x.
6. **The H3 runner must not be renamed into a comma-separated CLI.** Use `--loras-json`.
