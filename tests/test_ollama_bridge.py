from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import ollama_bridge  # noqa: E402


class OllamaBridgeReadyTests(unittest.TestCase):
    def test_ready_get_returns_plain_text_ready(self) -> None:
        bridge = ollama_bridge.OllamaBridge()
        for path in ("/ready", "/v1/ready"):
            with self.subTest(path=path):
                body, status = bridge.route(path, "GET", None)
                self.assertEqual(status, 200)
                self.assertEqual(body, "READY")
                self.assertIsInstance(body, str)

    def test_ready_post_is_not_routed(self) -> None:
        bridge = ollama_bridge.OllamaBridge()
        body, status = bridge.route("/ready", "POST", {})
        self.assertEqual(status, 404)
        self.assertIsInstance(body, dict)


if __name__ == "__main__":
    unittest.main()
