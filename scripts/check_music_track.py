"""Compare music tracks: is there a voice, what instruments, what tempo.

Uses the MiniMax Code audio-QA connector (audios_understand) when the
MiniMax Code host is running, and falls back to a local spectral analysis so
the check still works offline.

Usage:
  python scripts/check_music_track.py --audio track.wav [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave

import numpy as np

MCODE_TOOLS = os.environ.get("MCODE_TOOLS", "")


def _mcode_env() -> dict | None:
    home = os.path.expanduser("~")
    cap = os.path.join(home, ".minimax", "run", "mcode-auth-lease-v1.cap")
    tools = MCODE_TOOLS or os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Programs", "MiniMax Code", "resources", "resources", "mcode-tools")
    if not os.path.exists(cap) or not os.path.isdir(tools):
        return None
    env = dict(os.environ)
    env.update({
        "MCODE_AUTH_PROVIDER": "shared-broker",
        "MCODE_AUTH_BROKER_CAPABILITY_FILE": cap,
        "MCODE_REGION": "en",
    })
    # the pipe name changes per host session; find the live one
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[System.IO.Directory]::GetFiles('\\\\.\\pipe\\') | "
                              "Where-Object {$_ -match 'mcode-auth-lease'}"],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None
    pipes = [p.strip() for p in out.splitlines() if "mcode-auth-lease" in p]
    if not pipes:
        return None
    env["MCODE_AUTH_BROKER_ENDPOINT"] = pipes[0]
    return env


def audio_qa(path: str) -> str:
    env = _mcode_env()
    node = shutil.which("node")
    if not env or not node:
        return ""
    tools = MCODE_TOOLS or os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Programs", "MiniMax Code", "resources", "resources", "mcode-tools")
    cli = os.path.join(tools, "cli.mjs")
    tmp = os.path.join(os.path.dirname(path) or ".", "_qa_upload.wav")
    shutil.copy2(path, tmp)
    r = subprocess.run([node, cli, "upload-temp-url", tmp, "--mime-type", "audio/wav"],
                       capture_output=True, text=True, timeout=280, env=env)
    try:
        url = json.loads(r.stdout)["temp_url"]
    except Exception:
        return ""
    args = {"audio_info": [{"url": url, "mime_type": "audio/wav",
        "prompt": "List the instruments, give the tempo in BPM, and state clearly whether "
                  "a human voice sings words or only vocalizes. Is the guitar a real "
                  "fingerpicked acoustic/nylon guitar or an electronic synth?"}]}
    r2 = subprocess.run([node, cli, "call", "connector__matrix__audios_understand",
                         "--args", json.dumps(args)],
                        capture_output=True, text=True, timeout=280, env=env)
    try:
        return json.loads(r2.stdout)["results"][0]["description"]
    except Exception:
        return ""


def local_analysis(path: str) -> dict:
    w = wave.open(path, "rb")
    sr = w.getframerate(); n = w.getnframes(); ch = w.getnchannels()
    d = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    d = d.reshape(-1, ch).mean(axis=1)
    N = len(d)
    S = np.abs(np.fft.rfft(d * np.hanning(N)))
    f = np.fft.rfftfreq(N, 1 / sr)

    def band(a: int, b: int) -> float:
        m = (f >= a) & (f < b)
        return float(S[m].sum()) / (S.sum() + 1e-9)

    # envelope autocorrelation -> tempo
    hop = sr // 100
    env = np.array([np.sqrt((d[i:i + hop] ** 2).mean()) for i in range(0, len(d) - hop, hop)])
    ac = np.correlate(env - env.mean(), env - env.mean(), "full")[len(env) - 1:]
    lo, hi = int(100 / 2.6), int(100 / 0.8)
    lag = lo + int(np.argmax(ac[lo:hi]))
    bpm = 60.0 / (lag * 0.01)
    return {
        "duration_s": round(N / sr, 2),
        "sample_rate": sr,
        "channels": ch,
        "rms": round(float(np.sqrt((d ** 2).mean())), 4),
        "peak": round(float(np.abs(d).max()), 3),
        "energy_below_250hz": round(band(60, 250), 3),
        "energy_250_3500hz": round(band(250, 3500), 3),
        "energy_above_4000hz": round(band(4000, 14000), 3),
        "tempo_bpm": round(bpm, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--json", default="")
    ap.add_argument("--no-qa", action="store_true")
    a = ap.parse_args()

    out = {"file": os.path.basename(a.audio), "local": local_analysis(a.audio)}
    if not a.no_qa:
        qa = audio_qa(a.audio)
        if qa:
            out["qa"] = qa
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
