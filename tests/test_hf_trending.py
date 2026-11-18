from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import hf_trending as hf  # noqa: E402


class FamilyFilterTests(unittest.TestCase):
    def test_qwen_prefix(self) -> None:
        self.assertEqual(hf.match_family("Qwen/Qwen3.8-27B"), "qwen")

    def test_llama_prefix(self) -> None:
        self.assertEqual(hf.match_family("meta-llama/Llama-4-70B"), "llama")

    def test_unmatched(self) -> None:
        self.assertIsNone(hf.match_family("random/foo"))

    def test_devstral_before_mistral(self) -> None:
        self.assertEqual(hf.match_family("mistralai/Devstral-Small-2"), "devstral")

    def test_ministral_before_mistral(self) -> None:
        self.assertEqual(hf.match_family("mistralai/Ministral-8B-Instruct"), "ministral")


class StateIoTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hf_seen.json"
            state = {
                "schema_version": 1,
                "last_run": "2026-08-21T08:00:00Z",
                "seen": {
                    "Qwen/Qwen3.8-27B": "2026-08-19",
                    "google/gemma-4-31b": "2026-08-15",
                },
            }
            hf.save_state(path, state)
            loaded = hf.load_state(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["schema_version"], 1)
            self.assertEqual(loaded["last_run"], "2026-08-21T08:00:00Z")
            self.assertEqual(loaded["seen"]["Qwen/Qwen3.8-27B"], "2026-08-19")
            # serialize → deserialize via text
            again = json.loads(hf.serialize_state(loaded))
            self.assertEqual(again["seen"], loaded["seen"])


class DiffTests(unittest.TestCase):
    def test_fresh_minus_seen(self) -> None:
        fresh = [
            {"id": "Qwen/A", "family": "qwen", "downloads": 100, "lastModified": "2026-08-21"},
            {"id": "google/B", "family": "gemma", "downloads": 50, "lastModified": "2026-08-20"},
            {"id": "Qwen/C", "family": "qwen", "downloads": 200, "lastModified": "2026-08-21"},
        ]
        seen = {"google/B": "2026-08-01"}
        diff = hf.diff_models(fresh, seen)
        self.assertEqual([m["id"] for m in diff], ["Qwen/C", "Qwen/A"])


class AlertFormatterTests(unittest.TestCase):
    def test_empty_diff_no_alert(self) -> None:
        self.assertIsNone(hf.format_alert([]))

    def test_three_entries_three_bullets(self) -> None:
        diff = [
            {
                "id": "Qwen/Qwen3.9-7B-Instruct",
                "family": "qwen",
                "downloads": 12400,
                "lastModified": "2026-08-21",
            },
            {
                "id": "google/gemma-4-9b",
                "family": "gemma",
                "downloads": 8100,
                "lastModified": "2026-08-21",
            },
            {
                "id": "mistralai/Devstral-Small-2",
                "family": "devstral",
                "downloads": 5600,
                "lastModified": "2026-08-20",
            },
        ]
        text = hf.format_alert(diff)
        assert text is not None
        bullets = [line for line in text.splitlines() if line.startswith("• ")]
        self.assertEqual(len(bullets), 3)
        self.assertIn("12.4k downloads", text)
        self.assertIn("Open HF: https://huggingface.co/Qwen/Qwen3.9-7B-Instruct", text)


class FetchMockTests(unittest.TestCase):
    def test_fetch_models_uses_urllib_and_token(self) -> None:
        payload = [
            {"id": "Qwen/Qwen3.8-27B", "downloads": 10, "lastModified": "2026-08-19T00:00:00.000Z"},
            {"id": "random/foo", "downloads": 99, "lastModified": "2026-08-19T00:00:00.000Z"},
        ]
        raw = json.dumps(payload).encode("utf-8")

        class FakeResp:
            def read(self) -> bytes:
                return raw

            def __enter__(self) -> "FakeResp":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        captured: dict[str, object] = {}

        def fake_urlopen(req: object, timeout: float = 0) -> FakeResp:
            captured["req"] = req
            captured["timeout"] = timeout
            return FakeResp()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            items = hf.fetch_models(
                "downloads",
                50,
                env={"HF_TOKEN": "hf_test_token"},
            )

        self.assertEqual(len(items), 2)
        req = captured["req"]
        headers = {k.lower(): v for k, v in req.header_items()}  # type: ignore[attr-defined]
        self.assertEqual(headers.get("user-agent"), hf.USER_AGENT)
        self.assertEqual(headers.get("authorization"), "Bearer hf_test_token")
        self.assertIn("sort=downloads", req.full_url)  # type: ignore[attr-defined]

    def test_run_watcher_first_run_seeds_without_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fetch(sort: str, limit: int) -> list[dict]:
                return [
                    {
                        "id": "Qwen/Qwen3.8-27B",
                        "downloads": 1000,
                        "lastModified": "2026-08-19T12:00:00.000Z",
                    }
                ]

            alert = mock.Mock(return_value={"ok": True})
            with mock.patch.object(hf, "send_telegram_alert", alert):
                report = hf.run_watcher(
                    root=root,
                    fetch=fetch,
                    send_alert=True,
                    update_state=True,
                )

            self.assertTrue(report["first_run"])
            self.assertEqual(report["new_count"], 0)
            self.assertEqual(report["diff"], [])
            alert.assert_not_called()
            state = hf.load_state(hf.state_path(root))
            assert state is not None
            self.assertIn("Qwen/Qwen3.8-27B", state["seen"])

    def test_run_watcher_detects_new_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hf.save_state(
                hf.state_path(root),
                {
                    "schema_version": 1,
                    "last_run": "2026-08-20T08:00:00Z",
                    "seen": {"Qwen/Qwen3-Old": "2026-08-01"},
                },
            )

            def fetch(sort: str, limit: int) -> list[dict]:
                return [
                    {"id": "Qwen/Qwen3-Old", "downloads": 10, "lastModified": "2026-08-01"},
                    {"id": "Qwen/Qwen3-New", "downloads": 5000, "lastModified": "2026-08-21"},
                    {"id": "random/foo", "downloads": 99999, "lastModified": "2026-08-21"},
                ]

            report = hf.run_watcher(root=root, fetch=fetch, send_alert=False, update_state=False)
            self.assertEqual(report["new_count"], 1)
            self.assertEqual(report["diff"][0]["id"], "Qwen/Qwen3-New")
            self.assertEqual(report["diff"][0]["family"], "qwen")


class CliDryRunTests(unittest.TestCase):
    def test_default_cli_is_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fetch(sort: str, limit: int) -> list[dict]:
                return [{"id": "Qwen/X", "downloads": 1, "lastModified": "2026-08-21"}]

            # Seed state so there is a non-empty diff path we can observe.
            hf.save_state(
                hf.state_path(root),
                {"schema_version": 1, "last_run": "2026-08-20T00:00:00Z", "seen": {}},
            )

            with mock.patch.object(hf, "pull_models", return_value=[
                {"id": "Qwen/X", "family": "qwen", "downloads": 1, "lastModified": "2026-08-21"}
            ]):
                buf = io.StringIO()
                with mock.patch("sys.stdout", buf):
                    code = hf.main(["--root", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("Qwen/X", buf.getvalue())
            # dry-run must not update state
            loaded = hf.load_state(hf.state_path(root))
            assert loaded is not None
            self.assertEqual(loaded["seen"], {})


if __name__ == "__main__":
    unittest.main()
