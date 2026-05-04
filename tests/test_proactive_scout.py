from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.proactive_scout import (  # noqa: E402
    CACHE_TTL_SECONDS,
    _cache_path,
    _job_path,
    _run_worker,
    _write_json,
    scout_check,
    scout_clear,
    scout_predict,
    scout_run_background,
    scout_status,
)


class ProactiveScoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.scout_dir = Path(self.tempdir.name) / "scout"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_scout_predict_uses_fast_model_and_limits_to_two_predictions(self) -> None:
        predictions = scout_predict(
            "code",
            "Implemented parser cleanup with duplicate branches removed, but no tests were added.",
        )

        self.assertEqual(len(predictions), 2)
        self.assertEqual(predictions[0]["intent"], "add_tests")
        self.assertEqual(predictions[0]["model"], "openclaw-fast")
        self.assertEqual({item["intent"] for item in predictions}, {"add_tests", "refactor"})

    def test_scout_run_background_skips_when_idle_threshold_not_met(self) -> None:
        predictions = scout_predict("analysis", "Short answer.")

        result = scout_run_background(predictions, idle_seconds=4, scout_dir=self.scout_dir)

        self.assertIn("idle threshold not met", result["reason"])
        self.assertEqual(result["started"], [])
        self.assertEqual(result["cached"], [])
        self.assertEqual(len(result["skipped"]), 2)
        self.assertFalse((self.scout_dir / "jobs").exists())

    def test_worker_populates_cache_and_check_returns_cached_entry(self) -> None:
        predictions = scout_predict(
            "code",
            "Added a new CLI entry point and persistence layer, but there are no tests yet.",
        )

        with mock.patch("scripts.proactive_scout.subprocess.Popen") as popen:
            summary = scout_run_background(predictions, idle_seconds=16, scout_dir=self.scout_dir)

        self.assertEqual(len(summary["started"]), 2)
        popen.assert_called()

        job_files = sorted((self.scout_dir / "jobs").glob("*.json"))
        self.assertEqual(len(job_files), 2)

        intents_by_question = {item["question"]: item["intent"] for item in predictions}
        launched_questions = set(summary["started"])
        self.assertTrue(launched_questions)

        for job_file in job_files:
            worker_exit = _run_worker(job_file, self.scout_dir)
            self.assertEqual(worker_exit, 0)

        launched_test_question = next(
            question for question in launched_questions if intents_by_question[question] == "add_tests"
        )
        cached = scout_check(launched_test_question, scout_dir=self.scout_dir)
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual(cached["intent"], "add_tests")
        self.assertIn("Prepared follow-up for test coverage.", cached["prepared_response"])

        status = scout_status(self.scout_dir)
        self.assertEqual(len(status["active_cache_entries"]), 2)
        self.assertTrue(all(job["status"] == "completed" for job in status["jobs"]))

    def test_expired_cache_entries_are_purged_on_lookup(self) -> None:
        now = time.time()
        cache_key = "expired-entry"
        _write_json(
            _cache_path(self.scout_dir, cache_key),
            {
                "cache_key": cache_key,
                "task_type": "analysis",
                "intent": "more_details",
                "question": "Can you go into more detail on this analysis?",
                "normalized_question": "can you go into more detail on this analysis",
                "match_terms": ["more details"],
                "model": "openclaw-fast",
                "prepared_response": "stale",
                "created_at": now - (CACHE_TTL_SECONDS + 20),
                "expires_at": now - 1,
            },
        )

        result = scout_check("more details", scout_dir=self.scout_dir)

        self.assertIsNone(result)
        self.assertFalse(_cache_path(self.scout_dir, cache_key).exists())

    def test_cli_predict_status_check_and_clear(self) -> None:
        env = os.environ.copy()
        env["OPENCLAW_SCOUT_DIR"] = str(self.scout_dir)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(ROOT) if not existing_pythonpath else os.pathsep.join([str(ROOT), existing_pythonpath])

        predict = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "proactive_scout.py"),
                "predict",
                "analysis",
                "A concise risk review of the current design.",
                "--idle-seconds",
                "16",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(predict.returncode, 0, predict.stderr)
        predict_payload = json.loads(predict.stdout)
        self.assertEqual(len(predict_payload["predictions"]), 2)
        self.assertEqual(len(predict_payload["background"]["started"]), 2)

        predicted_questions = [item["question"] for item in predict_payload["predictions"]]
        expected_intents = {item["intent"] for item in predict_payload["predictions"]}
        self.assertEqual(expected_intents, {"more_details", "what_if"})
        deadline = time.time() + 5
        cached_payload = None
        while time.time() < deadline:
            check = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "proactive_scout.py"),
                    "check",
                    predicted_questions[0],
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(check.returncode, 0, check.stderr)
            cached_payload = json.loads(check.stdout)
            if cached_payload is not None:
                break
            time.sleep(0.1)

        self.assertIsNotNone(cached_payload)
        assert cached_payload is not None
        self.assertIn(cached_payload["intent"], expected_intents)

        status = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "proactive_scout.py"), "status"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        status_payload = json.loads(status.stdout)
        self.assertTrue(status_payload["active_cache_entries"])

        cleared = scout_clear(self.scout_dir)
        self.assertGreaterEqual(cleared["cache_files"], 1)
        self.assertGreaterEqual(cleared["job_files"], 1)

        clear_cli = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "proactive_scout.py"), "clear"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(clear_cli.returncode, 0, clear_cli.stderr)
        cleared_payload = json.loads(clear_cli.stdout)
        self.assertEqual(cleared_payload["cache_files"], 0)
        self.assertEqual(cleared_payload["job_files"], 0)


if __name__ == "__main__":
    unittest.main()
