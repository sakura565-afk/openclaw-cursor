"""Tests for ``scripts.self_improvement.auto_reflection``."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "self_improvement" / "auto_reflection.py"
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SPEC = importlib.util.spec_from_file_location("self_improvement_auto_reflection", MODULE_PATH)
auto_reflection = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("self_improvement_auto_reflection", auto_reflection)
SPEC.loader.exec_module(auto_reflection)


class SelfImprovementAutoReflectionTests(unittest.TestCase):
    def test_read_and_extract_accepts_root_tuple(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            log_path = root / "logs" / "run.log"
            log_path.parent.mkdir(parents=True)
            log_path.write_text("Lesson learned: always pin dependencies.\n", encoding="utf-8")
            roots = (root.resolve(),)
            insights = auto_reflection.read_and_extract(log_path, roots)
        self.assertTrue(any("pin" in i.text.lower() for i in insights))

    def test_sessions_history_adds_session_file_and_writes_openclaw_memory(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = base / "repo"
            oc = base / "openclaw_ws"
            repo.mkdir()
            oc.mkdir()
            (oc / "memory").mkdir(parents=True)

            (repo / "logs").mkdir(parents=True)
            (repo / "logs" / "stub.log").write_text("Lesson learned: from repo log.\n", encoding="utf-8")

            sess_rel = "memory/s_hist/session.json"
            sess = oc / sess_rel
            sess.parent.mkdir(parents=True, exist_ok=True)
            sess.write_text(
                json.dumps(
                    {
                        "messages": [
                            {"role": "assistant", "content": "Decision: prefer sqlite for tests."},
                            {"role": "assistant", "content": "Error: connection reset by peer"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            hist = oc / "memory" / "sessions_history.json"
            hist.write_text(
                json.dumps({"recent": [{"session_path": sess_rel}]}),
                encoding="utf-8",
            )

            run = auto_reflection.run_reflection(
                repo,
                since_hours=24,
                extra_globs=[],
                dry_run=False,
                openclaw_workspace=oc,
                skip_openclaw_memory=False,
            )
            self.assertGreaterEqual(run.files_scanned, 1)
            day = datetime.fromisoformat(run.started_at_utc.replace("Z", "+00:00")).date().isoformat()
            daily = oc / "memory" / f"{day}.md"
            self.assertTrue(daily.is_file(), msg=f"expected {daily}")
            mem = oc / "MEMORY.md"
            self.assertTrue(mem.is_file())
            mem_body = mem.read_text(encoding="utf-8")
            self.assertIn("Auto-reflection", mem_body)
            self.assertIn(run.run_id, mem_body)

    def test_skip_openclaw_memory_skips_workspace_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = base / "repo"
            oc = base / "oc"
            repo.mkdir()
            oc.mkdir()
            (repo / "logs").mkdir(parents=True)
            (repo / "logs" / "a.log").write_text("Lesson learned: skip test.\n", encoding="utf-8")

            auto_reflection.run_reflection(
                repo,
                since_hours=24,
                extra_globs=[],
                dry_run=False,
                openclaw_workspace=oc,
                skip_openclaw_memory=True,
            )
            self.assertFalse((oc / "MEMORY.md").exists())
            self.assertEqual(list((oc / "memory").glob("*.md")) if (oc / "memory").exists() else [], [])


if __name__ == "__main__":
    unittest.main()
