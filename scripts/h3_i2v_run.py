# -*- coding: utf-8 -*-
"""H3 I2V runner (PROD ComfyUI :8188) with exact LoRA names.

IMPORTANT INVARIANT: a LoraLoader exposes CLIP as output **1**, not 0 - using
[prev_c, 0] for the LoRA CLIP output makes ComfyUI answer HTTP 400.

Usage:
python h3_run.py --image NAME.jpg --prompt-file p.txt --loras-json '[{"n":"...","sm":0.85,"sc":0.8}]'
   --width 1056 --height 704 --length 175 --seed 1 --prefix pfx --audio a.wav --output out.mp4
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from video_pipeline.assembler import validate_workflow_against_object_info

BASE = os.environ.get("COMFYUI_PROD_URL", "http://127.0.0.1:8188")
COMFYUI_INPUT_DIR = os.environ.get("COMFYUI_INPUT_DIR", r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI\input")
COMFYUI_OUTPUT_DIR = os.environ.get("COMFYUI_OUTPUT_DIR", r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI\output")
FFMPEG = os.environ.get("FFMPEG_BIN", r"C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe")


def _scaled_image(nodes: dict, nid: int, image_name: str, source: list) -> tuple[str, int]:
    """Add a LoadImage + ImageScaleToTotalPixels pair, return (scale node id, next nid)."""
    nodes[str(nid)] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    load_id = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
        "image": [load_id, 0], "upscale_method": "lanczos", "megapixels": 0.73, "resolution_steps": 1}}
    return str(nid), nid + 1


def build_workflow(a):
    loras = json.loads(a.loras_json)
    nodes = {
        "1": {"class_type": "LoadImage", "inputs": {"image": a.image}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors", "type": "minimax"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
    }
    prev_m, prev_c = "2", "3"
    nid = 5
    for lo in loras:
        nodes[str(nid)] = {"class_type": "LoraLoader", "inputs": {
            "model": [prev_m, 0], "clip": [prev_c, 1] if prev_c != "3" else [prev_c, 0],
            "lora_name": lo["n"], "strength_model": lo["sm"], "strength_clip": lo.get("sc", lo["sm"])}}
        prev_m = prev_c = str(nid)
        nid += 1
    nodes[str(nid)] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
        "image": ["1", 0], "upscale_method": "lanczos", "megapixels": 0.73, "resolution_steps": 1}}
    scale_id = str(nid)
    nid += 1
    h3_inputs = {
        "clip": [prev_c, 1] if prev_c != "3" else [prev_c, 0], "vae": ["4", 0], "first_frame": [scale_id, 0],
        "prompt": a.prompt,
        # NOTE: the 2026-10 core has no negative_prompt input on the H3 node - ComfyUI
        # silently ignores unknown inputs, so a negative prompt there was dead weight.
        "width": a.width, "height": a.height, "length": a.length}
    ref_images = getattr(a, "ref_images", None) or []
    guide_video = getattr(a, "guide_video", "") or ""
    if ref_images or guide_video:
        # REF2VA mode: a reference image carries the subject, the prompt drives the action.
        nodes["2"]["inputs"]["unet_name"] = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
        # The official R2V template decodes video with the int8 convrot VAE (2.6 GB
        # instead of 4.9 GB for fp16) - same pixels, much less VRAM.
        nodes["4"]["inputs"]["vae_name"] = getattr(
            a, "ref_vae", "minimax_h3_video_vae_int8_convrot.safetensors")
        # The audio VAE gets its own id: node "5" may already hold a LoRA node
        # created by the loop above, and overwriting it silently swapped the
        # KSampler's MODEL input for a VAE - the graph then failed validation
        # with "received_type(VAE) mismatch input_type(MODEL)", and the LoRA
        # never applied at all.
        nodes[str(nid)] = {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}}
        audio_vae_id = str(nid)
        nid += 1
        if getattr(a, "turbo", False):
            # 4-step turbo LoRA for the ref2v pipeline (official Comfy-Org model).
            nodes[str(nid)] = {"class_type": "LoraLoaderModelOnly", "inputs": {
                "model": [prev_m, 0],
                "lora_name": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
                "strength_model": 1.0}}
            prev_m = str(nid)
            nid += 1
        links = []
        for name in ref_images:
            scale_id, nid = _scaled_image(nodes, nid, name, ["1", 0])
            links.append([scale_id, 0])
        nodes[str(nid)] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "clip": [prev_c, 1] if prev_c != "3" else [prev_c, 0],
            "prompt": a.prompt,
            "width": a.width, "height": a.height, "length": a.length,
            "ref_image_size": getattr(a, "ref_image_size", "match"),
            "ref_images": links,
            "audio_vae": [audio_vae_id, 0]}}
        vid = str(nid)
        nid += 1
        # ALIGNED GUIDE: the whole source clip is fed back as the guide, so the
        # guide latents sit at the SAME temporal and spatial positions as the
        # output. That is what keeps motion, timing and framing intact while
        # the sampler adds detail - the mechanism the LMS sharpness LoRA was
        # trained on, and the same one Tessera uses.
        if guide_video:
            nodes[str(nid)] = {"class_type": "LoadVideo", "inputs": {"file": guide_video}}
            lv = str(nid)
            nid += 1
            nodes[str(nid)] = {"class_type": "GetVideoComponents", "inputs": {"video": [lv, 0]}}
            gv = str(nid)
            nid += 1
            nodes[str(nid)] = {"class_type": "MiniMaxH3AddGuide", "inputs": {
                "positive": [vid, 0], "latent": [vid, 1],
                "frame_idx": getattr(a, "guide_frame", 0),
                "image": [gv, 0], "vae": ["4", 0]}}
            guide_pos = [str(nid), 0]
            nid += 1
        else:
            guide_pos = [vid, 0]
        # The official ComfyUI R2V template uses res_multistep with a beta/normal
        # scheduler - both clearly outperform euler/simple on reference-heavy prompts.
        nodes[str(nid)] = {"class_type": "KSampler", "inputs": {
            "model": [prev_m, 0], "positive": guide_pos, "negative": [vid, 0], "latent_image": [vid, 1],
            "seed": a.seed, "steps": a.steps, "cfg": 1.0,
            "sampler_name": getattr(a, "sampler", "res_multistep"),
            "scheduler": getattr(a, "scheduler", "beta"), "denoise": 1.0}}
        samp = str(nid)
        nid += 1
        nodes[str(nid)] = {"class_type": "VAEDecode", "inputs": {"samples": [samp, 0], "vae": ["4", 0]}}
        dec = str(nid)
        nid += 1
        nodes[str(nid)] = {"class_type": "CreateVideo", "inputs": {"images": [dec, 0], "fps": 24.0}}
        cv = str(nid)
        nid += 1
        nodes[str(nid)] = {"class_type": "SaveVideo", "inputs": {
            "video": [cv, 0], "filename_prefix": a.prefix, "format": "auto", "codec": "h264"}}
        return nodes
    # The 2026-10 core can pin the LAST frame as well (MiniMaxH3ImageToVideo.last_frame).
    if getattr(a, "last_frame", ""):
        last_scale, nid = _scaled_image(nodes, nid, a.last_frame, ["1", 0])
        h3_inputs["last_frame"] = [last_scale, 0]
    nodes[str(nid)] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": h3_inputs}
    vid = str(nid)
    nid += 1
    positive = [vid, 0]
    # A guide image can be pinned to a specific frame of the clip (MiniMaxH3AddGuide).
    if getattr(a, "guide_image", ""):
        guide_scale, nid = _scaled_image(nodes, nid, a.guide_image, ["1", 0])
        nodes[str(nid)] = {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": [vid, 0], "latent": [vid, 1],
            "frame_idx": getattr(a, "guide_frame", 0),
            "image": [guide_scale, 0], "vae": ["4", 0]}}
        positive = [str(nid), 0]
        nid += 1
    # Temporal State Transport (SelfLift): a MODEL -> MODEL patch that corrects
    # the temporal attention of H3, so details stop flickering between frames.
    # It cannot create detail the model does not have - it only makes what is
    # already there stable.
    if getattr(a, "tst_tau", 0.0) > 0:
        nodes[str(nid)] = {"class_type": "SelfLiftH3TST", "inputs": {
            "model": [prev_m, 0], "tau": a.tst_tau,
            "log_diagnostics": bool(getattr(a, "tst_log", False))}}
        prev_m = str(nid)
        nid += 1
    nodes[str(nid)] = {"class_type": "KSampler", "inputs": {
        "model": [prev_m, 0], "positive": positive, "negative": [vid, 0], "latent_image": [vid, 1],
        "seed": a.seed, "steps": a.steps, "cfg": 1.0, "sampler_name": "euler",
        "scheduler": "simple", "denoise": 1.0}}
    samp = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "VAEDecode", "inputs": {"samples": [samp, 0], "vae": ["4", 0]}}
    dec = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "CreateVideo", "inputs": {"images": [dec, 0], "fps": 24.0}}
    cv = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "SaveVideo", "inputs": {
        "video": [cv, 0], "filename_prefix": a.prefix, "format": "auto", "codec": "h264"}}
    return nodes



def _validate_against_live_comfyui(workflow: dict) -> list[str]:
    """Catch unknown inputs and wrong COMBO values before ComfyUI answers HTTP 400.

    A typo in a LoRA file name is invisible until the core rejects the whole graph,
    and the error message does not name the offending value.
    """
    try:
        raw = urllib.request.urlopen(BASE + "/object_info", timeout=120).read().decode("utf-8", "replace")
        object_info = json.loads(raw)
    except Exception as exc:  # the live check is a convenience, never a hard gate
        print("object_info недоступен, пропускаю локальную проверку:", exc, flush=True)
        return []
    return validate_workflow_against_object_info(workflow, object_info)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True)
    p.add_argument("--last-frame", default="",
                   help="image in ComfyUI/input pinned as the LAST frame of the clip")
    p.add_argument("--guide-image", default="",
                   help="image in ComfyUI/input added as a guide reference (MiniMaxH3AddGuide)")
    p.add_argument("--guide-video", default="",
                   help="clip in ComfyUI/input used as the ALIGNED guide: the whole clip is "
                        "fed back so its guide latents sit at the same temporal and spatial "
                        "positions as the output. This is the mechanism the LMS sharpness "
                        "LoRA was trained on and the same one Tessera uses.")
    p.add_argument("--ref-image", action="append", default=[],
                   help="reference image(s) in ComfyUI/input for the REF2VA mode")
    p.add_argument("--turbo", action="store_true",
                   help="EXPERIMENTAL, BROKEN in this wiring: the official 4-step ref2v turbo "
                        "LoRA needs the template's SamplerCustomAdvanced graph; with the plain "
                        "KSampler it blows out the frame. Keep it off.")
    p.add_argument("--sampler", default="res_multistep")
    p.add_argument("--scheduler", default="beta")
    p.add_argument("--ref-image-size", default="match",
                   help="'match' (fast) or 'max' (best identity fidelity, much slower)")
    p.add_argument("--guide-frame", type=int, default=0,
                   help="frame index the guide image is pinned to")
    p.add_argument("--tst-tau", type=float, default=0.0,
                   help="SelfLift Temporal State Transport strength for H3 "
                        "(0 disables it, 0.2 is the paper setting)")
    p.add_argument("--tst-log", action="store_true",
                   help="log the per-forward Spectral Tension diagnostics")
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--prompt-prefix", default="")
    p.add_argument("--loras-json", default="[]")
    p.add_argument("--negative", default="")
    p.add_argument("--width", type=int, default=1056)
    p.add_argument("--height", type=int, default=704)
    p.add_argument("--length", type=int, default=175)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--steps", type=int, default=14)
    p.add_argument("--prefix", default="h3run")
    p.add_argument("--audio", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--wait", type=int, default=7200)
    a = p.parse_args()
    a.prompt = a.prompt_prefix + "\n" + open(a.prompt_file, encoding="utf-8").read()

    # LoadVideo wants a file NAME inside ComfyUI's input dir. Accept a path
    # here and place it there, so the caller does not have to know the layout.
    # os.path.sep is a backslash on Windows, but these paths use forward
    # slashes, so test for absoluteness instead of for a separator.
    for attr in ("image", "guide_video"):
        val = getattr(a, attr, "") or ""
        if val and ("/" in val or "\\" in val):
            from pathlib import Path as _P
            import shutil as _sh
            try:
                from scripts import local_paths as _lp
            except ImportError:
                import local_paths as _lp  # type: ignore[no-redef]
            src = _P(val)
            if not src.exists():
                print(f"файл не найден: {src}", flush=True)
                return 2
            dst = _lp.PROD_IN / src.name
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                _sh.copy2(src, dst)
            setattr(a, attr, src.name)

    workflow = build_workflow(a)
    problems = _validate_against_live_comfyui(workflow)
    if problems:
        raise SystemExit("workflow rejected before sending:\n  " + "\n  ".join(problems))
    req = urllib.request.Request(BASE + "/prompt", data=json.dumps({"prompt": workflow}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        pid = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())["prompt_id"]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit("ComfyUI rejected the graph (HTTP %s):\n%s" % (exc.code, body))
    print("prompt_id", pid, flush=True)

    save_ids = [k for k, v in workflow.items() if v.get("class_type") == "SaveVideo"]
    t0 = time.time()
    last = -1
    while time.time() - t0 < a.wait:
        h = json.loads(urllib.request.urlopen(BASE + "/history/" + pid, timeout=30).read().decode())
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                print("ERROR:", str(st.get("messages"))[:600], flush=True)
                return 1
            outs = h[pid].get("outputs", {})
            # The SaveVideo node's output is the render. Other nodes report
            # too - VHS's LoadVideo lists the INPUT clip - and taking the
            # first video in the history silently hands back the input.
            ordered = [(sid, outs[sid]) for sid in save_ids if sid in outs]
            ordered += [(k, v) for k, v in outs.items() if k not in save_ids]
            for _sid, o in ordered:
                for v in o.get("videos", []) + o.get("images", []):
                    fn = v.get("filename", "")
                    sub = v.get("subfolder", "")
                    src = os.path.join(COMFYUI_OUTPUT_DIR, sub, fn)
                    if not src.lower().endswith(".mp4"):
                        cand = [x for x in os.listdir(os.path.join(COMFYUI_OUTPUT_DIR, sub)) if x.startswith(fn.split(".")[0]) and x.endswith(".mp4")]
                        if not cand:
                            continue
                        src = os.path.join(COMFYUI_OUTPUT_DIR, sub, cand[0])
                    print("видео:", src, flush=True)
                    dur = 0.0
                    try:
                        from video_pipeline.assembler import probe as _probe
                        _v, _a, _d = _probe(Path(src))
                        dur = float(_d["format"]["duration"])
                    except Exception:
                        dur = 0.0
                    cmd = [FFMPEG, "-y", "-stream_loop", "-1", "-i", src, "-i", a.audio,
                           "-t", f"{dur:.3f}" if dur > 0.1 else "7.3",
                           "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                           "-map", "0:v:0", "-map", "1:a:0", "-shortest", a.output]
                    subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    # Guard: VHS's LoadVideo reports the input clip as an
                    # output too, so a mis-picked history entry produces a
                    # file identical to the source. That is not a render.
                    try:
                        in_size = os.path.getsize(src)
                        out_size = os.path.getsize(a.output)
                        same = in_size == out_size and open(src, "rb").read(4096) == open(a.output, "rb").read(4096)
                    except OSError:
                        same = False
                    if same:
                        print("ВНИМАНИЕ: результат идентичен входу - рендер не дал нового файла", flush=True)
                        return 3
                    print("готово:", a.output, round(os.path.getsize(a.output) / 1e6, 2), "МБ", flush=True)
                    return 0
            return 0
        el = int(time.time() - t0)
        if el // 60 != last:
            last = el // 60
            q = json.loads(urllib.request.urlopen(BASE + "/queue", timeout=25).read().decode())
            print("[%d мин] running=%d" % (el // 60, len(q.get("queue_running", []))), flush=True)
        time.sleep(15)
    print("таймаут")
    return 2


if __name__ == "__main__":
    sys.exit(main())
