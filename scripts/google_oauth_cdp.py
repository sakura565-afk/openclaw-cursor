"""Complete the Google OAuth consent screen with a CDP-driven Chrome.

The user's real Chrome profile holds a signed-in Google account, but driving
their visible window needs desktop approval. Instead this copies the profile
to a scratch directory, launches that copy with --remote-debugging-port and
finishes the consent screen over the DevTools protocol.

Only the consent click is automated - no credential is ever typed or read.

Usage:
  python scripts/google_oauth_cdp.py --url "<auth url>"
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

CHROME = os.environ.get("CHROME_PATH", "")
PROFILE_SRC = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Google",
                           "Chrome", "User Data")
SKIP = ("Cache", "Code Cache", "GPUCache", "Service Worker", "ShaderCache",
        "GrShaderCache", "Crashpad", "component_crx_cache", "BrowserMetrics")


def _find_chrome() -> str:
    """Chrome binary: env override, then the standard install locations."""
    if CHROME and os.path.exists(CHROME):
        return CHROME
    roots = [os.environ.get("ProgramFiles", ""),
             os.environ.get("ProgramFiles(x86)", ""),
             os.environ.get("LOCALAPPDATA", "")]
    for root in roots:
        if not root:
            continue
        c = os.path.join(root, "Google", "Chrome", "Application", "chrome.exe")
        if os.path.exists(c):
            return c
    return ""


def _copy_profile(dst: str) -> bool:
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst, exist_ok=True)
    src_default = os.path.join(PROFILE_SRC, "Default")
    dst_default = os.path.join(dst, "Default")
    os.makedirs(dst_default, exist_ok=True)
    # Local State holds the encrypted key + profile list
    for name in ("Local State",):
        s = os.path.join(PROFILE_SRC, name)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(dst, name))
    # only what the sign-in flow needs
    for rel in ("Cookies", "Preferences", "Secure Preferences", "Login Data",
                "Web Data", "History", "Local Storage", "Session Storage",
                "Network/Cookies", "Network/Cookies-journal"):
        s = os.path.join(src_default, rel)
        d = os.path.join(dst_default, rel)
        if not os.path.exists(s):
            continue
        os.makedirs(os.path.dirname(d), exist_ok=True)
        try:
            shutil.copy2(s, d)
        except OSError:
            pass
    return True


def _launch(profile_dir: str, port: int) -> int:
    chrome = _find_chrome()
    if not chrome:
        raise SystemExit("Chrome не найден (задай CHROME_PATH)")
    args = [chrome, f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}", "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate,OptimizationGuideModelDownloading",
            "about:blank"]
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                if r.status == 200:
                    return p.pid
        except Exception:
            continue
    return p.pid


def _cdp(port: int):
    """Minimal CDP client over the websocket endpoint of the first page."""
    import asyncio
    import websockets

    async def run():
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
            targets = json.loads(r.read().decode())
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            raise SystemExit("нет page-таргета в Chrome")
        ws_url = page["webSocketDebuggerUrl"]
        out = {"url": AUTH_URL}

        async with websockets.connect(ws_url, max_size=40 * 1024 * 1024) as ws:
            mid = [0]

            async def send(method, params=None):
                mid[0] += 1
                await ws.send(json.dumps({"id": mid[0], "method": method,
                                          "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == mid[0]:
                        if "error" in msg:
                            raise RuntimeError(f"{method}: {msg['error']}")
                        return msg.get("result", {})

            async def evaluate(expr):
                r = await send("Runtime.evaluate", {
                    "expression": expr, "returnByValue": True, "awaitPromise": True})
                res = r.get("result", {})
                if res.get("subtype") == "error":
                    raise RuntimeError(res.get("description"))
                return res.get("value")

            await send("Page.enable")
            await send("Runtime.enable")
            await send("Page.navigate", {"url": out["url"]})

            # wait for the account chooser, then pick the signed-in account
            picked = False
            for _ in range(60):
                await asyncio.sleep(1.0)
                state = await evaluate("""(() => {
                    const t = document.body.innerText || '';
                    const hasChooser = /Выберите аккаунт|Choose an account/i.test(t);
                    const hasConsent = /Разрешить|Allow|не хотите|Continue/i.test(t);
                    const email = (t.match(/[\\w.+-]+@[\\w-]+\\.[\\w.]+/) || [''])[0];
                    return {t: t.slice(0, 400), hasChooser, hasConsent, email,
                            url: location.href};
                })()""")
                out["last_state"] = state
                if not state.get("hasConsent") and not state.get("hasChooser"):
                    continue
                if state.get("hasChooser") and not picked:
                    # click the account row that carries an email
                    clicked = await evaluate("""(() => {
                        const rows = Array.from(document.querySelectorAll('div[role=button], li, [data-identifier]'));
                        const row = rows.find(e => /[\\w.+-]+@[\\w-]+\\.[\\w.]+/.test(e.innerText || ''));
                        if (!row) return 'no-row';
                        row.click();
                        return 'clicked:' + ((row.innerText || '').trim().split('\\n')[0] || '?');
                    })()""")
                    out["account_click"] = clicked
                    picked = True
                    continue
                # consent screen: press the affirmative button
                consent = await evaluate("""(() => {
                    const btns = Array.from(document.querySelectorAll('button, div[role=button]'));
                    const want = btns.find(b => /^(Разрешить|Allow|Да Разрешить|Continue|Продолжить)/i.test((b.innerText||'').trim()));
                    if (!want) return 'no-consent-button';
                    const label = (want.innerText || '').trim();
                    want.click();
                    return 'consented:' + label;
                })()""")
                if consent.startswith("consented"):
                    out["consent"] = consent
                    break

            # wait for the redirect to the loopback server
            for _ in range(30):
                await asyncio.sleep(1.0)
                url = await evaluate("location.href")
                out["final_url"] = url
                if "127.0.0.1" in str(url) or "code=" in str(url):
                    break
            out["body"] = (await evaluate("document.body.innerText || ''")) or ""
            out["url"] = await evaluate("location.href")
        return out

    return asyncio.run(run())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--profile", default="")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    scratch = os.environ.get("TMPDIR") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch")
    profile = a.profile or os.path.join(scratch, "chrome_oauth_profile")

    global AUTH_URL
    AUTH_URL = a.url

    print("копирую профиль Chrome...", flush=True)
    _copy_profile(profile)
    print("запускаю Chrome с CDP...", flush=True)
    pid = _launch(profile, a.port)
    print(f"chrome pid={pid}", flush=True)
    try:
        res = _cdp(a.port)
    finally:
        if not a.keep:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=60)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:3000], flush=True)
    code = ""
    body = res.get("body", "") or ""
    final = res.get("final_url") or ""
    if "code=" in final:
        import urllib.parse
        code = (urllib.parse.parse_qs(urllib.parse.urlparse(final).query).get("code") or [""])[0]
    if not code:
        import re
        m = re.search(r"\b([0-9]+/[0-9A-Za-z_\-]+)\b", body)
        code = m.group(1) if m else ""
    if code:
        print("CODE=" + code, flush=True)
        return 0
    print("код не найден", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
