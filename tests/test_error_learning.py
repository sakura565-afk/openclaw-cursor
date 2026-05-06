from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import error_learning


class ErrorLearningTests(unittest.TestCase):
    def test_parse_and_auto_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / 'ERRORS.md'
            md_path.write_text(
                '\n'.join(
                    [
                        '# Patterns',
                        '## Upstream timeout',
                        '- category: unknown',
                        '- patterns: timed out, deadline exceeded',
                        '- keywords: upstream, api',
                        '- tags: network',
                        '- solution: retry',
                    ]
                ),
                encoding='utf-8',
            )
            parsed = error_learning.parse_markdown_errors(md_path)
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0].category, 'timeout')

    def test_deduplicates_near_identical_patterns(self) -> None:
        left = error_learning.ErrorPattern(
            title='Database connection refused',
            category='database',
            patterns=['connection refused', 'could not connect to server'],
            keywords=['postgres', 'connection'],
            tags=['backend'],
            solution='check db service',
        )
        right = error_learning.ErrorPattern(
            title='DB connection refused',
            category='database',
            patterns=['connection refused', 'db host unreachable'],
            keywords=['database', 'connection'],
            tags=['backend'],
            solution='validate db host and port',
        )

        deduped = error_learning.deduplicate_patterns([left, right])
        self.assertEqual(len(deduped), 1)
        self.assertIn('db host unreachable', deduped[0].patterns)

    def test_search_relevance_prefers_exact_phrase(self) -> None:
        records = [
            error_learning.ErrorPattern(
                title='TLS handshake failed',
                category='ssl',
                patterns=['TLS handshake failed', 'certificate verify failed'],
                keywords=['tls', 'certificate'],
                tags=['security'],
                solution='check certificate',
            ),
            error_learning.ErrorPattern(
                title='Network timeout',
                category='timeout',
                patterns=['timed out'],
                keywords=['network'],
                tags=['reliability'],
                solution='retry with backoff',
            ),
        ]
        ranked = error_learning.search_records(records, 'TLS handshake failed', limit=2)
        self.assertEqual(ranked[0][1].category, 'ssl')

    def test_sync_persists_deduplicated_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'ERRORS.md'
            db = Path(tmp) / 'error_patterns.json'
            source.write_text(
                '\n'.join(
                    [
                        '# Patterns',
                        '## Rate limit exceeded',
                        '- category: unknown',
                        '- patterns: too many requests, 429',
                        '- keywords: quota',
                        '- tags: reliability',
                        '- solution: throttle requests',
                        '## Rate limit exceeded on API',
                        '- category: rate_limit',
                        '- patterns: 429, too many requests',
                        '- keywords: api, throttle',
                        '- tags: reliability',
                        '- solution: add retry strategy',
                    ]
                ),
                encoding='utf-8',
            )

            count = error_learning.sync(source, db)
            self.assertEqual(count, 1)
            payload = json.loads(db.read_text(encoding='utf-8'))
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]['category'], 'rate_limit')


if __name__ == '__main__':
    unittest.main()
