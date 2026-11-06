"""Google OAuth (PKCE) with a loopback redirect, so the code lands locally.

The desktop client id that ships with the Gemini CLI accepts a loopback
redirect (http://127.0.0.1:<port>) for installed-app flows. Instead of the
user copying a code out of a browser tab, a tiny local server captures the
redirect, writes the code to disk and answers the browser.

Usage:
  python scripts/google_oauth_loopback.py --port 8765
  python scripts/google_oauth_loopback.py --port 8765 --code <code>   # manual fallback
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = ""
CLIENT_SECRET = ""
SCOPE = " ".join([
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
])
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

STATE_FILE = os.path.join(os.path.expanduser("~"), ".gemini", "pkce_loopback.json")
CRED_DIR = os.path.join(os.path.expanduser("~"), ".hermes", "oauth")

# The OAuth client id and secret are NOT stored in this repository: GitHub
# push protection blocks any commit that contains them. They are read at
# runtime instead - from the environment, or from the installed Gemini CLI,
# which is where they come from in the first place.
GEMINI_CLI_GLOBS = [
    os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm",
                 "node_modules", "@google", "gemini-cli", "bundle", "*.js"),
    "/usr/lib/node_modules/@google/gemini-cli/bundle/*.js",
    "/usr/local/lib/node_modules/@google/gemini-cli/bundle/*.js",
]


def _load_client_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret) from env or the installed Gemini CLI."""
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    csec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if cid and csec:
        return cid, csec

    import glob
    import re

    for pattern in GEMINI_CLI_GLOBS:
        for path in glob.glob(pattern):
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if not cid:
                m = re.search(r'OAUTH_CLIENT_ID\s*=\s*"([^"]+)"', text)
                if m:
                    cid = m.group(1)
            if not csec:
                m = re.search(r'OAUTH_CLIENT_SECRET\s*=\s*"([^"]+)"', text)
                if m:
                    csec = m.group(1)
            if cid and csec:
                return cid, csec
    raise SystemExit(
        "не найдены OAuth-данные клиента. Задай GOOGLE_OAUTH_CLIENT_ID и "
        "GOOGLE_OAUTH_CLIENT_SECRET или установи @google/gemini-cli."
    )


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_url(verifier: str, challenge: str, state: str, redirect: str) -> str:
    q = urllib.parse.urlencode({
        "redirect_uri": redirect,
        "access_type": "offline",
        "scope": SCOPE,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
        "response_type": "code",
        "client_id": CLIENT_ID,
        "prompt": "consent",
    })
    return f"{AUTH_URL}?{q}"


def exchange(code: str, verifier: str, redirect: str) -> dict:
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code}: {e.read().decode()[:400]}") from None


def save_creds(tok: dict) -> str:
    os.makedirs(CRED_DIR, exist_ok=True)
    payload = {
        "access_token": tok.get("access_token"),
        "refresh_token": tok.get("refresh_token"),
        "token_type": tok.get("token_type", "Bearer"),
        "scope": SCOPE,
        "expiry_date": int(time.time() * 1000) + int(tok.get("expires_in", 3600)) * 1000,
    }
    for d in (os.path.join(os.path.expanduser("~"), ".gemini"), CRED_DIR):
        try:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "oauth_creds.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=1)
        except OSError:
            pass
    return os.path.join(CRED_DIR, "oauth_creds.json")


def _free_port(preferred: int) -> int:
    for p in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return s.getsockname()[1]
            except OSError:
                continue
    raise SystemExit("не удалось занять порт")


def serve(port: int, redirect: str, timeout_s: int, expect_state: str = "") -> str:
    """Wait for the OAuth redirect and return the authorization code.

    Only a request that carries BOTH a code and the matching state counts.
    Anything else - a stray browser hit, a health check, a favicon request -
    must not end the wait, and must not make the process exit.
    """
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = (qs.get("code") or [""])[0]
            state = (qs.get("state") or [""])[0]
            if code and (not expect_state or state == expect_state):
                got["code"] = code
                body = ("<html><body style='font-family:sans-serif;padding:40px'>"
                        "<h2>Готово</h2><p>Авторизация выполнена. Можно закрыть вкладку.</p>"
                        "</body></html>").encode()
            else:
                # Not the callback (or a stale state): answer, but keep waiting.
                body = ("<html><body style='font-family:sans-serif;padding:40px'>"
                        "<h2>Ожидание авторизации</h2>"
                        "<p>Этот запрос не содержит кода авторизации. "
                        "Откройте ссылку из консоли.</p></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    srv.timeout = 1.0
    deadline = time.time() + timeout_s
    while time.time() < deadline and "code" not in got:
        srv.handle_request()
    srv.server_close()
    return got.get("code", "")


def main() -> int:
    global CLIENT_ID, CLIENT_SECRET
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--code", default="")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--print-url-only", action="store_true")
    a = ap.parse_args()

    if a.code:
        st = json.load(open(STATE_FILE, encoding="utf-8"))
        CLIENT_ID, CLIENT_SECRET = _load_client_credentials()
        code = a.code.strip()
        if "code=" in code:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(code).query)
            code = (qs.get("code") or [""])[0]
        tok = exchange(code, st["verifier"], st["redirect"])
        path = save_creds(tok)
        print(f"OK: токен получен, refresh_token={'да' if tok.get('refresh_token') else 'нет'}")
        print(f"сохранено: {path}")
        return 0

    port = _free_port(a.port)
    redirect = f"http://127.0.0.1:{port}"
    verifier, challenge = _pkce()
    state = secrets.token_hex(16)
    CLIENT_ID, CLIENT_SECRET = _load_client_credentials()
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"verifier": verifier, "challenge": challenge, "state": state,
                   "redirect": redirect, "port": port}, f)
    url = build_url(verifier, challenge, state, redirect)
    print("URL:", url, flush=True)
    print(f"redirect: {redirect}", flush=True)
    if a.print_url_only:
        return 0

    print("Жду редирект с кодом...", flush=True)
    code = serve(port, redirect, a.timeout, expect_state=state)
    if not code:
        print("код не получен (таймаут)", flush=True)
        return 1
    print("код получен, обмениваю...", flush=True)
    try:
        tok = exchange(code, verifier, redirect)
    except SystemExit as e:
        # A failed exchange must not be fatal: the state file keeps the
        # verifier, so the user can retry with --code without a new flow.
        print(f"обмен не удался: {e}", flush=True)
        print("Можно повторить: python scripts/google_oauth_loopback.py --code <КОД>", flush=True)
        return 1
    path = save_creds(tok)
    print(f"OK: токен получен, refresh_token={'да' if tok.get('refresh_token') else 'нет'}")
    print(f"сохранено: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
