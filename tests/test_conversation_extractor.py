import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "conversation_extractor.py"
SPEC = importlib.util.spec_from_file_location("conversation_extractor", MODULE_PATH)
conversation_extractor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = conversation_extractor
SPEC.loader.exec_module(conversation_extractor)


class ConversationExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir(parents=True)
        self.now = datetime(2026, 5, 6, 6, 10, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_session(self, relative_path: str, content: str, *, modified_at: datetime) -> Path:
        path = self.sessions_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        timestamp = modified_at.timestamp()
        os.utime(path, (timestamp, timestamp))
        return path

    def write_json_session(self, relative_path: str, payload: object, *, modified_at: datetime) -> Path:
        return self.write_session(relative_path, json.dumps(payload, indent=2), modified_at=modified_at)

    def test_extract_conversations_filters_recent_and_identifies_value(self) -> None:
        self.write_json_session(
            "recent-session.json",
            {
                "messages": [
                    {
                        "role": "user",
                        "timestamp": "2026-05-05T09:00:00+00:00",
                        "content": "We learned the cache cache invalidation root cause was stale cache keys.",
                    },
                    {
                        "role": "assistant",
                        "timestamp": "2026-05-05T09:01:00+00:00",
                        "content": "Decision: we will keep cache tags and add a repair job.",
                    },
                    {
                        "role": "user",
                        "timestamp": "2026-05-05T09:02:00+00:00",
                        "content": (
                            "Actually the previous worker estimate was wrong; instead we need "
                            "to batch cache writes. Next step: implement the migration."
                        ),
                    },
                ]
            },
            modified_at=self.now - timedelta(hours=2),
        )
        self.write_session(
            "old-session.txt",
            "User: We learned a historical lesson.\nAssistant: Decision: archive the old plan.\n",
            modified_at=self.now - timedelta(days=14),
        )

        conversations = conversation_extractor.extract_conversations(
            self.sessions_dir,
            days=7,
            now=self.now,
        )

        self.assertEqual(1, len(conversations))
        conversation = conversations[0]
        self.assertEqual("recent-session", conversation.session_id)
        self.assertEqual(3, len(conversation.messages))
        self.assertIn("User", conversation.participants)
        self.assertIn("Assistant", conversation.participants)
        self.assertIn("cache", conversation.topics)

        valuable = conversation_extractor.identify_valuable_exchanges(conversations)
        self.assertEqual(1, len(valuable))
        self.assertGreater(valuable[0].score, 0)
        self.assertTrue(valuable[0].signals["learnings"])
        self.assertTrue(valuable[0].signals["decisions"])
        self.assertTrue(valuable[0].signals["corrections"])
        self.assertTrue(valuable[0].signals["actions"])

    def test_generate_summary_and_archive_to_memory_are_structured_and_deduplicated(self) -> None:
        session_path = self.write_json_session(
            "archive-me.json",
            {
                "conversation": [
                    {
                        "author": {"role": "user", "name": "Operator"},
                        "timestamp": "2026-05-04T12:00:00+00:00",
                        "content": "We learned the retry loop was hiding a timeout bug.",
                    },
                    {
                        "author": {"role": "assistant", "name": "Planner"},
                        "timestamp": "2026-05-04T12:01:00+00:00",
                        "content": "We decided to add an explicit timeout guard and keep the queue.",
                    },
                    {
                        "author": {"role": "user", "name": "Operator"},
                        "timestamp": "2026-05-04T12:02:00+00:00",
                        "content": "Next step: implement the timeout guard today.",
                    },
                ]
            },
            modified_at=self.now - timedelta(days=1),
        )

        conversation = conversation_extractor.parse_session_file(session_path)
        assert conversation is not None

        summary = conversation_extractor.generate_summary(conversation)
        self.assertIn("key_points", summary)
        self.assertTrue(summary["learnings"])
        self.assertTrue(summary["decisions_made"])
        self.assertTrue(summary["actions_taken"])
        self.assertEqual("archive-me", summary["metadata"]["session_id"])

        first_archive = conversation_extractor.archive_to_memory(conversation, project_root=self.root)
        self.assertFalse(first_archive["duplicate"])
        self.assertTrue(Path(first_archive["json_path"]).exists())
        self.assertTrue(Path(first_archive["markdown_path"]).exists())

        archive_json = json.loads(Path(first_archive["json_path"]).read_text(encoding="utf-8"))
        self.assertEqual("archive-me", archive_json["session_id"])
        self.assertIn("summary", archive_json)
        self.assertEqual(3, archive_json["message_count"])

        archive_markdown = Path(first_archive["markdown_path"]).read_text(encoding="utf-8")
        self.assertIn("## Decisions Made", archive_markdown)
        self.assertIn("## Actions Taken", archive_markdown)
        self.assertIn("timeout guard", archive_markdown)

        duplicate_archive = conversation_extractor.archive_to_memory(conversation, project_root=self.root)
        self.assertTrue(duplicate_archive["duplicate"])

        index_path = self.root / ".learnings" / "conversations" / "index.json"
        records = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(records))

    def test_cli_extract_archives_conversations_with_colorized_output(self) -> None:
        self.write_session(
            "transcript.log",
            (
                "User: I learned the deployment drift came from an unset timeout.\n"
                "Assistant: Decision: we will keep the worker and add a timeout guard.\n"
                "User: Actually the previous diagnosis was wrong. Next step: implement the timeout change.\n"
            ),
            modified_at=self.now - timedelta(hours=3),
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = conversation_extractor.main(
                ["extract", "--session-path", str(self.sessions_dir), "--days", "7"],
                root=self.root,
                now=self.now,
            )

        output = stdout.getvalue()
        self.assertEqual(0, exit_code)
        self.assertIn("\033[", output)
        self.assertIn("Found 1 valuable conversation(s).", output)
        self.assertIn("Archived 1 conversation(s); skipped 0 duplicate(s).", output)
        self.assertTrue((self.root / ".learnings" / "conversations" / "index.json").exists())

    def test_cli_list_and_stats_show_archived_metadata(self) -> None:
        for name, offset_days in (("first.json", 1), ("second.json", 2)):
            session_path = self.write_json_session(
                name,
                {
                    "messages": [
                        {
                            "role": "user",
                            "timestamp": f"2026-05-0{offset_days}T08:00:00+00:00",
                            "content": "We learned the scheduler needed a queue backoff policy.",
                        },
                        {
                            "role": "assistant",
                            "timestamp": f"2026-05-0{offset_days}T08:01:00+00:00",
                            "content": "Decision: we will add queue backoff and keep the scheduler simple.",
                        },
                        {
                            "role": "user",
                            "timestamp": f"2026-05-0{offset_days}T08:02:00+00:00",
                            "content": "Next step: implement the backoff change.",
                        },
                    ]
                },
                modified_at=self.now - timedelta(days=offset_days),
            )
            conversation = conversation_extractor.parse_session_file(session_path)
            assert conversation is not None
            conversation_extractor.archive_to_memory(conversation, project_root=self.root)

        list_stdout = io.StringIO()
        with redirect_stdout(list_stdout):
            list_exit = conversation_extractor.main(["list", "--limit", "10"], root=self.root, now=self.now)

        stats_stdout = io.StringIO()
        with redirect_stdout(stats_stdout):
            stats_exit = conversation_extractor.main(["stats"], root=self.root, now=self.now)

        self.assertEqual(0, list_exit)
        self.assertEqual(0, stats_exit)
        self.assertIn("Archived conversations", list_stdout.getvalue())
        self.assertIn("Participants:", list_stdout.getvalue())
        self.assertIn("Conversation archive stats", stats_stdout.getvalue())
        self.assertIn("Total archived:", stats_stdout.getvalue())
        self.assertIn("Top topics", stats_stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
