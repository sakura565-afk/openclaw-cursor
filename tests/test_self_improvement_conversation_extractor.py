"""Tests for scripts.self_improvement.conversation_extractor."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.self_improvement import conversation_extractor as ce


class ConversationExtractorTests(unittest.TestCase):
    def test_json_messages_segments_and_decisions(self) -> None:
        payload = {
            "messages": [
                {"role": "user", "content": "Help with auth."},
                {
                    "role": "assistant",
                    "content": "Decision: use OAuth2 for the MVP.\nLearning: refresh tokens must be rotated.",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            segs = ce.parse_session_log(p)
            self.assertTrue(any("OAuth2" in t for _, _, t in segs))
            d = ce.analyze_segments(segs, p.as_posix())
            self.assertTrue(any("OAuth2" in x for x in d.decisions))
            self.assertTrue(any("refresh" in x.lower() for x in d.learnings))

    def test_markdown_headings_and_frontmatter(self) -> None:
        body = """---
title: x
---
## Session goals
User: hello
### Sub
Decision: ship the fix today.
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.md"
            p.write_text(body, encoding="utf-8")
            raw = ce.strip_markdown_frontmatter(ce.read_text(p))
            heads = ce.extract_markdown_headings(raw)
            self.assertIn("Session goals", heads)
            segs = ce.parse_session_log(p)
            d = ce.analyze_segments(segs, p.as_posix(), raw_markdown=raw)
            self.assertIn("Session goals", d.markdown_headings)

    def test_topic_counters_skips_stopwords(self) -> None:
        text = "the API and the API and the database connection"
        uni, bi = ce.extract_topic_counters(text)
        self.assertGreater(uni.get("api", 0), 1)
        self.assertEqual(uni.get("the", 0), 0)

    def test_aggregate_merges_topics(self) -> None:
        d1 = ce.ConversationDigest(
            source="a",
            generated_at_utc="t",
            segments=[],
            decisions=["use postgres"],
            learnings=["cache headers matter"],
            context_snippets=[],
            action_items=[],
            markdown_headings=[],
            topic_terms=[("api", 4), ("auth", 2)],
            topic_phrases=[("access token", 2)],
        )
        d2 = ce.ConversationDigest(
            source="b",
            generated_at_utc="t",
            segments=[],
            decisions=["use postgres"],
            learnings=["cache headers matter"],
            context_snippets=[],
            action_items=[],
            markdown_headings=[],
            topic_terms=[("api", 3)],
            topic_phrases=[("access token", 1)],
        )
        agg = ce.aggregate_topic_maps([d1, d2])
        self.assertEqual(agg["recurring_decisions"][0][1], 2)
        terms = dict(agg["cross_session_topic_terms"])
        self.assertEqual(terms.get("api"), 7)


if __name__ == "__main__":
    unittest.main()
