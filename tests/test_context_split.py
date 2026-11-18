from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import context_split  # noqa: E402


def fake_token_counter(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return len(stripped.split())


class ContextSplitTests(unittest.TestCase):
    def test_split_context_skips_small_inputs(self) -> None:
        context = "short context stays whole"

        chunks = context_split.split_context(
            context,
            chunk_size=5,
            overlap_tokens=1,
            split_threshold=100,
            recursive_limit=10,
            token_counter=fake_token_counter,
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, context)
        self.assertEqual(chunks[0].estimated_tokens, 4)
        self.assertEqual(chunks[0].overlap_tokens, 0)

    def test_split_context_uses_headers_and_overlap(self) -> None:
        context = "\n\n".join(
            [
                "# Intro\n\nalpha beta gamma delta",
                "## Details\n\none two three four",
                "## More\n\nfive six seven eight",
            ]
        )

        chunks = context_split.split_context(
            context,
            chunk_size=8,
            overlap_tokens=2,
            split_threshold=1,
            recursive_limit=20,
            token_counter=fake_token_counter,
        )

        self.assertEqual(len(chunks), 3)
        self.assertIn("# Intro", chunks[0].text)
        self.assertIn("## Details", chunks[1].text)
        self.assertIn("one two three four", chunks[1].text)
        self.assertGreaterEqual(chunks[1].overlap_tokens, 2)
        self.assertIn("## More", chunks[2].text)

    def test_split_context_recursively_splits_large_chunk(self) -> None:
        huge_block = " ".join(f"token{i}" for i in range(40))

        chunks = context_split.split_context(
            huge_block,
            chunk_size=20,
            overlap_tokens=5,
            split_threshold=10,
            recursive_limit=15,
            token_counter=fake_token_counter,
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.estimated_tokens <= 15 for chunk in chunks))
        self.assertTrue(any(chunk.depth > 0 for chunk in chunks))

    def test_query_with_retry_retries_once(self) -> None:
        calls: list[int] = []

        def flaky_requester(messages: context_split.MessageList, timeout: int) -> str:
            calls.append(timeout)
            if len(calls) == 1:
                raise RuntimeError("temporary failure")
            return "ok"

        answer, attempts = context_split.query_with_retry(
            flaky_requester,
            [{"role": "user", "content": "hi"}],
            timeout=9,
            retry_attempts=1,
        )

        self.assertEqual(answer, "ok")
        self.assertEqual(attempts, 2)
        self.assertEqual(calls, [9, 9])

    def test_split_and_query_context_direct_mode(self) -> None:
        captured_messages: list[context_split.MessageList] = []

        def requester(messages: context_split.MessageList, timeout: int) -> str:
            captured_messages.append(messages)
            self.assertEqual(timeout, 7)
            return "direct answer"

        result = context_split.split_and_query_context(
            "What is this?",
            "small context only",
            chunk_size=10,
            overlap_tokens=2,
            split_threshold=100,
            recursive_limit=15,
            timeout=7,
            token_counter=fake_token_counter,
            requester=requester,
        )

        self.assertEqual(result["method"], "direct")
        self.assertEqual(result["n_chunks"], 1)
        self.assertEqual(result["answer"], "direct answer")
        self.assertEqual(result["chunks_used"], [1])
        self.assertEqual(result["chunks_info"][0]["attempts"], 1)
        self.assertEqual(len(captured_messages), 1)

    def test_split_and_query_context_parallel_and_synthesis(self) -> None:
        context = "\n\n".join(
            [
                "# Part 1\n\nalpha beta gamma delta epsilon",
                "# Part 2\n\nzeta eta theta iota kappa",
                "# Part 3\n\nlambda mu nu xi omicron",
            ]
        )
        recorded_prompts: list[str] = []

        def requester(messages: context_split.MessageList, timeout: int) -> str:
            recorded_prompts.append(messages[-1]["content"])
            prompt = messages[-1]["content"]
            if "Chunk answers:" in prompt:
                return "final synthesis"
            marker = "Chunk "
            start = prompt.index(marker) + len(marker)
            end = prompt.index(" of ", start)
            chunk_index = int(prompt[start:end])
            return f"partial {chunk_index}"

        result = context_split.split_and_query_context(
            "Summarize",
            context,
            chunk_size=8,
            overlap_tokens=2,
            split_threshold=1,
            recursive_limit=12,
            timeout=11,
            max_workers=3,
            token_counter=fake_token_counter,
            requester=requester,
        )

        self.assertEqual(result["method"], "context_split")
        self.assertEqual(result["n_chunks"], 3)
        self.assertEqual(result["answer"], "final synthesis")
        self.assertEqual(result["chunks_used"], [1, 2, 3])
        self.assertEqual(len(result["chunks_info"]), 3)
        self.assertEqual(sum("Chunk answers:" in prompt for prompt in recorded_prompts), 1)
        self.assertEqual(sum("Chunk " in prompt and "Chunk answers:" not in prompt for prompt in recorded_prompts), 3)

    def test_extract_message_text_supports_text_parts(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "hello"},
                            {"type": "text", "text": " world"},
                        ]
                    }
                }
            ]
        }

        self.assertEqual(context_split.extract_message_text(payload), "hello world")

    def test_load_context_reads_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "context.txt"
            path.write_text("from file", encoding="utf-8")

            loaded = context_split.load_context(
                Namespace(context_file=str(path), context=None)
            )

        self.assertEqual(loaded, "from file")

    def test_main_prints_json(self) -> None:
        def requester(messages: context_split.MessageList, timeout: int) -> str:
            prompt = messages[-1]["content"]
            if "Chunk answers:" in prompt:
                return "combined"
            return "piece"

        stdout = io.StringIO()
        previous = sys.stdout
        try:
            sys.stdout = stdout
            exit_code = context_split.main(
                [
                    "Question?",
                    "alpha beta gamma delta epsilon zeta eta theta iota kappa",
                    "--chunk-size",
                    "4",
                    "--overlap",
                    "1",
                    "--split-threshold",
                    "1",
                    "--recursive-limit",
                    "5",
                    "--timeout",
                    "3",
                    "--max-workers",
                    "2",
                ],
                requester=requester,
            )
        finally:
            sys.stdout = previous

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["answer"], "combined")
        self.assertEqual(payload["method"], "context_split")


if __name__ == "__main__":
    unittest.main()
