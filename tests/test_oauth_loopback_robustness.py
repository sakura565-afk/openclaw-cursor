"""Regression: the OAuth loopback server must survive junk requests.

The server used to take the code from ANY query string and stop waiting as
soon as one arrived, so a stray health check ended the flow. On top of that
the token exchange did not send client_secret, and Google answered
400 "client_secret is missing".
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "google_oauth_loopback.py"

spec = importlib.util.spec_from_file_location("glo", SCRIPT)
glo = importlib.util.module_from_spec(spec)
sys.modules["glo"] = glo
spec.loader.exec_module(glo)


def test_exchange_sends_the_client_secret():
    """Google answers 400 "client_secret is missing" without it."""
    captured = {}

    class _Resp:
        def read(self):
            return b'{"access_token": "x", "refresh_token": "y"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["body"] = req.data.decode()
        return _Resp()

    glo.CLIENT_ID, glo.CLIENT_SECRET = "ID", "SECRET"
    orig = glo.urllib.request.urlopen
    glo.urllib.request.urlopen = fake_urlopen
    try:
        glo.exchange("CODE", "VERIFIER", "http://127.0.0.1:8765")
    finally:
        glo.urllib.request.urlopen = orig

    body = captured["body"]
    assert "client_secret=" in body, "the token exchange must send client_secret"
    assert "client_id=" in body
    assert "code_verifier=" in body
    assert "grant_type=authorization_code" in body


def test_credentials_are_not_stored_in_the_repository():
    """GitHub push protection blocks any commit that contains the secret."""
    import re

    text = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r'GOCSPX-[A-Za-z0-9_-]+', text), (
        "the OAuth client secret must not be committed"
    )
    assert not re.search(r'\d{12}-[a-z0-9]+\.apps\.googleusercontent\.com', text), (
        "the OAuth client id must not be committed"
    )


def test_credentials_load_from_the_environment(monkeypatch):
    """The credentials are read from the environment."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "123456789-abc.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "GOCSPX-test-secret-value")
    cid, csec = glo._load_client_credentials()
    assert cid and csec, "the client credentials must resolve to a pair"
    assert "@" in cid or "apps.googleusercontent.com" in cid
    assert len(csec) > 8


def test_missing_credentials_stop_the_flow(monkeypatch):
    """Without any credentials the script must fail loudly, not half-start.

    On a CI runner there is neither the env pair nor an installed Gemini CLI,
    so the loader raises SystemExit. That is the documented behaviour - the
    test must not demand that a secret exists on the machine.
    """
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(glo, "GEMINI_CLI_GLOBS", [])
    with pytest.raises(SystemExit):
        glo._load_client_credentials()


def test_server_ignores_requests_without_a_code():
    """A health check must not end the wait."""
    import threading
    import time
    import urllib.request

    port = glo._free_port(0)
    result = {}

    def run():
        result["code"] = glo.serve(port, f"http://127.0.0.1:{port}", timeout_s=12,
                                   expect_state="STATE123")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.0)

    # a request without a code - must be answered, must not stop the server
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            body = r.read().decode()
        assert "Ожидание" in body, "a code-less request must not claim success"
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"the server did not answer a junk request: {exc}")

    # a request with a code but the WRONG state - must also be ignored
    with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/?code=BOGUS&state=WRONG", timeout=10) as r:
        body2 = r.read().decode()
    assert "Ожидание" in body2, "a wrong state must not be accepted"

    # the real callback must be accepted
    with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/?code=REALCODE&state=STATE123", timeout=10) as r:
        body3 = r.read().decode()
    assert "Готово" in body3

    t.join(timeout=15)
    assert result.get("code") == "REALCODE", result
