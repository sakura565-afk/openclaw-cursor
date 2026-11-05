"""Ask the MiniMax Code audio-QA connector about a local WAV file.

The connector runs inside the MiniMax Code host (shared-broker auth), so the
pipe endpoint and the capability file are required. Both are discovered at
runtime: the pipe name lives in the mcode-tools wrapper and the capability
file has a fixed name.

Usage:
  python scripts/minimax_audio_qa.py track.wav "Is there a human voice singing?"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

TOOL_NAME = "connector__matrix__audios_understand"
DEFAULT_QUESTION = ("List the instruments, give the tempo in BPM, and state clearly whether a "
                    "human voice sings actual words (quote a line) or only vocalizes.")


def _tools_dir() -> str:
    env = os.environ.get("MCODE_TOOLS")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(local, "Programs", "MiniMax Code", "resources", "resources", "mcode-tools")


def _find_pipe(tools_dir: str) -> str:
    """The pipe id is baked into the global mcode-tools wrapper script."""
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".minimax", "bin", "mcode-tools-global.cmd"),
        os.path.join(tools_dir, "bin", "mcode-tools-global.cmd"),
        os.path.join(tools_dir, "mcode-tools-global.cmd"),
    ]
    for cand in candidates:
        if not os.path.exists(cand):
            continue
        try:
            data = open(cand, "rb").read()
        except OSError:
            continue
        m = re.search(rb"mcode-auth-lease-([0-9a-f]{16})", data)
        if m:
            return r"\\.\pipe\mcode-auth-lease-" + m.group(1).decode()
    return ""


def _env() -> dict | None:
    tools = _tools_dir()
    cap = os.path.join(os.path.expanduser("~"), ".minimax", "run", "mcode-auth-lease-v1.cap")
    pipe = _find_pipe(tools)
    if not pipe or not os.path.isfile(cap) or not os.path.isdir(tools):
        return None
    env = dict(os.environ)
    env.update({
        "MCODE_AUTH_PROVIDER": "shared-broker",
        "MCODE_AUTH_BROKER_ENDPOINT": pipe,
        "MCODE_AUTH_BROKER_CAPABILITY_FILE": cap,
        "MCODE_REGION": "en",
    })
    return env


def _run(env: dict, args: list[str], timeout: int = 280) -> str:
    node = shutil.which("node")
    cli = os.path.join(_tools_dir(), "cli.mjs")
    if not node or not os.path.exists(cli):
        return ""
    r = subprocess.run([node, cli] + args, capture_output=True, text=True, env=env, timeout=timeout)
    return r.stdout


def ask(path: str, question: str = DEFAULT_QUESTION) -> str:
    env = _env()
    if not env:
        return "MiniMax Code host недоступен (нет pipe/lease)"
    out = _run(env, ["upload-temp-url", path, "--mime-type", "audio/wav"])
    try:
        url = json.loads(out)["temp_url"]
    except Exception:
        return f"upload не удался: {out[:200]}"
    payload = {"audio_info": [{"url": url, "mime_type": "audio/wav", "prompt": question}]}
    out = _run(env, ["connector", "call", TOOL_NAME, "--args", json.dumps(payload, ensure_ascii=False)])
    try:
        return json.loads(out)["results"][0]["description"]
    except Exception:
        return f"вызов не удался: {out[:200]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--question", default=DEFAULT_QUESTION)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    ans = ask(a.audio, a.question)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"file": os.path.basename(a.audio), "qa": ans}, f, ensure_ascii=False, indent=1)
    print(ans)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
