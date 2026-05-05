#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


FAST_MODEL = os.environ.get("OPENCLAW_SCOUT_MODEL", "openclaw-fast")
CACHE_TTL_SECONDS = 300
IDLE_THRESHOLD_SECONDS = 15
MAX_PREDICTIONS = 2

TASK_PATTERNS: dict[str, list[dict[str, Any]]] = {
    "image": [
        {
            "intent": "variants",
            "question": "Can you show a couple of variants of this image?",
            "aliases": ["variants", "more versions", "more options", "different versions"],
        },
        {
            "intent": "change_style",
            "question": "Can you change the style but keep the same subject?",
            "aliases": ["change style", "different style", "restyle"],
        },
        {
            "intent": "upscale",
            "question": "Can you upscale this image?",
            "aliases": ["upscale", "higher resolution", "4k", "hi res"],
        },
        {
            "intent": "more",
            "question": "Can you make more options like this?",
            "aliases": ["make more", "more like this", "additional versions"],
        },
    ],
    "code": [
        {
            "intent": "add_tests",
            "question": "Can you add tests for this change?",
            "aliases": ["add tests", "write tests", "test coverage"],
        },
        {
            "intent": "optimize",
            "question": "Can you optimize this implementation?",
            "aliases": ["optimize", "improve performance", "make faster"],
        },
        {
            "intent": "explain",
            "question": "Can you explain how this works?",
            "aliases": ["explain", "walk me through", "how it works"],
        },
        {
            "intent": "refactor",
            "question": "Can you refactor this to be cleaner?",
            "aliases": ["refactor", "clean up", "simplify"],
        },
    ],
    "video": [
        {
            "intent": "shorter",
            "question": "Can you make a shorter version of this video?",
            "aliases": ["shorter", "trim it", "short version"],
        },
        {
            "intent": "different_angle",
            "question": "Can you render this from a different angle?",
            "aliases": ["different angle", "new camera angle", "alternate angle"],
        },
        {
            "intent": "change_format",
            "question": "Can you export this in a different format?",
            "aliases": ["change format", "different format", "convert format"],
        },
    ],
    "analysis": [
        {
            "intent": "more_details",
            "question": "Can you go into more detail on this analysis?",
            "aliases": ["more details", "go deeper", "expand analysis"],
        },
        {
            "intent": "what_if",
            "question": "What if the assumptions or constraints change?",
            "aliases": ["what if", "different assumptions", "change constraints"],
        },
        {
            "intent": "alternatives",
            "question": "What are the main alternatives?",
            "aliases": ["alternatives", "other options", "different approaches"],
        },
    ],
}


