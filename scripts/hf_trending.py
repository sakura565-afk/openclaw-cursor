#!/usr/bin/env python3
"""HuggingFace trending / newest model watcher (stdlib urllib only)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

USER_AGENT = "openclaw-hf-trending/1.0"
HF_MODELS_API = "https://huggingface.co/api/models"
STATE_SCHEMA_VERSION = 1
DEFAULT_LIMIT = 50
ALERT_CAP = 10
DEFAULT_CHAT_ID = "25979298"

# More-specific prefixes first so ministral/devstral win over mistral.
DEFAULT_FAMILIES: tuple[str, ...] = (
    "devstral",
    "ministral",
    "qwen",
    "gemma",
    "minimax",
    "nemotron",
    "mistral",
    "llama",
    "kimi",
    "deepseek",
    "glm",
)

DEFAULT_TELEGRAM_SCRIPT = Path(
    r"C:\Users\user\.openclaw\skills\telegram-media-send\scripts\telegram_media_send_v2.py"
)

JsonDict = dict[str, Any]
FetchFn = Callable[[str, int], list[JsonDict]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def state_path(root: Path | None = None) -> Path:
    return (root or ROOT) / "data" / "hf_seen.json"


def model_name(model_id: str) -> str:
    text = (model_id or "").strip()
    return text.rsplit("/", 1)[-1] if "/" in text else text


def match_family(model_id: str, families: Sequence[str] | None = None) -> str | None:
    """Case-insensitive prefix match on the model name segment."""
    name = model_name(model_id).lower()
    if not name:
        return None
    for family in families or DEFAULT_FAMILIES:
        prefix = family.strip().lower()
        if prefix and name.startswith(prefix):
            return prefix
    return None


def format_downloads(count: int | float | None) -> str:
    try:
        value = float(count) if count is not None else 0.0
    except (TypeError, ValueError):
        value = 0.0
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".rstrip("0").rstrip(".")
    if value >= 1_000:
        return f"{value / 1_000:.1f}k".rstrip("0").rstrip(".")
    return str(int(value))


def _parse_day(value: Any, fallback: str | None = None) -> str:
    fallback = fallback or utc_now().strftime("%Y-%m-%d")
    text = str(value or "").strip()
    if not text:
        return fallback
    return (text.split("T", 1)[0] if "T" in text else text)[:10]


def hf_token(env: dict[str, str] | None = None) -> str | None:
    e = env if env is not None else os.environ
    return e.get("HF_TOKEN") or e.get("HUGGINGFACE_API_KEY") or e.get("HUGGINGFACE_HUB_TOKEN")


def fetch_models(
    sort: str,
    limit: int = DEFAULT_LIMIT,
    *,
    env: dict[str, str] | None = None,
    opener: Callable[..., Any] | None = None,
) -> list[JsonDict]:
    open_fn = opener or urllib.request.urlopen
    params = urllib.parse.urlencode({"sort": sort, "direction": "-1", "limit": str(limit)})
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = hf_token(env)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{HF_MODELS_API}?{params}", headers=headers, method="GET")
    try:
        with open_fn(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HF API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HF API request failed: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("HF API returned non-list payload")
    return [item for item in payload if isinstance(item, dict)]


def pull_models(
    *,
    sort: str = "both",
    limit: int = DEFAULT_LIMIT,
    families: Sequence[str] | None = None,
    fetch: FetchFn | None = None,
    env: dict[str, str] | None = None,
) -> list[JsonDict]:
    do_fetch = fetch or (lambda s, lim: fetch_models(s, lim, env=env))
    sorts = ("downloads", "createdAt") if sort == "both" else (sort,)
    by_id: dict[str, JsonDict] = {}
    for sort_key in sorts:
        for item in do_fetch(sort_key, limit):
            mid = str(item.get("id") or item.get("modelId") or "").strip()
            if not mid:
                continue
            family = match_family(mid, families)
            if family is None:
                continue
            try:
                downloads = int(item.get("downloads") or 0)
            except (TypeError, ValueError):
                downloads = 0
            day = _parse_day(item.get("lastModified") or item.get("createdAt"))
            prev = by_id.get(mid)
            if prev is None or downloads > int(prev.get("downloads") or 0):
                by_id[mid] = {
                    "id": mid,
                    "family": family,
                    "downloads": downloads,
                    "lastModified": day,
                }
    return sorted(by_id.values(), key=lambda m: (-int(m["downloads"]), m["id"]))


def empty_state(now: datetime | None = None) -> JsonDict:
    stamp = (now or utc_now()).astimezone(timezone.utc).replace(microsecond=0)
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "last_run": stamp.isoformat().replace("+00:00", "Z"),
        "seen": {},
    }


def load_state(path: Path) -> JsonDict | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("state root must be an object")
    seen = data.get("seen") or {}
    if not isinstance(seen, dict):
        raise ValueError("state.seen must be an object")
    return {
        "schema_version": int(data.get("schema_version") or STATE_SCHEMA_VERSION),
        "last_run": data.get("last_run"),
        "seen": {str(k): str(v) for k, v in seen.items()},
    }


def serialize_state(state: JsonDict) -> str:
    payload = {
        "schema_version": int(state.get("schema_version") or STATE_SCHEMA_VERSION),
        "last_run": state.get("last_run"),
        "seen": dict(sorted((state.get("seen") or {}).items())),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def save_state(path: Path, state: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(serialize_state(state), encoding="utf-8")
    os.replace(tmp, path)


def diff_models(fresh: Iterable[JsonDict], seen: dict[str, str]) -> list[JsonDict]:
    out = [m for m in fresh if m.get("id") not in seen]
    return sorted(out, key=lambda m: (-int(m.get("downloads") or 0), str(m.get("id"))))


def format_alert(diff: Sequence[JsonDict], *, cap: int = ALERT_CAP) -> str | None:
    if not diff:
        return None
    shown = list(diff[:cap])
    extra = max(0, len(diff) - len(shown))
    lines = [f"🆕 HuggingFace new models ({len(diff)}):", ""]
    for item in shown:
        lines.append(
            f"• {item['id']} ({item['family']}, "
            f"{format_downloads(item.get('downloads'))} downloads, "
            f"{item.get('lastModified')})"
        )
    if extra:
        lines.extend(["", f"(+{extra} more in data/hf_seen.json)"])
    lines.extend(["", f"Open HF: https://huggingface.co/{shown[0]['id']}"])
    return "\n".join(lines)


def format_human(diff: Sequence[JsonDict]) -> str:
    if not diff:
        return "No new HuggingFace models in watched families."
    lines = [f"New models ({len(diff)}):"]
    for item in diff:
        lines.append(
            f"  - {item['id']} [{item['family']}] "
            f"downloads={item.get('downloads')} lastModified={item.get('lastModified')}"
        )
    return "\n".join(lines)


def send_telegram_alert(
    message: str,
    *,
    script_path: Path | None = None,
    caption: str | None = None,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> JsonDict:
    script = script_path or Path(
        os.environ.get("HF_TRENDING_TELEGRAM_SCRIPT", str(DEFAULT_TELEGRAM_SCRIPT))
    )
    if not script.is_file():
        return {"ok": False, "error": f"telegram script not found: {script}"}
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".txt", prefix="hf_trending_", delete=False
    ) as handle:
        handle.write(message)
        text_path = Path(handle.name)
    try:
        cmd = [
            sys.executable,
            str(script),
            str(text_path),
            "--caption",
            caption or "🆕 HuggingFace new models",
        ]
        env = os.environ.copy()
        env.setdefault("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
        env.setdefault("CHAT_ID", DEFAULT_CHAT_ID)
        completed = run_command(cmd, capture_output=True, text=True, check=False, env=env, timeout=60)
        if completed.returncode == 0:
            return {"ok": True, "command": cmd, "stdout": (completed.stdout or "").strip()[:500]}
        err = (completed.stderr or completed.stdout or "").strip() or f"exit {completed.returncode}"
        return {"ok": False, "error": err, "command": cmd}
    finally:
        try:
            text_path.unlink(missing_ok=True)
        except OSError:
            pass


def run_watcher(
    *,
    root: Path | None = None,
    sort: str = "both",
    limit: int = DEFAULT_LIMIT,
    families: Sequence[str] | None = None,
    send_alert: bool = False,
    update_state: bool = False,
    fetch: FetchFn | None = None,
    env: dict[str, str] | None = None,
    telegram_script: Path | None = None,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    now: datetime | None = None,
) -> JsonDict:
    base = root or ROOT
    path = state_path(base)
    stamp = (now or utc_now()).astimezone(timezone.utc).replace(microsecond=0)
    last_run = stamp.isoformat().replace("+00:00", "Z")
    day = stamp.strftime("%Y-%m-%d")

    fresh = pull_models(sort=sort, limit=limit, families=families, fetch=fetch, env=env)
    prior = load_state(path)
    first_run = prior is None

    if first_run:
        new_models: list[JsonDict] = []
        seen_map = {str(m["id"]): str(m.get("lastModified") or day) for m in fresh}
    else:
        seen_map = dict(prior.get("seen") or {})
        new_models = diff_models(fresh, seen_map)
        for item in new_models:
            seen_map[str(item["id"])] = str(item.get("lastModified") or day)

    state = {"schema_version": STATE_SCHEMA_VERSION, "last_run": last_run, "seen": seen_map}
    alert_text = format_alert(new_models)
    telegram_result: JsonDict | None = None
    if send_alert and alert_text:
        telegram_result = send_telegram_alert(
            alert_text, script_path=telegram_script, run_command=run_command
        )
    elif send_alert:
        telegram_result = {"ok": True, "skipped": True, "reason": "empty diff"}

    state_updated = False
    if update_state:
        save_state(path, state)
        state_updated = True

    return {
        "first_run": first_run,
        "pulled": len(fresh),
        "new_count": len(new_models),
        "diff": new_models,
        "state_path": str(path),
        "state_updated": state_updated,
        "telegram": telegram_result,
        "last_run": last_run,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Watch HuggingFace for new models in watched families.")
    p.add_argument("--send-alert", action="store_true", help="Send Telegram when diff is non-empty.")
    p.add_argument("--update-state", action="store_true", help="Write data/hf_seen.json.")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Models per sort.")
    p.add_argument("--sort", choices=("downloads", "createdAt", "both"), default="both")
    p.add_argument("--family", default=None, help="Override whitelist (comma-separated).")
    p.add_argument("--print-json", action="store_true", help="Print diff as JSON.")
    p.add_argument("--root", type=Path, default=None, help="Repository root.")
    p.add_argument("--telegram-script", type=Path, default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    families = None
    if args.family:
        families = [x.strip() for x in args.family.split(",") if x.strip()]

    report = run_watcher(
        root=args.root,
        sort=args.sort,
        limit=args.limit,
        families=families,
        send_alert=args.send_alert,
        update_state=args.update_state,
        telegram_script=args.telegram_script,
    )

    if args.print_json:
        print(json.dumps(report["diff"], indent=2, ensure_ascii=False))
        return 0

    print(format_human(report["diff"]))
    if report.get("first_run"):
        print("(first run: seeded seen-set; no alerts for the initial pull)", file=sys.stderr)
    if report.get("state_updated"):
        print(f"Updated state: {report['state_path']}", file=sys.stderr)
    if args.send_alert and report.get("telegram"):
        tg = report["telegram"]
        if tg.get("skipped"):
            print("Telegram: skipped (empty diff)", file=sys.stderr)
        elif tg.get("ok"):
            print("Telegram: sent", file=sys.stderr)
        else:
            print(f"Telegram: FAILED — {tg.get('error')}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
