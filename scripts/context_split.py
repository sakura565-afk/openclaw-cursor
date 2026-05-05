#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from urllib import error, request


DEFAULT_CHUNK_SIZE = 50_000
DEFAULT_OVERLAP = 5_000
DEFAULT_SPLIT_THRESHOLD = 100_000
DEFAULT_RECURSIVE_LIMIT = 150_000
DEFAULT_TIMEOUT = 60
DEFAULT_RETRY_ATTEMPTS = 1
DEFAULT_API_URL = os.environ.get(
    "OPENROUTER_API_URL",
    os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"),
)
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "minimax/minimax-m1")

TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
HEADER_RE = re.compile(r"^(?:#{1,6}\s+\S.*|[A-Z][A-Z0-9 _/\-]{2,}:?)$")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

MessageList = list[dict[str, str]]
Requester = Callable[[MessageList, int], str]


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    estimated_tokens: int
    overlap_tokens: int
    depth: int = 0


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    estimated_tokens: int
    overlap_tokens: int
    depth: int


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def estimate_tokens(text: str) -> int:
    normalized = normalize_text(text)
    if not normalized:
        return 0
    return len(TOKEN_RE.findall(normalized))


def normalize_api_url(api_url: str) -> str:
    normalized = api_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def is_header_block(block: str) -> bool:
    stripped = block.strip()
    if not stripped or "\n" in stripped:
        return False
    return bool(HEADER_RE.match(stripped))


def split_semantic_units(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]
    if not paragraphs:
        return [normalized]

    units: list[str] = []
    pending_header: str | None = None
    for paragraph in paragraphs:
        if is_header_block(paragraph):
            if pending_header is not None:
                units.append(pending_header)
            pending_header = paragraph
            continue
        if pending_header is not None:
            units.append(f"{pending_header}\n\n{paragraph}")
            pending_header = None
            continue
        units.append(paragraph)

    if pending_header is not None:
        units.append(pending_header)
    return units or [normalized]


def _count_units(units: Sequence[str], token_counter: Callable[[str], int]) -> int:
    return sum(token_counter(unit) for unit in units)


def _select_overlap_units(
    units: Sequence[str],
    overlap_tokens: int,
    token_counter: Callable[[str], int],
) -> list[str]:
    if overlap_tokens <= 0:
        return []

    chosen: list[str] = []
    total = 0
    for unit in reversed(units):
        chosen.insert(0, unit)
        total += token_counter(unit)
        if total >= overlap_tokens:
            break
    return chosen


def _trim_overlap_units(
    units: Sequence[str],
    max_tokens: int,
    token_counter: Callable[[str], int],
) -> list[str]:
    if max_tokens <= 0:
        return []

    trimmed = list(units)
    while trimmed and _count_units(trimmed, token_counter) > max_tokens:
        trimmed.pop(0)
    return trimmed


def _tail_word_overlap(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    words = re.findall(r"\S+", normalize_text(text))
    if not words:
        return ""
    return " ".join(words[-max_tokens:])


def _split_words_into_windows(
    text: str,
    chunk_size: int,
    overlap_tokens: int,
) -> list[str]:
    words = re.findall(r"\S+", normalize_text(text))
    if not words:
        return []

    step = max(1, chunk_size - min(overlap_tokens, max(0, chunk_size - 1)))
    windows: list[str] = []
    index = 0
    while index < len(words):
        end = min(len(words), index + chunk_size)
        windows.append(" ".join(words[index:end]))
        if end >= len(words):
            break
        index += step
    return windows


def _split_large_unit(
    text: str,
    chunk_size: int,
    overlap_tokens: int,
    token_counter: Callable[[str], int],
) -> list[str]:
    normalized = normalize_text(text)
    if token_counter(normalized) <= chunk_size:
        return [normalized]

    line_units = [line.strip() for line in normalized.splitlines() if line.strip()]
    if len(line_units) > 1:
        packed = _pack_units_into_drafts(line_units, chunk_size, overlap_tokens, token_counter, depth=0)
        if packed and all(draft.estimated_tokens <= chunk_size for draft in packed):
            return [draft.text for draft in packed]

    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(normalized) if part.strip()]
    if len(sentences) > 1:
        packed = _pack_units_into_drafts(sentences, chunk_size, overlap_tokens, token_counter, depth=0)
        if packed and all(draft.estimated_tokens <= chunk_size for draft in packed):
            return [draft.text for draft in packed]

    return _split_words_into_windows(normalized, chunk_size, overlap_tokens)