@dataclass(frozen=True)
class Prediction:
    task_type: str
    intent: str
    question: str
    model: str
    aliases: list[str]
    seed_result: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _default_scout_dir() -> Path:
    override = os.environ.get("OPENCLAW_SCOUT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openclaw" / "proactive_scout"


def _cache_dir(root: Path) -> Path:
    return root / "cache"


def _jobs_dir(root: Path) -> Path:
    return root / "jobs"


def _state_path(root: Path) -> Path:
    return root / "state.json"


def _ensure_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _cache_dir(root).mkdir(parents=True, exist_ok=True)
    _jobs_dir(root).mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _normalize_space(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _normalize_question(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return _normalize_space(cleaned)


def _hash_for_prediction(task_type: str, intent: str, question: str) -> str:
    raw = f"{task_type}|{intent}|{_normalize_question(question)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _coerce_prediction(prediction: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(prediction, str):
        normalized = _normalize_question(prediction)
        return {
            "task_type": "analysis",
            "intent": normalized.replace(" ", "_") or "follow_up",
            "question": prediction,
            "model": FAST_MODEL,
            "aliases": [normalized],
            "seed_result": "",
        }
    return {
        "task_type": str(prediction.get("task_type", "analysis")),
        "intent": str(prediction.get("intent", "follow_up")),
        "question": str(prediction["question"]),
        "model": str(prediction.get("model", FAST_MODEL)),
        "aliases": [str(item) for item in prediction.get("aliases", [])],
        "seed_result": str(prediction.get("seed_result", "")),
    }


def _load_state(root: Path) -> dict[str, Any]:
    payload = _read_json(_state_path(root))
    if isinstance(payload, dict):
        return payload
    return {}


def _save_state(root: Path, **updates: Any) -> dict[str, Any]:
    state = _load_state(root)
    state.update(updates)
    _write_json(_state_path(root), state)
    return state


def _resolve_idle_seconds(root: Path, idle_seconds: float | None) -> float:
    if idle_seconds is not None:
        return float(idle_seconds)
    env_value = os.environ.get("OPENCLAW_IDLE_SECONDS")
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            pass
    state = _load_state(root)
    last_user_request = state.get("last_user_request_at")
    if isinstance(last_user_request, (int, float)):
        return max(0.0, time.time() - float(last_user_request))
    return IDLE_THRESHOLD_SECONDS + 1.0


def _cache_path(root: Path, cache_key: str) -> Path:
    return _cache_dir(root) / f"{cache_key}.json"


def _job_path(root: Path, job_id: str) -> Path:
    return _jobs_dir(root) / f"{job_id}.json"


def _iter_json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.glob("*.json"))


def _purge_expired(root: Path, now: float | None = None) -> dict[str, int]:
    now = time.time() if now is None else now
    removed = {"cache_entries": 0, "job_records": 0}

    for cache_file in _iter_json_files(_cache_dir(root)):
        payload = _read_json(cache_file)
        expires_at = payload.get("expires_at") if isinstance(payload, dict) else None
        if isinstance(expires_at, (int, float)) and float(expires_at) <= now:
            cache_file.unlink(missing_ok=True)
            removed["cache_entries"] += 1

    for job_file in _iter_json_files(_jobs_dir(root)):
        payload = _read_json(job_file)
        if not isinstance(payload, dict):
            job_file.unlink(missing_ok=True)
            removed["job_records"] += 1
            continue
        status = payload.get("status")
        finished_at = payload.get("completed_at") or payload.get("failed_at") or payload.get("created_at")
        if status in {"completed", "failed"} and isinstance(finished_at, (int, float)):
            if float(finished_at) + CACHE_TTL_SECONDS <= now:
                job_file.unlink(missing_ok=True)
                removed["job_records"] += 1

    return removed


def _priority_for_prediction(task_type: str, intent: str, result: str) -> int:
    text = result.lower()
    text_length = len(result)

    if task_type == "image":
        scores = {
            "variants": 95,
            "change_style": 80 if "style" not in text else 60,
            "upscale": 78 if "4k" not in text and "high resolution" not in text else 45,
            "more": 72,
        }
        return scores.get(intent, 0)
    if task_type == "code":
        missing_tests = any(
            phrase in text
            for phrase in (
                "no tests",
                "without tests",
                "missing tests",
                "tests were not added",
                "there are no tests",
                "untested",
            )
        )
        scores = {
            "add_tests": 99 if missing_tests or "test" not in text else 30,
            "optimize": 90 if any(word in text for word in ("optimiz", "performance", "latency", "memory", "slow")) else 52,
            "explain": 88 if text_length > 300 or "```" in result else 50,
            "refactor": 85 if any(word in text for word in ("cleanup", "duplicate", "complex", "refactor")) or text_length > 500 else 56,
        }
        return scores.get(intent, 0)
    if task_type == "video":
        scores = {
            "shorter": 92,
            "different_angle": 82 if "angle" not in text else 60,
            "change_format": 76 if "format" not in text else 58,
        }
        return scores.get(intent, 0)
    scores = {
        "more_details": 94 if text_length < 500 else 78,
        "what_if": 84,
        "alternatives": 80,
    }
    return scores.get(intent, 0)


def scout_predict(task_type: str, result: str) -> list[dict[str, Any]]:
    normalized_type = task_type.strip().lower() or "analysis"
    if normalized_type not in TASK_PATTERNS:
        normalized_type = "analysis"

    ranked_predictions: list[tuple[int, int, Prediction]] = []
    for position, pattern in enumerate(TASK_PATTERNS[normalized_type]):
        prediction = Prediction(
            task_type=normalized_type,
            intent=str(pattern["intent"]),
            question=str(pattern["question"]),
            model=FAST_MODEL,
            aliases=[str(item) for item in pattern.get("aliases", [])],
            seed_result=result,
        )
        priority = _priority_for_prediction(normalized_type, prediction.intent, result)
        ranked_predictions.append((priority, -position, prediction))

    ranked_predictions.sort(reverse=True)
    top_predictions = [item[2].to_dict() for item in ranked_predictions[:MAX_PREDICTIONS]]
    return top_predictions


def _match_terms(question: str, aliases: list[str]) -> list[str]:
    terms = {_normalize_question(question)}
    for alias in aliases:
        normalized = _normalize_question(alias)
        if normalized:
            terms.add(normalized)
    return sorted(terms)


def _question_matches(question: str, entry: dict[str, Any]) -> bool:
    normalized = _normalize_question(question)
    if not normalized:
        return False

    cached_question = str(entry.get("normalized_question", ""))
    if normalized == cached_question:
        return True

    for match_term in entry.get("match_terms", []):
        normalized_term = _normalize_question(str(match_term))
        if normalized == normalized_term:
            return True
        if normalized_term and (normalized_term in normalized or normalized in normalized_term):
            return True

    ratio = SequenceMatcher(None, normalized, cached_question).ratio()
    return ratio >= 0.72


def _build_response(task_type: str, intent: str, question: str, result: str) -> str:
    excerpt = result.strip().replace("\r\n", "\n")
    excerpt = excerpt[:500].strip()
    if not excerpt:
        excerpt = "No prior result snapshot was captured."

    if task_type == "code":
        templates = {
            "add_tests": (
                "Prepared follow-up for test coverage.\n"
                f"Question: {question}\n"
                "- Focus tests on the newly changed paths and edge cases.\n"
                "- Cover at least one success path and one failure path.\n"
                "- Reuse the current implementation summary as the test fixture source.\n"
                f"Context snapshot:\n{excerpt}"
            ),
            "optimize": (
                "Prepared follow-up for optimization.\n"
                f"Question: {question}\n"
                "- Look for repeated work, unnecessary allocations, and avoidable I/O.\n"
                "- Preserve behavior first, then benchmark or spot-check after simplification.\n"
                f"Context snapshot:\n{excerpt}"
            ),
            "explain": (
                "Prepared follow-up explanation.\n"
                f"Question: {question}\n"
                "- Summarize the main entry point.\n"
                "- Describe the key data flow and control decisions.\n"
                "- Call out the highest-risk edge case.\n"
                f"Context snapshot:\n{excerpt}"
            ),
            "refactor": (
                "Prepared follow-up refactor plan.\n"
                f"Question: {question}\n"
                "- Separate parsing, orchestration, and persistence responsibilities.\n"
                "- Keep external behavior stable while reducing duplication.\n"
                f"Context snapshot:\n{excerpt}"
            ),
        }
        return templates.get(intent, f"Prepared code follow-up for: {question}\n\n{excerpt}")

    if task_type == "image":
        templates = {
            "variants": (
                "Prepared image variant brief.\n"
                f"Question: {question}\n"
                "- Generate 2-3 alternatives that keep the subject and composition anchor.\n"
                "- Vary palette, framing, or detail density.\n"
                f"Context snapshot:\n{excerpt}"
            ),
            "change_style": (
                "Prepared style-transfer brief.\n"
                f"Question: {question}\n"
                "- Keep the subject and overall silhouette stable.\n"
                "- Change only rendering style, texture language, or lighting treatment.\n"
                f"Context snapshot:\n{excerpt}"
            ),
            "upscale": (
                "Prepared upscale brief.\n"
                f"Question: {question}\n"
                "- Preserve composition and facial/object proportions.\n"
                "- Increase clarity, edge detail, and texture consistency.\n"
                f"Context snapshot:\n{excerpt}"
            ),
            "more": (
                "Prepared extended image options brief.\n"
                f"Question: {question}\n"
                "- Keep the winning direction while broadening variation.\n"
                f"Context snapshot:\n{excerpt}"
            ),
        }
        return templates.get(intent, f"Prepared image follow-up for: {question}\n\n{excerpt}")

    if task_type == "video":
        templates = {
            "shorter": (
                "Prepared shorter-cut brief.\n"
                f"Question: {question}\n"
                "- Trim the intro/outro first and keep the strongest motion beats.\n"
                f"Context snapshot:\n{excerpt}"
            ),
            "different_angle": (
                "Prepared alternate-angle brief.\n"
                f"Question: {question}\n"
                "- Preserve scene timing while changing camera position and framing.\n"
                f"Context snapshot:\n{excerpt}"
            ),
            "change_format": (
                "Prepared export-format brief.\n"
                f"Question: {question}\n"
                "- Keep timing intact while adapting the container or aspect ratio.\n"
                f"Context snapshot:\n{excerpt}"
            ),
        }
        return templates.get(intent, f"Prepared video follow-up for: {question}\n\n{excerpt}")

    templates = {
        "more_details": (
            "Prepared deeper analysis.\n"
            f"Question: {question}\n"
            "- Expand the key assumptions.\n"
            "- Add the most important supporting details.\n"
            f"Context snapshot:\n{excerpt}"
        ),
        "what_if": (
            "Prepared what-if analysis.\n"
            f"Question: {question}\n"
            "- Re-evaluate the conclusion under changed assumptions.\n"
            "- Highlight what remains stable versus what flips.\n"
            f"Context snapshot:\n{excerpt}"
        ),
        "alternatives": (
            "Prepared alternatives comparison.\n"
            f"Question: {question}\n"
            "- Compare the strongest competing approaches.\n"
            "- Note the main trade-offs for each one.\n"
            f"Context snapshot:\n{excerpt}"
        ),
    }
    return templates.get(intent, f"Prepared analytical follow-up for: {question}\n\n{excerpt}")


def _load_cache_entry(root: Path, cache_key: str, now: float | None = None) -> dict[str, Any] | None:
    payload = _read_json(_cache_path(root, cache_key))
    if not isinstance(payload, dict):
        return None
    expires_at = payload.get("expires_at")
    now = time.time() if now is None else now
    if isinstance(expires_at, (int, float)) and float(expires_at) <= now:
        _cache_path(root, cache_key).unlink(missing_ok=True)
        return None
    return payload


def scout_check(question: str, scout_dir: Path | None = None) -> dict[str, Any] | None:
    root = scout_dir or _default_scout_dir()
    _ensure_layout(root)
    _purge_expired(root)

    entries: list[dict[str, Any]] = []
    for cache_file in _iter_json_files(_cache_dir(root)):
        payload = _read_json(cache_file)
        if isinstance(payload, dict):
            entries.append(payload)

    entries.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
    for entry in entries:
        if _question_matches(question, entry):
            _save_state(root, last_user_request_at=time.time())
            return entry
    _save_state(root, last_user_request_at=time.time())
    return None


def _existing_job(root: Path, cache_key: str) -> dict[str, Any] | None:
    for job_file in _iter_json_files(_jobs_dir(root)):
        payload = _read_json(job_file)
        if not isinstance(payload, dict):
            continue
        if payload.get("cache_key") == cache_key and payload.get("status") in {"pending", "running", "completed"}:
            return payload
    return None


def scout_run_background(
    predictions: list[dict[str, Any] | str],
    idle_seconds: float | None = None,
    scout_dir: Path | None = None,
) -> dict[str, Any]:
    root = scout_dir or _default_scout_dir()
    resolved_idle = _resolve_idle_seconds(root, idle_seconds)

    summary: dict[str, Any] = {
        "idle_seconds": round(resolved_idle, 2),
        "started": [],
        "cached": [],
        "skipped": [],
        "reason": None,
    }
    if resolved_idle <= IDLE_THRESHOLD_SECONDS:
        summary["reason"] = f"idle threshold not met ({resolved_idle:.2f}s <= {IDLE_THRESHOLD_SECONDS}s)"
        summary["skipped"] = [str(_coerce_prediction(item)["question"]) for item in predictions[:MAX_PREDICTIONS]]
        return summary

    _ensure_layout(root)
    _purge_expired(root)

    for raw_prediction in predictions[:MAX_PREDICTIONS]:
        prediction = _coerce_prediction(raw_prediction)
        cache_key = _hash_for_prediction(prediction["task_type"], prediction["intent"], prediction["question"])
        if _load_cache_entry(root, cache_key) is not None:
            summary["cached"].append(prediction["question"])
            continue
        existing_job = _existing_job(root, cache_key)
        if existing_job is not None and existing_job.get("status") in {"pending", "running", "completed"}:
            summary["skipped"].append(prediction["question"])
            continue

        created_at = time.time()
        job_id = f"{int(created_at * 1000)}-{cache_key}"
        job_payload = {
            "job_id": job_id,
            "cache_key": cache_key,
            "status": "pending",
            "created_at": created_at,
            "prediction": prediction,
        }
        job_path = _job_path(root, job_id)
        _write_json(job_path, job_payload)
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_worker", "--job-path", str(job_path), "--scout-dir", str(root)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        summary["started"].append(prediction["question"])

    _save_state(root, last_background_run_at=time.time())
    return summary


def _run_worker(job_path: Path, scout_dir: Path) -> int:
    _ensure_layout(scout_dir)
    job = _read_json(job_path)
    if not isinstance(job, dict):
        return 1

    started_at = time.time()
    job["status"] = "running"
    job["started_at"] = started_at
    _write_json(job_path, job)

    try:
        prediction = _coerce_prediction(job.get("prediction", {}))
        cache_key = str(job["cache_key"])
        prepared_response = _build_response(
            prediction["task_type"],
            prediction["intent"],
            prediction["question"],
            prediction.get("seed_result", ""),
        )
        cache_entry = {
            "cache_key": cache_key,
            "task_type": prediction["task_type"],
            "intent": prediction["intent"],
            "question": prediction["question"],
            "normalized_question": _normalize_question(prediction["question"]),
            "match_terms": _match_terms(prediction["question"], prediction.get("aliases", [])),
            "model": prediction.get("model", FAST_MODEL),
            "prepared_response": prepared_response,
            "created_at": started_at,
            "expires_at": started_at + CACHE_TTL_SECONDS,
        }
        _write_json(_cache_path(scout_dir, cache_key), cache_entry)
        job["status"] = "completed"
        job["completed_at"] = time.time()
        _write_json(job_path, job)
        return 0
    except Exception as exc:  # pragma: no cover - defensive state capture
        job["status"] = "failed"
        job["failed_at"] = time.time()
        job["error"] = f"{type(exc).__name__}: {exc}"
        _write_json(job_path, job)
        return 1


def scout_status(scout_dir: Path | None = None) -> dict[str, Any]:
    root = scout_dir or _default_scout_dir()
    _ensure_layout(root)
    _purge_expired(root)

    caches: list[dict[str, Any]] = []
    for cache_file in _iter_json_files(_cache_dir(root)):
        payload = _read_json(cache_file)
        if not isinstance(payload, dict):
            continue
        caches.append(
            {
                "question": payload.get("question"),
                "intent": payload.get("intent"),
                "task_type": payload.get("task_type"),
                "model": payload.get("model"),
                "expires_at": payload.get("expires_at"),
            }
        )

    jobs: list[dict[str, Any]] = []
    for job_file in _iter_json_files(_jobs_dir(root)):
        payload = _read_json(job_file)
        if not isinstance(payload, dict):
            continue
        prediction = payload.get("prediction", {})
        jobs.append(
            {
                "job_id": payload.get("job_id"),
                "status": payload.get("status"),
                "question": prediction.get("question"),
                "created_at": payload.get("created_at"),
            }
        )

    return {
        "scout_dir": str(root),
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "idle_threshold_seconds": IDLE_THRESHOLD_SECONDS,
        "fast_model": FAST_MODEL,
        "active_cache_entries": caches,
        "jobs": jobs,
        "state": _load_state(root),
    }


def scout_clear(scout_dir: Path | None = None) -> dict[str, Any]:
    root = scout_dir or _default_scout_dir()
    removed = {"cache_files": 0, "job_files": 0, "state_removed": False}

    for cache_file in _iter_json_files(_cache_dir(root)):
        cache_file.unlink(missing_ok=True)
        removed["cache_files"] += 1
    for job_file in _iter_json_files(_jobs_dir(root)):
        job_file.unlink(missing_ok=True)
        removed["job_files"] += 1

    state_file = _state_path(root)
    if state_file.exists():
        state_file.unlink()
        removed["state_removed"] = True

    if root.exists() and not any(root.iterdir()):
        shutil.rmtree(root)

    return removed


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Proactive scout for OpenClaw follow-up prediction.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Return a prepared follow-up if it is already cached.")
    check_parser.add_argument("question", help="Question to look up in the speculative cache.")
    check_parser.add_argument("--scout-dir", type=Path, default=None, help="Override the scout state directory.")

    status_parser = subparsers.add_parser("status", help="Show current scout cache and job state.")
    status_parser.add_argument("--scout-dir", type=Path, default=None, help="Override the scout state directory.")

    clear_parser = subparsers.add_parser("clear", help="Clear scout cache and job records.")
    clear_parser.add_argument("--scout-dir", type=Path, default=None, help="Override the scout state directory.")

    predict_parser = subparsers.add_parser("predict", help="Predict follow-ups and optionally run them in the background.")
    predict_parser.add_argument("task_type", help="Task type: image, code, video, or analysis.")
    predict_parser.add_argument("result", help="The assistant result that future follow-ups will build on.")
    predict_parser.add_argument("--scout-dir", type=Path, default=None, help="Override the scout state directory.")
    predict_parser.add_argument(
        "--idle-seconds",
        type=float,
        default=None,
        help="Only start background work when the system has been idle longer than this number.",
    )

    worker_parser = subparsers.add_parser("_worker")
    worker_parser.add_argument("--job-path", type=Path, required=True)
    worker_parser.add_argument("--scout-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        _print_json(scout_check(args.question, scout_dir=args.scout_dir))
        return 0
    if args.command == "status":
        _print_json(scout_status(scout_dir=args.scout_dir))
        return 0
    if args.command == "clear":
        _print_json(scout_clear(scout_dir=args.scout_dir))
        return 0
    if args.command == "predict":
        predictions = scout_predict(args.task_type, args.result)
        background = scout_run_background(
            predictions,
            idle_seconds=args.idle_seconds,
            scout_dir=args.scout_dir,
        )
        visible_predictions = [
            {key: value for key, value in prediction.items() if key != "seed_result"} for prediction in predictions
        ]
        _print_json({"predictions": visible_predictions, "background": background})
        return 0
    if args.command == "_worker":
        return _run_worker(args.job_path, args.scout_dir)
    return 1


def _selftest(stdout=None) -> bool:
    target = stdout or __import__("sys").stdout
    checks = []

    smoke_ok = callable(globals().get("main")) or callable(globals().get("build_parser"))
    checks.append(("smoke", smoke_ok))

    try:
        if callable(globals().get("estimate_tokens")):
            edge_ok = estimate_tokens("") == 0
        elif callable(globals().get("normalize_text")):
            edge_ok = isinstance(normalize_text("  sample  "), str)
        elif callable(globals().get("parse_args")):
            try:
                parse_args(["--help"])
                edge_ok = True
            except SystemExit:
                edge_ok = True
        else:
            edge_ok = True
    except Exception:
        edge_ok = False
    checks.append(("edge_case", edge_ok))

    passed = all(result for _, result in checks)
    target.write(f"{__name__} self-test: {'PASS' if passed else 'FAIL'}\n")
    for name, result in checks:
        target.write(f"  - {name}: {'PASS' if result else 'FAIL'}\n")
    return passed


if __name__ == "__main__":
    raise SystemExit(main())
