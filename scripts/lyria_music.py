"""Lyria 3.5 via the Gemini API (needs a Google AI Studio API key).

Lyria 3.5 generates full-length songs with vocals from a text prompt, at
44.1 kHz stereo. The model is reachable through the Interactions API:

    POST https://generativelanguage.googleapis.com/v1beta/interactions

The API key comes from https://aistudio.google.com/apikey and is read from
the environment - never hardcoded, never logged.

Usage:
  GEMINI_API_KEY=... python scripts/lyria_music.py --out song.mp3 \
      --prompt "Genre: acoustic pop..." --lyrics "[verse]..."
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta/interactions"


def _key() -> str:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    raise SystemExit("Set GEMINI_API_KEY (https://aistudio.google.com/apikey)")


def generate(model: str, prompt: str, lyrics: str | None, timeout: int) -> dict:
    body: dict = {"model": model, "input": prompt}
    if lyrics:
        body["input"] = prompt + "\n\nLyrics:\n" + lyrics
    req = urllib.request.Request(
        BASE, data=json.dumps(body).encode(),
        headers={"x-goog-api-key": _key(), "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise SystemExit(f"HTTP {e.code}: {detail[:500]}") from None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--lyrics", default="")
    ap.add_argument("--model", default="lyria-3-clip-preview")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()

    res = generate(a.model, a.prompt, a.lyrics or None, a.timeout)
    audio = res.get("output_audio") or res.get("output_audio_data")
    data = None
    if isinstance(audio, dict):
        data = audio.get("data")
    elif isinstance(audio, str):
        data = audio
    if not data:
        # Some responses nest the audio under steps.
        for step in res.get("steps", []) or []:
            for cand in (step.get("output_audio"), step.get("audio")):
                if isinstance(cand, dict) and cand.get("data"):
                    data = cand["data"]
                    break
            if data:
                break
    if not data:
        print(json.dumps(res, ensure_ascii=False)[:800], file=sys.stderr)
        raise SystemExit("в ответе нет output_audio")
    with open(a.out, "wb") as fh:
        fh.write(base64.b64decode(data))
    print(f"готово: {a.out} ({os.path.getsize(a.out)} байт)")
    if res.get("output_text"):
        print("текст/слова:", str(res["output_text"])[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