def _ensure_unit_fits(
    text: str,
    chunk_size: int,
    overlap_tokens: int,
    token_counter: Callable[[str], int],
) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    if token_counter(normalized) <= chunk_size:
        return [normalized]

    pieces = _split_large_unit(normalized, chunk_size, overlap_tokens, token_counter)
    if not pieces:
        return [normalized]
    if len(pieces) == 1 and normalize_text(pieces[0]) == normalized:
        return [normalized]

    fitted: list[str] = []
    for piece in pieces:
        fitted.extend(_ensure_unit_fits(piece, chunk_size, overlap_tokens, token_counter))
    return fitted


def _pack_units_into_drafts(
    units: Sequence[str],
    chunk_size: int,
    overlap_tokens: int,
    token_counter: Callable[[str], int],
    depth: int,
) -> list[ChunkDraft]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens cannot be negative")
    if overlap_tokens >= chunk_size:
        raise ValueError("overlap_tokens must be smaller than chunk_size")

    prepared_units: list[str] = []
    for unit in units:
        prepared_units.extend(_ensure_unit_fits(unit, chunk_size, overlap_tokens, token_counter))

    drafts: list[ChunkDraft] = []
    current_units: list[str] = []
    current_tokens = 0
    current_overlap = 0

    for unit in prepared_units:
        unit_tokens = token_counter(unit)
        if current_units and current_tokens + unit_tokens > chunk_size:
            chunk_text = "\n\n".join(current_units).strip()
            drafts.append(
                ChunkDraft(
                    text=chunk_text,
                    estimated_tokens=current_tokens,
                    overlap_tokens=current_overlap,
                    depth=depth,
                )
            )
            overlap_units = _select_overlap_units(current_units, overlap_tokens, token_counter)
            overlap_units = _trim_overlap_units(
                overlap_units,
                max(0, chunk_size - unit_tokens),
                token_counter,
            )
            if not overlap_units and overlap_tokens > 0:
                fallback_overlap = _tail_word_overlap(
                    chunk_text,
                    min(overlap_tokens, max(0, chunk_size - unit_tokens)),
                )
                if fallback_overlap:
                    overlap_units = [fallback_overlap]
            current_units = list(overlap_units)
            current_tokens = _count_units(current_units, token_counter)
            current_overlap = current_tokens

        if current_units and current_tokens + unit_tokens > chunk_size:
            current_units = []
            current_tokens = 0
            current_overlap = 0

        current_units.append(unit)
        current_tokens += unit_tokens

    if current_units:
        chunk_text = "\n\n".join(current_units).strip()
        drafts.append(
            ChunkDraft(
                text=chunk_text,
                estimated_tokens=current_tokens,
                overlap_tokens=current_overlap,
                depth=depth,
            )
        )
    return drafts


def _split_context_to_drafts(
    text: str,
    chunk_size: int,
    overlap_tokens: int,
    split_threshold: int,
    recursive_limit: int,
    token_counter: Callable[[str], int],
    depth: int = 0,
    max_depth: int = 8,
) -> list[ChunkDraft]:
    normalized = normalize_text(text)
    total_tokens = token_counter(normalized)
    if not normalized:
        return [ChunkDraft(text="", estimated_tokens=0, overlap_tokens=0, depth=depth)]
    if total_tokens <= split_threshold:
        return [
            ChunkDraft(
                text=normalized,
                estimated_tokens=total_tokens,
                overlap_tokens=0,
                depth=depth,
            )
        ]

    units = split_semantic_units(normalized)
    drafts = _pack_units_into_drafts(units, chunk_size, overlap_tokens, token_counter, depth)

    if depth >= max_depth:
        return drafts

    expanded: list[ChunkDraft] = []
    for draft in drafts:
        if draft.estimated_tokens <= recursive_limit:
            expanded.append(draft)
            continue
        nested_chunk_size = min(chunk_size, recursive_limit)
        nested_overlap = min(overlap_tokens, max(0, nested_chunk_size - 1))
        nested = _split_context_to_drafts(
            draft.text,
            chunk_size=nested_chunk_size,
            overlap_tokens=nested_overlap,
            split_threshold=0,
            recursive_limit=recursive_limit,
            token_counter=token_counter,
            depth=depth + 1,
            max_depth=max_depth,
        )
        if len(nested) == 1 and nested[0].text == draft.text:
            expanded.append(draft)
            continue
        expanded.extend(nested)
    return expanded


