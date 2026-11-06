"""Long-lived Google OAuth (PKCE) for Gemini Code Assist / AI Studio.

Why not the Gemini CLI: its manual flow prints a URL but expires after five
minutes, which is useless when the user is away from the keyboard. This
script keeps the request alive indefinitely and prints the URL once.

The client id is the public desktop client shipped in the Gemini CLI. No
client secret is involved: the flow is PKCE, so the code verifier stays
local and the refresh token is what we keep.

Usage:
  python scripts/google_oauth_pkce.py --url-only
  python scripts/google_oauth_pkce.py --code <authorization-code>

The refresh token is written to ~/.gemini/oauth_creds.json (the path the
Gemini CLI reads) and to ~/.hermes/oauth/google_creds.json.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from google_oauth_loopback import _load_client_credentials  # noqa: E402

CLIENT_ID = ""  # resolved at runtime by _load_client_credentials()
REDIRECT_URI = "https://codeassist.google.com/authcode"
SCOPE = " ".join([
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
])
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

STATE_FILE = os.path.join(os.path.expanduser("~"), ".gemini", "pkce_state.json")
CRED_DIR = os.path.join(os.path.expanduser("~"), ".hermes", "oauth")


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_url(verifier: str, challenge: str, state: str) -> str:
    q = urllib.parse.urlencode({
        "redirect_uri": REDIRECT_URI,
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


def exchange(code: str, verifier: str) -> dict:
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
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


def main() -> int:
    global CLIENT_ID
    CLIENT_ID, _ = _load_client_credentials()
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="")
    ap.add_argument("--url-only", action="store_true")
    ap.add_argument("--state-file", default=STATE_FILE)
    a = ap.parse_args()

    if a.code:
        st = json.load(open(a.state_file, encoding="utf-8"))
        code = a.code.strip()
        if "code=" in code:  # a pasted redirect URL
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(code).query)
            code = (qs.get("code") or [""])[0]
        if not code:
            raise SystemExit("не найден код")
        tok = exchange(code, st["verifier"])
        path = save_creds(tok)
        print(f"OK: токен получен, refresh_token={'да' if tok.get('refresh_token') else 'нет'}")
        print(f"сохранено: {path}")
        print(f"expires_in: {tok.get('expires_in')} с")
        return 0

    verifier, challenge = _pkce()
    state = secrets.token_hex(16)
    os.makedirs(os.path.dirname(a.state_file), exist_ok=True)
    with open(a.state_file, "w", encoding="utf-8") as f:
        json.dump({"verifier": verifier, "challenge": challenge, "state": state}, f)
    url = build_url(verifier, challenge, state)
    print("URL:", url, flush=True)
    print("Открой ссылку, нажми «Разрешить», скопируй код и пришли его сюда.", flush=True)
    if a.url_only:
        return 0
    # keep the state file alive; the URL itself has no server-side expiry here
    for _ in range(60 * 60 * 24):
        time.sleep(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
