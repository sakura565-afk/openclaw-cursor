"""One command for the Lyria / Google OAuth handshake.

The flow needs a human: Google shows a consent screen and only the account
owner may press "Allow". Everything else can be prepared, so that the moment
the user is at the machine the whole thing is a single command.

What it does:
  1. stops a stale listener on the port (an old challenge cannot be reused -
     the authorization code Google issues expires in minutes);
  2. starts a fresh PKCE challenge and a loopback listener;
  3. opens the consent page in the default browser, which uses the signed-in
     Google profile;
  4. waits for the redirect, exchanges the code, stores the token;
  5. prints the outcome. The token value is never printed.

Usage:
  python scripts/lyria_oauth_now.py            # do the handshake
  python scripts/lyria_oauth_now.py --dry-run  # only prepare and show the URL
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _listener_pids(port: int) -> list[int]:
    """PIDs listening on the loopback port."""
    out: list[int] = []
    try:
        # netstat writes in the console code page, not UTF-8
        r = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                           capture_output=True, timeout=60)
    except Exception:
        return out
    text = r.stdout.decode("utf-8", errors="replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and f":{port}" in parts[1] and parts[3] == "LISTENING":
            try:
                out.append(int(parts[4]))
            except ValueError:
                pass
    return sorted(set(out))


def _stop_listener(port: int) -> list[int]:
    pids = _listener_pids(port)
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=120)
    if pids:
        time.sleep(1.0)
    return pids


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--timeout", type=int, default=1800,
                    help="how long to wait for the consent click, seconds")
    ap.add_argument("--dry-run", action="store_true",
                    help="prepare the challenge and print the URL, do not open it")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    lb = _load("google_oauth_loopback")

    stopped = _stop_listener(a.port)
    if stopped:
        print(f"остановлен старый слушатель на {a.port}: pid {stopped}")
    if not _port_free(a.port):
        print(f"порт {a.port} всё ещё занят", file=sys.stderr)
        return 3

    port = lb._free_port(a.port)
    redirect = f"http://127.0.0.1:{port}"
    verifier, challenge = lb._pkce()
    state = secrets.token_hex(16)
    os.makedirs(os.path.dirname(lb.STATE_FILE), exist_ok=True)
    import json
    with open(lb.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"verifier": verifier, "challenge": challenge, "state": state,
                   "redirect": redirect, "port": port}, f)
    url = lb.build_url(verifier, challenge, state, redirect)
    print(f"URL: {url}")
    print(f"redirect: {redirect}")

    if a.dry_run:
        print("dry-run: вкладка не открыта, ожидаю ручного запуска")
        return 0

    result: dict[str, str] = {}

    def wait():
        code = lb.serve(port, redirect, a.timeout)
        result["code"] = code

    t = threading.Thread(target=wait, daemon=True)
    t.start()
    time.sleep(0.5)

    if not a.no_browser:
        print("открываю страницу согласия в браузере по умолчанию...")
        webbrowser.open(url)
    print("нужен клик пользователя: «Разрешить». Жду редирект...", flush=True)

    t.join(timeout=a.timeout + 30)
    code = result.get("code", "")
    if not code:
        print("код не получен (таймаут) - пользователь не подтвердил", file=sys.stderr)
        return 1

    print("код получен, обмениваю...", flush=True)
    tok = lb.exchange(code, verifier, redirect)
    path = lb.save_creds(tok)
    print(f"OK: токен получен, refresh_token={'да' if tok.get('refresh_token') else 'нет'}")
    print(f"сохранено: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