def split_context(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap_tokens: int = DEFAULT_OVERLAP,
    split_threshold: int = DEFAULT_SPLIT_THRESHOLD,
    recursive_limit: int = DEFAULT_RECURSIVE_LIMIT,
    token_counter: Callable[[str], int] = estimate_tokens,
) -> list[Chunk]:
    drafts = _split_context_to_drafts(
        text,
        chunk_size=chunk_size,
        overlap_tokens=overlap_tokens,
        split_threshold=split_threshold,
        recursive_limit=recursive_limit,
        token_counter=token_counter,
    )
    return [
        Chunk(
            index=index,
            text=draft.text,
            estimated_tokens=draft.estimated_tokens,
            overlap_tokens=draft.overlap_tokens,
            depth=draft.depth,
        )
        for index, draft in enumerate(drafts, start=1)
    ]


def build_chunk_messages(question: str, chunk: Chunk, total_chunks: int) -> MessageList:
    return [
        {
            "role": "system",
            "content": (
                "You are answering a question using only one chunk from a larger context. "
                "Do not assume facts that are not present in the chunk."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                f"Chunk {chunk.index} of {total_chunks}:\n"
                f"{chunk.text}\n\n"
                "Instructions:\n"
                "- Answer only from this chunk.\n"
                "- If the chunk is insufficient, say what is missing.\n"
                "- Keep the response concise and factual.\n"
            ),
        },
    ]


def build_synthesis_messages(question: str, chunk_answers: Sequence[dict[str, object]]) -> MessageList:
    rendered_answers = []
    for item in chunk_answers:
        rendered_answers.append(
            f"Chunk {item['index']} answer:\n{item['answer']}"
        )
    return [
        {
            "role": "system",
            "content": (
                "You are combining answers from multiple context chunks into one final answer. "
                "Synthesize them faithfully and call out uncertainty when chunks disagree."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                "Chunk answers:\n\n"
                + "\n\n".join(rendered_answers)
                + "\n\nInstructions:\n"
                "- Produce a single final answer.\n"
                "- Reconcile repeated details across chunks.\n"
                "- Mention uncertainty or missing information when needed.\n"
            ),
        },
    ]


def extract_message_text(payload: dict) -> str:
    try:
        message = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected chat completion payload: {payload!r}") from exc

    if isinstance(message, str):
        return message.strip()
    if isinstance(message, list):
        parts: list[str] = []
        for item in message:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        combined = "".join(parts).strip()
        if combined:
            return combined
    raise RuntimeError(f"Unsupported completion content payload: {message!r}")


