#!/usr/bin/env python3
"""Extract categorized insights from OpenClaw session history.

Uses the OpenClaw session tools ``sessions_list`` and ``sessions_history`` when
available (MCP ``openclaw mcp serve``), otherwise falls back to the local CLI
``openclaw sessions`` and transcript ``*.jsonl`` files under ``~/.openclaw``.

Outputs deduplicated entries under ``<repo>/.learnings/conversations/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterator

# Repo imports when executed as ``python scripts/conversation_extractor.py``
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.optimize_context import repo_root  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
CATEGORIES = frozenset(
    {"LEARNED", "DECISION", "ERROR_FIX", "TOOL_RESULT", "USER_CORRECTION"}
)

USER_CORRECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(actually|not quite|that'?s wrong|incorrect|correction|instead of|"
        r"should be|i meant|no,?\s+(use|try|do)|don'?t use|avoid using)\b.{{0,400}}",
        r"\b(wrong (file|path|command|approach|assumption))\b.{{0,400}}",
    )
)

ERROR_FIX_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(fixed|fix applied|patched|resolved|workaround|root cause|"
        r"the bug was|error was|corrected)\b.{{0,400}}",
        r"(traceback|exception|error):\s*.+",
    )
)

_LEARNED_FLAGS = re.IGNORECASE | re.MULTILINE
_DECISION_FLAGS = re.IGNORECASE | re.MULTILINE

LEARNED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^\s*(?:learning|lesson|takeaway|insight|key\s+learning|pattern)\s*[:\-—]\s*(.+)",
        _LEARNED_FLAGS,
    ),
    re.compile(
        r"\b(new pattern|discovered that|we (?:now )?know|important to remember)\b.{{0,400}}",
        re.IGNORECASE,
    ),
)

DECISION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:decision|resolution|resolved|outcome)\s*[:\-—]\s*(.+)", _DECISION_FLAGS),
    re.compile(
        r"(?:we(?:'ve)?\s+(?:decided|agreed|chose)|let'?s\s+go\s+with)\s+(.+)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:TL;DR|takeaway)s?\s*[:\-—]\s*(.+)", _DECISION_FLAGS),
)

TOOL_OUTCOME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(tool (?:returned|output|result)|exit code\s*\d+|command succeeded|command failed)\b.{{0,400}}",
        r"\[(?:tool|Tool)\s*(?:result|output)[^\]]*\]",
    )
)

STRUCT_TOOL_KEYS = frozenset(
    {
        "tool_calls",
        "toolCalls",
        "tool_use",
        "toolUses",
        "tools",
        "function_call",
        "functionCall",
        "calls",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def normalize_fingerprint(s: str) -> str:
    return normalize_ws(s).lower()


def openclaw_home() -> Path:
    raw = os.environ.get("OPENCLAW_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".openclaw").resolve()


def conversations_dir(root: Path | None = None) -> Path:
    base = (root or repo_root()).resolve()
    return base / ".learnings" / "conversations"


def weekly_dir(root: Path | None = None) -> Path:
    return conversations_dir(root) / "weekly"


# ---------------------------------------------------------------------------
# MCP stdio client (JSON-RPC + Content-Length framing)
# ---------------------------------------------------------------------------


class McpSessionError(RuntimeError):
    pass


class McpJsonRpcClient:
    """Minimal MCP client over stdio for ``tools/call``."""

    def __init__(self, argv: list[str], *, cwd: Path | None = None) -> None:
        self._argv = argv
        self._cwd = cwd
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 1

    def __enter__(self) -> McpJsonRpcClient:
        self._proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=str(self._cwd) if self._cwd else None,
        )
        self._initialize()
        return self

    def __exit__(self, *args: object) -> None:
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def _write_raw(self, payload: str) -> None:
        assert self._proc and self._proc.stdin
        data = payload.encode("utf-8")
        msg = f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8") + data
        self._proc.stdin.buffer.write(msg)
        self._proc.stdin.buffer.flush()

    def _read_one_message(self) -> dict[str, Any]:
        assert self._proc and self._proc.stdout
        headers: dict[str, str] = {}
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise McpSessionError("unexpected EOF reading MCP headers")
            line = line.rstrip("\r\n")
            if line == "":
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        cl = int(headers.get("content-length", "0"))
        if cl <= 0:
            raise McpSessionError(f"invalid Content-Length: {headers!r}")
        body = self._proc.stdout.read(cl)
        if len(body) != cl:
            raise McpSessionError("short read on MCP body")
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise McpSessionError(f"invalid MCP JSON: {e}") from e

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        envelope: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            envelope["params"] = params
        self._write_raw(json.dumps(envelope))
        while True:
            resp = self._read_one_message()
            if resp.get("method") == "notifications/message":
                continue
            if resp.get("id") != req_id:
                continue
            if "error" in resp:
                err = resp["error"]
                raise McpSessionError(str(err))
            return resp.get("result") or {}

    def _initialize(self) -> None:
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "conversation_extractor", "version": "1.0.0"},
            },
        )
        note: dict[str, Any] = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self._write_raw(json.dumps(note))

    def tools_call(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            return result
        content = result.get("content")
        if isinstance(content, list) and content:
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            joined = "\n".join(parts).strip()
            if joined:
                try:
                    return json.loads(joined)
                except json.JSONDecodeError:
                    return joined
        return result


def _mcp_serve_argv() -> list[str] | None:
    raw = os.environ.get("OPENCLAW_MCP_SERVE_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list) and all(isinstance(x, str) for x in data):
                return list(data)
        except json.JSONDecodeError:
            return None
    exe = shutil_which("openclaw")
    if exe:
        return [exe, "mcp", "serve"]
    return None


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def _tool_result_to_obj(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                pass
        return raw
    return raw


# ---------------------------------------------------------------------------
# Session fetch: MCP tools -> CLI -> disk
# ---------------------------------------------------------------------------


def _parse_iso_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def sessions_list_via_mcp(client: McpJsonRpcClient, **kwargs: Any) -> dict[str, Any] | list[Any]:
    merged = dict(kwargs)
    if "limit" not in merged:
        merged["limit"] = 200
    raw = _tool_result_to_obj(client.tools_call("sessions_list", merged))
    if isinstance(raw, dict):
        return raw
    return {"sessions": raw if isinstance(raw, list) else []}


def sessions_history_via_mcp(
    client: McpJsonRpcClient, session_key: str, *, include_tools: bool = True, limit: int = 500
) -> Any:
    args = {"sessionKey": session_key, "includeTools": include_tools, "limit": limit}
    return _tool_result_to_obj(client.tools_call("sessions_history", args))


def _run_openclaw_json(argv: list[str]) -> Any:
    exe = shutil_which("openclaw")
    if not exe:
        raise FileNotFoundError("openclaw executable not found on PATH")
    out = subprocess.run(
        [exe, *argv],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"openclaw exited {out.returncode}")
    return json.loads(out.stdout)


def sessions_list_via_cli(active_minutes: int | None, limit: int) -> dict[str, Any]:
    args = ["sessions", "--all-agents", "--json", "--limit", str(limit)]
    if active_minutes is not None:
        args.extend(["--active", str(max(1, active_minutes))])
    return _run_openclaw_json(args)


def _iter_session_store_paths(home: Path) -> Iterator[Path]:
    agents = home / "agents"
    if not agents.is_dir():
        return
    for agent_dir in sorted(agents.iterdir()):
        if not agent_dir.is_dir():
            continue
        p = agent_dir / "sessions" / "sessions.json"
        if p.is_file():
            yield p


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def sessions_list_via_disk(home: Path) -> dict[str, Any]:
    sessions: list[dict[str, Any]] = []
    for store_path in _iter_session_store_paths(home):
        data = _load_json(store_path)
        if not isinstance(data, dict):
            continue
        agent_guess = store_path.parent.parent.name
        for key, entry in data.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            row = {"key": key, "agentId": entry.get("agentId") or agent_guess}
            row.update({k: entry.get(k) for k in ("updatedAt", "lastInteractionAt", "sessionId", "model")})
            sessions.append(row)
    return {
        "sessions": sessions,
        "source": "disk_sessions.json",
        "count": len(sessions),
    }


def _transcript_path_for_entry(store_dir: Path, entry: dict[str, Any]) -> Path | None:
    sf = entry.get("sessionFile")
    if isinstance(sf, str) and sf.strip():
        p = Path(sf)
        if p.is_absolute():
            return p if p.exists() else None
        cand = (store_dir / p).resolve()
        return cand if cand.exists() else None
    sid = entry.get("sessionId")
    if isinstance(sid, str) and sid.strip():
        cand = store_dir / f"{sid}.jsonl"
        if cand.exists():
            return cand
    return None


def sessions_history_via_disk(session_key: str, home: Path) -> dict[str, Any] | None:
    for store_path in _iter_session_store_paths(home):
        data = _load_json(store_path)
        if not isinstance(data, dict):
            continue
        entry = data.get(session_key)
        if not isinstance(entry, dict):
            continue
        store_dir = store_path.parent
        tpath = _transcript_path_for_entry(store_dir, entry)
        if not tpath:
            return {"messages": [], "sessionKey": session_key, "note": "transcript file not found"}
        messages = _jsonl_to_openai_like_messages(tpath)
        return {"messages": messages, "sessionKey": session_key, "transcriptPath": str(tpath)}
    return None


def _jsonl_to_openai_like_messages(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "message":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            msg = obj
        role = msg.get("role") or msg.get("speaker")
        content = msg.get("content") or msg.get("text") or msg.get("body")
        if isinstance(role, str):
            out.append({"role": role, "content": content})
    return out


def fetch_sessions_list(
    *,
    active_minutes: int | None,
    limit: int,
    home: Path,
) -> tuple[dict[str, Any], str]:
    """Returns (payload, source_tag)."""

    mcp_argv = _mcp_serve_argv()
    if mcp_argv:
        try:
            with McpJsonRpcClient(mcp_argv) as client:
                kwargs: dict[str, Any] = {"limit": limit}
                if active_minutes is not None:
                    kwargs["activeMinutes"] = active_minutes
                data = sessions_list_via_mcp(client, **kwargs)
                if isinstance(data, dict) and (data.get("sessions") or data.get("session") is not None):
                    return data, "mcp:sessions_list"
        except (McpSessionError, FileNotFoundError, subprocess.SubprocessError, OSError):
            pass

    try:
        return sessions_list_via_cli(active_minutes, limit), "cli:openclaw_sessions"
    except (FileNotFoundError, RuntimeError, json.JSONDecodeError):
        pass

    return sessions_list_via_disk(home), "disk:sessions.json"


def fetch_session_history(session_key: str, home: Path, *, limit: int = 800) -> tuple[Any, str]:
    mcp_argv = _mcp_serve_argv()
    if mcp_argv:
        try:
            with McpJsonRpcClient(mcp_argv) as client:
                data = sessions_history_via_mcp(client, session_key, include_tools=True, limit=limit)
                if data:
                    return data, "mcp:sessions_history"
        except (McpSessionError, FileNotFoundError, subprocess.SubprocessError, OSError):
            pass

    disk = sessions_history_via_disk(session_key, home)
    if disk is not None:
        return disk, "disk:jsonl"

    try:
        exe = shutil_which("openclaw")
        if exe:
            out = subprocess.run(
                [exe, "sessions", "export-trajectory", "--session-key", session_key, "--json"],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if out.returncode == 0 and out.stdout.strip():
                return json.loads(out.stdout), "cli:export-trajectory"
    except (json.JSONDecodeError, OSError):
        pass

    return {"messages": []}, "none"


def _session_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("sessions")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    return []


def _row_updated_at(row: dict[str, Any]) -> datetime | None:
    for k in ("updatedAt", "lastInteractionAt", "sessionStartedAt"):
        dt = _parse_iso_dt(row.get(k))
        if dt:
            return dt
    return None


def _row_session_key(row: dict[str, Any]) -> str | None:
    for k in ("key", "sessionKey", "sessionId"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


# ---------------------------------------------------------------------------
# Message flattening (reuse shapes from OpenClaw / Pi transcripts)
# ---------------------------------------------------------------------------


def _stringify_toolish_dict(d: dict[str, Any]) -> str | None:
    fn = d.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return fn["name"]
    for key in ("name", "tool", "toolName", "tool_name", "id"):
        raw = d.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _flatten_content_piece(piece: Any) -> tuple[str, list[str]]:
    tools: list[str] = []
    if piece is None:
        return "", tools
    if isinstance(piece, str):
        return piece, tools
    if isinstance(piece, dict):
        typ = str(piece.get("type") or "").lower()
        if typ in ("tool_use", "tool-use", "toolcall", "function", "tool_invocation"):
            name = _stringify_toolish_dict(piece)
            if name:
                tools.append(name)
            return "", tools
        if typ in ("tool_result", "tool-result"):
            tr = piece.get("content") or piece.get("text") or piece.get("output")
            if isinstance(tr, str) and tr.strip():
                return tr, tools
            return "", tools
        if isinstance(piece.get("text"), str):
            return piece["text"], tools
        if isinstance(piece.get("content"), str):
            return piece["content"], tools
        nested = piece.get("text") or piece.get("content")
        txt, subt = _flatten_content_piece(nested)
        tools.extend(subt)
        return txt, tools
    if isinstance(piece, list):
        parts: list[str] = []
        for item in piece:
            t, tt = _flatten_content_piece(item)
            if t.strip():
                parts.append(t.strip())
            tools.extend(tt)
        return "\n".join(parts), tools
    return str(piece), tools


def _tool_names_from_calls(value: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(value, list):
        return names
    for item in value:
        if isinstance(item, dict):
            t = _stringify_toolish_dict(item)
            if t:
                names.append(t)
            fn = item.get("function")
            if isinstance(fn, dict):
                n = fn.get("name")
                if isinstance(n, str) and n.strip():
                    names.append(n.strip())
    return names


def segments_from_messages(messages: list[Any]) -> list[tuple[int, str | None, str]]:
    segments: list[tuple[int, str | None, str]] = []
    for i, raw in enumerate(messages):
        if not isinstance(raw, dict):
            continue
        turn = i + 1
        for key in ("turn", "step", "message_index", "index"):
            v = raw.get(key)
            if isinstance(v, int) and v > 0:
                turn = v
            elif isinstance(v, str) and v.isdigit():
                turn = int(v)

        role = raw.get("role") or raw.get("speaker") or raw.get("from")
        rl = role.lower().strip() if isinstance(role, str) else ""

        blob_tools: list[str] = []
        for key in STRUCT_TOOL_KEYS:
            if key in raw:
                blob_tools.extend(_tool_names_from_calls(raw[key]))

        text = ""
        c = raw.get("content") or raw.get("text") or raw.get("body")
        frag, inner_tools = _flatten_content_piece(c)
        blob_tools.extend(inner_tools)
        text += frag

        for tn in blob_tools:
            tn = tn.strip()
            if tn:
                segments.append((turn, "tool", tn))

        if text.strip():
            eff = "tool_output" if rl == "tool" else (rl if rl else None)
            segments.append((turn, eff, text.strip()))

    return segments


def history_to_messages(history: Any) -> list[dict[str, Any]]:
    if isinstance(history, dict):
        for key in ("messages", "conversation", "transcript", "history"):
            inner = history.get(key)
            if isinstance(inner, list):
                return [m for m in inner if isinstance(m, dict)]
    if isinstance(history, list):
        return [m for m in history if isinstance(m, dict)]
    return []


# ---------------------------------------------------------------------------
# Insight extraction + dedupe
# ---------------------------------------------------------------------------


@dataclass
class Extraction:
    category: str
    text: str
    session_key: str
    agent_id: str | None
    turn: int | None
    source: str

    def as_dict(self, eid: str, created: str) -> dict[str, Any]:
        return {
            "id": eid,
            "category": self.category,
            "text": self.text,
            "session_key": self.session_key,
            "agent_id": self.agent_id,
            "turn": self.turn,
            "source": self.source,
            "created_at_utc": created,
        }


def _snippet_from_match(m: re.Match[str], cap: int = 400) -> str:
    s = m.group(0).strip()
    if len(s) > cap:
        return s[: cap - 3] + "..."
    return s


def _match_patterns_on_text(
    text: str, patterns: tuple[re.Pattern[str], ...], category: str, *, session_key: str, agent_id: str | None, turn: int | None, source: str
) -> list[Extraction]:
    found: list[Extraction] = []
    for pat in patterns:
        for m in pat.finditer(text):
            found.append(
                Extraction(
                    category=category,
                    text=_snippet_from_match(m),
                    session_key=session_key,
                    agent_id=agent_id,
                    turn=turn,
                    source=source,
                )
            )
    return found


def extract_from_segments(
    segments: list[tuple[int, str | None, str]],
    *,
    session_key: str,
    agent_id: str | None,
    source: str,
) -> list[Extraction]:
    out: list[Extraction] = []

    for turn, role, text in segments:
        rl = (role or "").lower()

        if rl == "user":
            out.extend(
                _match_patterns_on_text(
                    text, USER_CORRECTION_PATTERNS, "USER_CORRECTION", session_key=session_key, agent_id=agent_id, turn=turn, source=source
                )
            )

        if rl in {"user", "assistant", "agent", ""}:
            out.extend(
                _match_patterns_on_text(
                    text, ERROR_FIX_PATTERNS, "ERROR_FIX", session_key=session_key, agent_id=agent_id, turn=turn, source=source
                )
            )
            out.extend(
                _match_patterns_on_text(
                    text, LEARNED_PATTERNS, "LEARNED", session_key=session_key, agent_id=agent_id, turn=turn, source=source
                )
            )
            out.extend(
                _match_patterns_on_text(
                    text, DECISION_PATTERNS, "DECISION", session_key=session_key, agent_id=agent_id, turn=turn, source=source
                )
            )

        if rl == "tool_output" or rl == "tool":
            out.extend(
                _match_patterns_on_text(
                    text, TOOL_OUTCOME_PATTERNS, "TOOL_RESULT", session_key=session_key, agent_id=agent_id, turn=turn, source=source
                )
            )
            if rl == "tool_output" and len(text) > 40:
                out.append(
                    Extraction(
                        category="TOOL_RESULT",
                        text=text if len(text) <= 400 else text[:397] + "...",
                        session_key=session_key,
                        agent_id=agent_id,
                        turn=turn,
                        source=source + ":tool_output",
                    )
                )

        if rl == "tool":
            name = normalize_ws(text)
            if name:
                out.append(
                    Extraction(
                        category="TOOL_RESULT",
                        text=f"Tool invoked: {name}",
                        session_key=session_key,
                        agent_id=agent_id,
                        turn=turn,
                        source=source + ":tool_invocation",
                    )
                )

    return out


def entry_fingerprint(category: str, text: str) -> str:
    payload = {"c": category, "t": normalize_fingerprint(text)}
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_fingerprint(a), normalize_fingerprint(b)).ratio()


def dedupe_extractions(items: list[Extraction], *, threshold: float = 0.92) -> list[Extraction]:
    kept: list[Extraction] = []
    texts: list[str] = []
    for ex in items:
        if ex.category not in CATEGORIES:
            continue
        dup = False
        for prev in texts:
            if similarity(ex.text, prev) >= threshold:
                dup = True
                break
        if dup:
            continue
        kept.append(ex)
        texts.append(ex.text)
    return kept


def materialize_entries(items: list[Extraction], created: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for ex in items:
        eid = entry_fingerprint(ex.category, ex.text)
        if eid in seen_ids:
            eid = hashlib.sha1(f"{eid}:{uuid.uuid4().hex}".encode()).hexdigest()[:16]
        seen_ids.add(eid)
        out.append(ex.as_dict(eid, created))
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def daily_file_path(root: Path, day: date) -> Path:
    return conversations_dir(root) / f"{day.isoformat()}.json"


def load_daily(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "date": path.stem, "extractions": []}
    data = _load_json(path)
    if not isinstance(data, dict):
        return {"schema_version": SCHEMA_VERSION, "date": path.stem, "extractions": []}
    ex = data.get("extractions")
    if not isinstance(ex, list):
        data["extractions"] = []
    return data


def dedupe_persisted_entries(entries: list[dict[str, Any]], *, threshold: float = 0.92) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    fingerprints: list[tuple[str, str]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        cat = e.get("category")
        text = e.get("text")
        if not isinstance(cat, str) or not isinstance(text, str):
            continue
        fp = normalize_fingerprint(text)
        dup = False
        for prev_cat, prev_fp in fingerprints:
            if prev_cat != cat:
                continue
            if SequenceMatcher(None, fp, prev_fp).ratio() >= threshold:
                dup = True
                break
        if dup:
            continue
        kept.append(e)
        fingerprints.append((cat, fp))
    return kept


def merge_daily_into_day(root: Path, day: date, new_entries: list[dict[str, Any]], *, fetch_source: str) -> Path:
    conversations_dir(root).mkdir(parents=True, exist_ok=True)
    path = daily_file_path(root, day)
    existing = load_daily(path)
    old_ids = {e.get("id") for e in existing.get("extractions", []) if isinstance(e, dict) and isinstance(e.get("id"), str)}

    merged_list = [e for e in existing.get("extractions", []) if isinstance(e, dict)]
    created = utc_now_iso()
    for e in new_entries:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if isinstance(eid, str) and eid in old_ids:
            continue
        merged_list.append(e)
        if isinstance(eid, str):
            old_ids.add(eid)

    merged_list = dedupe_persisted_entries(merged_list)

    doc = {
        "schema_version": SCHEMA_VERSION,
        "date": day.isoformat(),
        "updated_at_utc": created,
        "fetch_source": fetch_source,
        "extractions": merged_list,
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def iter_learning_json_files(root: Path) -> Iterator[Path]:
    base = conversations_dir(root)
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*.json")):
        if path.is_file():
            yield path


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_extract(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve() if args.repo else repo_root().resolve()
    home = openclaw_home()
    days = max(1, int(args.days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    active_minutes = days * 24 * 60

    payload, fetch_tag = fetch_sessions_list(active_minutes=active_minutes, limit=int(args.limit), home=home)
    rows = _session_rows(payload)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        ts = _row_updated_at(row)
        if ts is None or ts >= cutoff:
            filtered.append(row)
    filtered.sort(key=lambda r: _row_updated_at(r) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    filtered = filtered[: int(args.max_sessions)]

    all_extractions: list[Extraction] = []
    for row in filtered:
        sk = _row_session_key(row)
        if not sk:
            continue
        agent_id = row.get("agentId") if isinstance(row.get("agentId"), str) else None
        hist, hist_tag = fetch_session_history(sk, home, limit=int(args.message_limit))
        messages = history_to_messages(hist)
        segments = segments_from_messages(messages)
        all_extractions.extend(
            extract_from_segments(
                segments,
                session_key=sk,
                agent_id=agent_id,
                source=f"{fetch_tag}+{hist_tag}",
            )
        )

    all_extractions = dedupe_extractions(all_extractions)
    created = utc_now_iso()
    entries = materialize_entries(all_extractions, created)
    day = datetime.now(timezone.utc).date()
    out_path = merge_daily_into_day(root, day, entries, fetch_source=fetch_tag)
    print(f"Wrote {out_path} ({len(entries)} extractions this run from {len(filtered)} sessions)")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve() if args.repo else repo_root().resolve()
    q = args.query.lower()
    hits = 0
    for path in iter_learning_json_files(root):
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        for ex in data.get("extractions", []):
            if not isinstance(ex, dict):
                continue
            text = ex.get("text")
            if isinstance(text, str) and q in text.lower():
                hits += 1
                cat = ex.get("category", "")
                sk = ex.get("session_key", "")
                print(f"{path.name}\t{cat}\t{sk}\t{text[:200]}")
    print(f"Total matches: {hits}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve() if args.repo else repo_root().resolve()
    by_cat: Counter[str] = Counter()
    files = 0
    entries = 0
    for path in iter_learning_json_files(root):
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        ex_list = data.get("extractions")
        if not isinstance(ex_list, list) or not ex_list:
            continue
        files += 1
        for ex in ex_list:
            if not isinstance(ex, dict):
                continue
            entries += 1
            c = ex.get("category")
            if isinstance(c, str):
                by_cat[c] += 1
    print(f"JSON files with data: {files}")
    print(f"Total extractions: {entries}")
    for k, v in by_cat.most_common():
        print(f"  {k}: {v}")
    return 0


def _parse_day(s: str) -> date:
    return date.fromisoformat(s)


def week_range_containing(day: date) -> tuple[date, date]:
    """Monday–Sunday week containing ``day`` (ISO week)."""

    monday = day - timedelta(days=day.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def cmd_merge(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve() if args.repo else repo_root().resolve()
    weekly_dir(root).mkdir(parents=True, exist_ok=True)

    if args.week_start:
        start = _parse_day(args.week_start)
        monday, sunday = week_range_containing(start)
    else:
        today = datetime.now(timezone.utc).date()
        monday, sunday = week_range_containing(today)

    collected: list[Extraction] = []
    d = monday
    while d <= sunday:
        doc = load_daily(daily_file_path(root, d))
        for ex in doc.get("extractions", []):
            if not isinstance(ex, dict):
                continue
            cat = ex.get("category")
            text = ex.get("text")
            sk = ex.get("session_key")
            if not isinstance(cat, str) or not isinstance(text, str) or not isinstance(sk, str):
                continue
            aid = ex.get("agent_id")
            if not isinstance(aid, str):
                aid = ex.get("agentId") if isinstance(ex.get("agentId"), str) else None
            collected.append(
                Extraction(
                    category=cat,
                    text=text,
                    session_key=sk,
                    agent_id=aid,
                    turn=ex.get("turn") if isinstance(ex.get("turn"), int) else None,
                    source="merge_weekly",
                )
            )
        d += timedelta(days=1)

    collected = dedupe_extractions(collected)
    created = utc_now_iso()
    entries = materialize_entries(collected, created)
    name = f"week_{monday.isoformat()}_{sunday.isoformat()}.json"
    out = weekly_dir(root) / name
    out.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "week_start": monday.isoformat(),
                "week_end": sunday.isoformat(),
                "generated_at_utc": created,
                "extractions": entries,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out} ({len(entries)} extractions)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenClaw conversation insight extractor.")
    p.add_argument("--repo", type=Path, default=None, help="Repository root (default: auto-detect).")
    sub = p.add_subparsers(dest="command", required=True)

    pe = sub.add_parser("extract", help="Fetch sessions and write today's JSON digest.")
    pe.add_argument("--days", type=int, default=7, help="Only sessions touched in the last N days (default 7).")
    pe.add_argument("--limit", type=int, default=200, help="Max rows from sessions_list / CLI list.")
    pe.add_argument("--max-sessions", type=int, default=40, help="Cap history fetches per run.")
    pe.add_argument("--message-limit", type=int, default=800, help="Bound for sessions_history messages.")
    pe.set_defaults(func=cmd_extract)

    ps = sub.add_parser("search", help="Search stored extraction text.")
    ps.add_argument("query", help="Substring query (case-insensitive).")
    ps.set_defaults(func=cmd_search)

    pst = sub.add_parser("stats", help="Print category counts across stored JSON.")
    pst.set_defaults(func=cmd_stats)

    pm = sub.add_parser("merge", help="Consolidate daily JSON files into a weekly summary.")
    pm.add_argument(
        "--week-start",
        type=str,
        default=None,
        help="ISO date (YYYY-MM-DD) within the target week; default: ISO week (Mon–Sun) containing today.",
    )
    pm.set_defaults(func=cmd_merge)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fn: Callable[[argparse.Namespace], int] = args.func
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
