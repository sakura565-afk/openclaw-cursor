from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_SOURCE = Path('.learnings/ERRORS.md')
DEFAULT_DB = Path('.learnings/error_patterns.json')

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    'timeout': ('timeout', 'timed out', 'deadline', 'latency'),
    'network': ('enotfound', 'dns', 'resolve', 'network', 'econnreset', 'connection reset'),
    'ssl': ('tls', 'ssl', 'x509', 'certificate', 'handshake'),
    'auth': ('unauthorized', 'forbidden', 'token', 'credential', 'permission denied', '401', '403'),
    'rate_limit': ('rate limit', 'too many requests', 'quota', '429', 'throttle'),
    'resource': ('out of memory', 'memoryerror', 'enospc', 'no space', 'resource exhausted', 'heap'),
    'database': ('sql', 'database', 'postgres', 'mysql', 'schema', 'relation', 'column'),
    'dependency': ('modulenotfounderror', 'importerror', 'cannot import', 'package not found'),
    'build': ('compile', 'compilation', 'linker', 'build failed', 'syntax error'),
    'filesystem': ('filenotfounderror', 'no such file', 'eacces', 'permission denied', 'path'),
}


@dataclass(slots=True)
class ErrorPattern:
    title: str
    category: str
    patterns: list[str]
    keywords: list[str]
    tags: list[str]
    solution: str


WORD_RE = re.compile(r'[a-z0-9_]+')
TOKEN_ALIASES = {
    'db': 'database',
    'svc': 'service',
    'authn': 'auth',
    'authz': 'auth',
}


def _normalize_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip().lower())


def _tokenize(text: str) -> set[str]:
    return {TOKEN_ALIASES.get(token, token) for token in WORD_RE.findall(_normalize_text(text))}


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    return len(left_set & right_set) / len(left_set | right_set)


def _auto_category(record: ErrorPattern) -> str:
    if record.category and record.category != 'unknown':
        return record.category
    corpus = ' '.join([record.title, *record.patterns, *record.keywords]).lower()
    best_category = 'unknown'
    best_score = 0
    for category, needles in CATEGORY_KEYWORDS.items():
        score = sum(1 for needle in needles if needle in corpus)
        if score > best_score:
            best_score = score
            best_category = category
    return best_category


def _canonical_key(record: ErrorPattern) -> tuple[str, tuple[str, ...]]:
    title_tokens = sorted(_tokenize(record.title))
    pattern_tokens = sorted({token for p in record.patterns for token in _tokenize(p)})
    # Use both title and pattern tokens so near-identical entries collapse.
    return (record.category, tuple(title_tokens + ['|'] + pattern_tokens))


def _should_merge(existing: ErrorPattern, incoming: ErrorPattern) -> bool:
    if existing.category != incoming.category:
        return False

    if _canonical_key(existing) == _canonical_key(incoming):
        return True

    existing_norm_patterns = {_normalize_text(p) for p in existing.patterns}
    incoming_norm_patterns = {_normalize_text(p) for p in incoming.patterns}
    if existing_norm_patterns & incoming_norm_patterns:
        return True

    existing_tokens = set(_tokenize(existing.title)) | {tok for p in existing.patterns for tok in _tokenize(p)}
    incoming_tokens = set(_tokenize(incoming.title)) | {tok for p in incoming.patterns for tok in _tokenize(p)}

    # Aggressive but safe dedupe for semantically close patterns.
    return _jaccard(existing_tokens, incoming_tokens) >= 0.5


def _merge_records(existing: ErrorPattern, incoming: ErrorPattern) -> ErrorPattern:
    patterns = sorted({*existing.patterns, *incoming.patterns})
    keywords = sorted({*existing.keywords, *incoming.keywords})
    tags = sorted({*existing.tags, *incoming.tags})
    solution = existing.solution if len(existing.solution) >= len(incoming.solution) else incoming.solution
    title = existing.title if len(existing.title) >= len(incoming.title) else incoming.title
    return ErrorPattern(
        title=title,
        category=existing.category,
        patterns=patterns,
        keywords=keywords,
        tags=tags,
        solution=solution,
    )