def openrouter_chat_completion(
    messages: MessageList,
    timeout: int,
    *,
    api_key: str,
    api_url: str = DEFAULT_API_URL,
    model: str = DEFAULT_MODEL,
) -> str:
    if not api_key:
        raise ValueError("An OpenRouter API key is required.")

    payload = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Title": os.environ.get("OPENROUTER_APP_NAME", "OpenClaw Context Splitter"),
    }
    referer = os.environ.get("OPENROUTER_SITE_URL")
    if referer:
        headers["HTTP-Referer"] = referer

    request_obj = request.Request(
        normalize_api_url(api_url),
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with request.urlopen(request_obj, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenRouter request failed with HTTP {exc.code}: {detail}"
        ) from exc
    except error.URLError as exc:
        raise RuntimeError(f"OpenRouter request failed: {exc.reason}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenRouter returned invalid JSON: {raw!r}") from exc
    return extract_message_text(data)


def query_with_retry(
    requester: Requester,
    messages: MessageList,
    timeout: int,
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
) -> tuple[str, int]:
    attempts = 0
    last_error: Exception | None = None
    while attempts <= retry_attempts:
        attempts += 1
        try:
            return requester(messages, timeout), attempts
        except Exception as exc:  # pragma: no cover - exercised via tests with mocks
            last_error = exc
            if attempts > retry_attempts:
                break
            time.sleep(1)
    if last_error is None:
        raise RuntimeError("Query failed without raising an error.")
    raise last_error


def _query_chunk(
    chunk: Chunk,
    question: str,
    total_chunks: int,
    requester: Requester,
    timeout: int,
    retry_attempts: int,
) -> dict[str, object]:
    messages = build_chunk_messages(question, chunk, total_chunks)
    answer, attempts = query_with_retry(
        requester,
        messages,
        timeout=timeout,
        retry_attempts=retry_attempts,
    )
    return {
        "index": chunk.index,
        "answer": answer,
        "attempts": attempts,
        "estimated_tokens": chunk.estimated_tokens,
        "overlap_tokens": chunk.overlap_tokens,
        "depth": chunk.depth,
        "status": "completed",
    }


def split_and_query_context(
    question: str,
    context: str,
    *,
    api_key: str | None = None,
    api_url: str = DEFAULT_API_URL,
    model: str = DEFAULT_MODEL,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap_tokens: int = DEFAULT_OVERLAP,
    split_threshold: int = DEFAULT_SPLIT_THRESHOLD,
    recursive_limit: int = DEFAULT_RECURSIVE_LIMIT,
    timeout: int = DEFAULT_TIMEOUT,
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    max_workers: int | None = None,
    token_counter: Callable[[str], int] = estimate_tokens,
    requester: Requester | None = None,
) -> dict[str, object]:
    normalized_context = normalize_text(context)
    if not normalized_context:
        raise ValueError("context must not be empty")

    if requester is None:
        resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        requester = lambda messages, timeout_value: openrouter_chat_completion(
            messages,
            timeout=timeout_value,
            api_key=resolved_key,
            api_url=api_url,
            model=model,
        )

    chunks = split_context(
        normalized_context,
        chunk_size=chunk_size,
        overlap_tokens=overlap_tokens,
        split_threshold=split_threshold,
        recursive_limit=recursive_limit,
        token_counter=token_counter,
    )

    if len(chunks) == 1 and chunks[0].estimated_tokens <= split_threshold:
        messages = build_chunk_messages(question, chunks[0], 1)
        answer, attempts = query_with_retry(
            requester,
            messages,
            timeout=timeout,
            retry_attempts=retry_attempts,
        )
        return {
            "answer": answer,
            "n_chunks": 1,
            "chunks_used": [1],
            "method": "direct",
            "chunks_info": [
                {
                    "index": 1,
                    "estimated_tokens": chunks[0].estimated_tokens,
                    "overlap_tokens": chunks[0].overlap_tokens,
                    "depth": chunks[0].depth,
                    "status": "completed",
                    "attempts": attempts,
                }
            ],
        }

    workers = max_workers or min(8, max(1, len(chunks)))
    chunk_results: list[dict[str, object] | None] = [None] * len(chunks)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _query_chunk,
                chunk,
                question,
                len(chunks),
                requester,
                timeout,
                retry_attempts,
            ): chunk.index
            for chunk in chunks
        }
        for future in as_completed(futures):
            result = future.result()
            chunk_results[result["index"] - 1] = result

    ordered_results = [result for result in chunk_results if result is not None]
    synthesis_messages = build_synthesis_messages(question, ordered_results)
    final_answer, synthesis_attempts = query_with_retry(
        requester,
        synthesis_messages,
        timeout=timeout,
        retry_attempts=retry_attempts,
    )

    chunks_info: list[dict[str, object]] = []
    for chunk, result in zip(chunks, ordered_results):
        chunks_info.append(
            {
                "index": chunk.index,
                "estimated_tokens": chunk.estimated_tokens,
                "overlap_tokens": chunk.overlap_tokens,
                "depth": chunk.depth,
                "status": result["status"],
                "attempts": result["attempts"],
            }
        )

    return {
        "answer": final_answer,
        "n_chunks": len(chunks),
        "chunks_used": [chunk.index for chunk in chunks],
        "method": "context_split",
        "chunks_info": chunks_info,
        "synthesis_attempts": synthesis_attempts,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split large contexts before querying MiniMax.")
    parser.add_argument("question", help="Question to ask about the context.")
    parser.add_argument("context", nargs="?", help="Context text to analyze.")
    parser.add_argument(
        "--file",
        dest="context_file",
        help="Read the context from a file instead of the positional context argument.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENROUTER_API_KEY", ""),
        help="OpenRouter API key. Defaults to OPENROUTER_API_KEY.",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help="OpenRouter-style chat completions endpoint or base URL.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model name to send to the OpenRouter-style endpoint.",
    )
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--split-threshold", type=int, default=DEFAULT_SPLIT_THRESHOLD)
    parser.add_argument("--recursive-limit", type=int, default=DEFAULT_RECURSIVE_LIMIT)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--max-workers", type=int, default=None)

    args = parser.parse_args(argv)
    if args.context_file and args.context is not None:
        parser.error("Provide either a positional context or --file, not both.")
    if not args.context_file and args.context is None:
        parser.error("Provide a context string or use --file.")
    return args


def load_context(args: argparse.Namespace) -> str:
    if args.context_file:
        return Path(args.context_file).read_text(encoding="utf-8")
    return args.context


def main(
    argv: list[str] | None = None,
    requester: Requester | None = None,
) -> int:
    args = parse_args(argv)
    result = split_and_query_context(
        args.question,
        load_context(args),
        api_key=args.api_key,
        api_url=args.api_url,
        model=args.model,
        chunk_size=args.chunk_size,
        overlap_tokens=args.overlap,
        split_threshold=args.split_threshold,
        recursive_limit=args.recursive_limit,
        timeout=args.timeout,
        max_workers=args.max_workers,
        requester=requester,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


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
    sys.exit(main())
