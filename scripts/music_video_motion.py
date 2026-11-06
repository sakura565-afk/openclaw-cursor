"""Music-video motion design for a single locked-off beauty shot.

The source clip is a tripod-held beauty portrait: the face, the audio and the
timing are all good, but the camera never moves, so it reads as a static
picture with a soundtrack. The fix is editorial, not generative - nothing is
re-rendered, so the face cannot drift and the music is not touched.

What the motion design does:

  1. a slow push-in across the whole clip (zoom 1.00 -> 1.26), so the frame
     is always breathing;
  2. a handheld micro-drift on top of it, two incommensurate sine waves per
     axis, which is what stops a digital push-in from looking like a zoom;
  3. re-frame jumps on selected beats - the crop window changes size in one
     frame, which reads as a jump cut in framing while the subject's own
     motion stays continuous;
  4. two flash accents on the strongest onsets;
  5. a warm film grade: S-curve, warm shift, vignette, grain, and an
     unsharp pass that pays for the upscale.

The crop keeps the face in frame: the face centre sits at x=320, the eyes at
y=260 in the 736x1120 original, so the crop window is solved for those two
landmarks instead of being centred on the canvas.

Usage:
    python scripts/music_video_motion.py SRC.mp4 OUT.mp4
    python scripts/music_video_motion.py SRC.mp4 OUT.mp4 --flip-segment 2.46:0.37
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Landmarks measured on the source frame (736x1120 original).
FACE_X = 320
EYES_Y = 260
SRC_W = 736
SRC_H = 1120
HEADROOM = 3  # upscale factor for the crop to have room to move

# Zoom schedule: a slow ramp with a few beat steps.
# The source is only 736 px wide and the crown of the head is already cut off,
# so the push-in stays shallow - past about 1.18 the frame loses the
# background and the move stops reading as a camera and starts reading as a
# digital crop. The per-look values live in PRESETS.
ZOOM_START = 1.00

# Where the face sits in the output, as a function of time. A camera move
# shows up as the subject sliding in the frame while the scale changes; a
# plain zoom keeps the subject pinned to the centre and reads as a zoom.
# At zoom 1.0 these have to be the face's own position in the source
# (x=320, eyes y=260) or the framing would not match the original.
FACE_OUT_X_START = 320
FACE_OUT_X_END = 348
EYES_OUT_Y_START = 260
EYES_OUT_Y_END = 278

# Two looks, same machinery.
#   tasteful  - a calm push that reads as a fashion beauty insert
#   energetic - a deeper push with beat-stepped reframes and flash accents,
#               which is what a music video needs
PRESETS: dict[str, dict] = {
    "tasteful": {
        "zoom_end": 1.12,
        "zoom_steps": [(3.90, 0.05), (4.99, 0.06), (5.71, 0.05), (6.62, -0.04)],
        "flashes": [(5.50, 0.10), (6.25, 0.08)],
        "shake_x": 4.0,
        "shake_y": 3.0,
        "drift_x": 28,
        "drift_y": 18,
        "curves": "master='0/0.02 0.22/0.20 0.78/0.85 1/0.98'",
        "balance": "rs=0.008:gs=0.003:bs=-0.008:rm=0.006:gm=0.0:bm=-0.006",
        "vignette": "PI/10",
        "unsharp": 0.25,
        "grain": 3,
    },
    "energetic": {
        "zoom_end": 1.18,
        "zoom_steps": [
            (3.18, 0.06), (3.90, -0.06), (4.48, 0.09), (4.99, -0.07),
            (5.25, 0.08), (5.50, -0.08), (5.71, 0.10), (6.25, -0.07),
        ],
        "flashes": [(3.90, 0.09), (4.99, 0.10), (5.50, 0.11), (6.25, 0.09)],
        "shake_x": 5.5,
        "shake_y": 4.0,
        "drift_x": 34,
        "drift_y": 22,
        "curves": "master='0/0.025 0.22/0.21 0.78/0.86 1/0.98'",
        "balance": "rs=0.010:gs=0.004:bs=-0.010:rm=0.007:gm=0.0:bm=-0.007",
        "vignette": "PI/11",
        "unsharp": 0.18,
        "grain": 3,
    },
}


def _zoom_expr(duration: float, look: dict) -> str:
    """Zoom factor as a function of time."""
    start = ZOOM_START
    end = float(look["zoom_end"])
    ramp = f"{start}+{end - start:.4f}*min(t/{duration:.4f},1)"
    expr = ramp
    for when, delta in look["zoom_steps"]:
        expr = f"({expr})+{delta:.4f}*gte(t,{when:.3f})"
    return expr


def _face_out_x_expr(duration: float, look: dict) -> str:
    """Where the face sits in the output frame, over time.

    At zoom 1.0 this has to be the face's own position in the source (320),
    otherwise the framing does not match the original. The drift away from
    that is what reads as a camera move rather than a zoom.
    """
    ratio = f"min(t/{duration:.4f},1)"
    drift = float(look["drift_x"])
    return f"{FACE_OUT_X_START}+{drift:.2f}*({ratio})"


def _eyes_out_y_expr(duration: float, look: dict) -> str:
    """Where the eyes sit in the output frame, over time."""
    ratio = f"min(t/{duration:.4f},1)"
    drift = float(look["drift_y"])
    return f"{EYES_OUT_Y_START}+{drift:.2f}*({ratio})"


def _crop_x_expr(duration: float, look: dict) -> str:
    """Left edge of the crop window.

    The scaled frame is SRC_W*HEADROOM wide. A zoom of Z shows a window of
    SRC_W*HEADROOM/Z, and the face (at FACE_X*HEADROOM in the scaled frame)
    lands at face_out_x in the output when the window starts at
    FACE_X*HEADROOM - face_out_x*HEADROOM/Z.
    """
    face = FACE_X * HEADROOM
    zoom = _zoom_expr(duration, look)
    expr = f"{face}-({_face_out_x_expr(duration, look)})*{HEADROOM}/({zoom})"
    # handheld wobble, converted from output pixels into window pixels
    shake = float(look["shake_x"])
    expr = (f"({expr})+({shake:.2f}*sin(2.1*t)"
            f"+{shake * 0.45:.2f}*sin(5.3*t+1.2))*{HEADROOM}/({zoom})")
    return expr


def _crop_y_expr(duration: float, look: dict) -> str:
    """Top edge of the crop window, solved the same way for the eyes."""
    eyes = EYES_Y * HEADROOM
    zoom = _zoom_expr(duration, look)
    expr = f"{eyes}-({_eyes_out_y_expr(duration, look)})*{HEADROOM}/({zoom})"
    shake = float(look["shake_y"])
    expr = (f"({expr})+({shake:.2f}*sin(1.7*t+0.6)"
            f"+{shake * 0.4:.2f}*sin(4.1*t))*{HEADROOM}/({zoom})")
    return expr


def _flash_expr(look: dict) -> str:
    expr = "0"
    for when, amount in look["flashes"]:
        expr = f"if(lt(abs(t-{when:.3f}),0.045),{amount:.3f},({expr}))"
    return expr


def build_filter(duration: float, look: dict,
                 flip: list[tuple[float, float]]) -> str:
    zoom = _zoom_expr(duration, look)
    # The crop window shrinks as the zoom grows, and the final scale puts the
    # frame back to the source size, so the output resolution never changes.
    chain = [
        f"scale={SRC_W * HEADROOM}:{SRC_H * HEADROOM}:flags=bicubic",
        (f"crop=w='{SRC_W * HEADROOM}/({zoom})':h='{SRC_H * HEADROOM}/({zoom})'"
         f":x='{_crop_x_expr(duration, look)}':y='{_crop_y_expr(duration, look)}'"),
        f"scale={SRC_W}:{SRC_H}:flags=bicubic",
        f"unsharp=5:5:{float(look['unsharp']):.2f}:5:5:0.0",
    ]
    if flip:
        # A mirrored beat reads as a cut without any new footage.
        when, length = flip[0]
        chain.append(
            f"hflip=enable='between(t,{when:.3f},{when + length:.3f})'")
    chain += [
        # S-curve with the shadows lifted a little: the previous pass crushed
        # them and the skin went flat.
        f"curves={look['curves']}",
        f"colorbalance={look['balance']}",
        f"eq=brightness='{_flash_expr(look)}':eval=frame",
        f"vignette={look['vignette']}:mode=forward",
        f"noise=alls={int(look['grain'])}:allf=t+u",
        "format=yuv420p",
    ]
    return ",".join(chain)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--flip-segment", default="",
                    help="start:length in seconds for a mirrored beat")
    ap.add_argument("--crf", type=int, default=17)
    ap.add_argument("--preset", default="slow", help="x264 preset")
    ap.add_argument("--look", default="tasteful",
                    choices=sorted(PRESETS), help="which motion design to apply")
    a = ap.parse_args()

    src = Path(a.src)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, check=True, timeout=120)
    duration = float(probe.stdout.strip().splitlines()[0])

    flip: list[tuple[float, float]] = []
    if a.flip_segment:
        start, length = a.flip_segment.split(":")
        flip = [(float(start), float(length))]

    vf = build_filter(duration, PRESETS[a.look], flip)
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(src),
        "-vf", vf,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", a.preset, "-crf", str(a.crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out),
    ]
    print("  ffmpeg:", " ".join(cmd[:6]), "...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        print(result.stderr[-2500:], file=sys.stderr)
        return result.returncode
    print(f"  готово: {out} ({out.stat().st_size/1024**2:.1f} МБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