def parse_markdown_errors(path: Path) -> list[ErrorPattern]:
    if not path.exists():
        return []

    lines = path.read_text(encoding='utf-8').splitlines()
    entries: list[ErrorPattern] = []
    current: dict[str, str] = {}

    def flush() -> None:
        if not current.get('title'):
            return
        patterns = [part.strip() for part in current.get('patterns', '').split(',') if part.strip()]
        keywords = [part.strip() for part in current.get('keywords', '').split(',') if part.strip()]
        tags = [part.strip() for part in current.get('tags', '').split(',') if part.strip()]
        record = ErrorPattern(
            title=current['title'],
            category=current.get('category', 'unknown') or 'unknown',
            patterns=patterns,
            keywords=keywords,
            tags=tags,
            solution=current.get('solution', ''),
        )
        record.category = _auto_category(record)
        entries.append(record)

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith('## '):
            flush()
            current = {'title': line[3:].strip()}
            continue
        if not line.startswith('- ') or ':' not in line:
            continue
        key, value = line[2:].split(':', 1)
        current[key.strip().lower()] = value.strip()

    flush()
    return entries


def deduplicate_patterns(records: list[ErrorPattern]) -> list[ErrorPattern]:
    deduped: list[ErrorPattern] = []
    for record in records:
        merged = False
        for idx, existing in enumerate(deduped):
            if _should_merge(existing, record):
                deduped[idx] = _merge_records(existing, record)
                merged = True
                break
        if not merged:
            deduped.append(record)
    return deduped


def save_database(path: Path, records: list[ErrorPattern]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(record) for record in records]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def load_database(path: Path) -> list[ErrorPattern]:
    if not path.exists():
        return []
    content = json.loads(path.read_text(encoding='utf-8'))
    return [ErrorPattern(**item) for item in content]


def _score_record(query: str, record: ErrorPattern) -> float:
    q = _normalize_text(query)
    q_tokens = _tokenize(q)

    title = _normalize_text(record.title)
    patterns = [_normalize_text(p) for p in record.patterns]
    keywords = [_normalize_text(k) for k in record.keywords]

    score = 0.0

    if q in title:
        score += 8.0
    if any(q in p for p in patterns):
        score += 6.0

    score += 2.2 * sum(1 for token in q_tokens if token in _tokenize(record.title))
    score += 1.8 * sum(1 for token in q_tokens if any(token in _tokenize(p) for p in patterns))
    score += 1.1 * sum(1 for token in q_tokens if any(token in k for k in keywords))

    if record.category != 'unknown' and record.category in q:
        score += 2.5

    score += min(len(record.tags), 3) * 0.15
    return score


def search_records(records: list[ErrorPattern], query: str, limit: int = 5) -> list[tuple[float, ErrorPattern]]:
    ranked = [( _score_record(query, record), record) for record in records]
    ranked = [item for item in ranked if item[0] > 0]
    ranked.sort(key=lambda item: (item[0], len(item[1].patterns), len(item[1].keywords)), reverse=True)
    return ranked[:limit]


def sync(source: Path, db_path: Path) -> int:
    parsed = parse_markdown_errors(source)
    existing = load_database(db_path)
    combined = deduplicate_patterns([*existing, *parsed])
    save_database(db_path, combined)
    return len(combined)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Learn and search common error patterns.')
    subparsers = parser.add_subparsers(dest='command', required=True)

    sync_parser = subparsers.add_parser('sync', help='Parse markdown and refresh local db.')
    sync_parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    sync_parser.add_argument('--db', type=Path, default=DEFAULT_DB)

    search_parser = subparsers.add_parser('search', help='Search known error patterns.')
    search_parser.add_argument('query', type=str)
    search_parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    search_parser.add_argument('--limit', type=int, default=5)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == 'sync':
        count = sync(args.source, args.db)
        print(f'Synced {count} deduplicated error patterns into {args.db}')
        return 0

    if args.command == 'search':
        records = load_database(args.db)
        results = search_records(records, args.query, args.limit)
        for idx, (score, record) in enumerate(results, start=1):
            print(f'[{idx}] score={score:.2f} category={record.category} title={record.title}')
            if record.patterns:
                print(f'  patterns: {", ".join(record.patterns[:4])}')
            if record.solution:
                print(f'  solution: {record.solution}')
        if not results:
            print('No results found.')
        return 0

    parser.print_help()
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
