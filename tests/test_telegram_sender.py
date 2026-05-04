import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from scripts.media_tool import PreparedFile
from scripts.telegram_sender import (
    DEFAULT_GROUP_LIMIT,
    ProgressFile,
    TelegramConfig,
    TelegramSender,
    TelegramSenderError,
    build_parser,
    main,
)


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        return self._payload


class ProgressFileTests(unittest.TestCase):
    def test_reports_progress_until_complete(self):
        messages = []
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.bin"
            path.write_bytes(b"a" * 100)
            with ProgressFile(path, reporter=messages.append) as handle:
                while handle.read(15):
                    pass
        self.assertTrue(any("10%" in message for message in messages))
        self.assertTrue(any("100%" in message for message in messages))


class TelegramConfigTests(unittest.TestCase):
    def test_from_env_requires_values(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TelegramSenderError) as ctx:
                TelegramConfig.from_env()
        self.assertIn("TELEGRAM_BOT_TOKEN", str(ctx.exception))
        self.assertIn("TELEGRAM_CHAT_ID", str(ctx.exception))


class TelegramSenderTests(unittest.TestCase):
    def setUp(self):
        self.config = TelegramConfig(token="token", chat_id="chat", retries=3)
        self.messages = []

    def create_sender(self, **kwargs):
        return TelegramSender(self.config, reporter=self.messages.append, **kwargs)

    def test_send_photo_uses_resize_and_uploads(self):
        request_calls = []

        def fake_request(**kwargs):
            request_calls.append(kwargs)
            file_tuple = kwargs["files"]["photo"]
            content = file_tuple[1].read()
            self.assertEqual(content, b"img-data")
            return DummyResponse({"ok": True, "result": {"message_id": 1}})

        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "image.jpg"
            image_path.write_bytes(b"img-data")

            def fake_resize(path, reporter):
                self.assertEqual(Path(path), image_path)
                reporter("resize called")
                return PreparedFile(
                    path=image_path,
                    temporary=False,
                    original_size=image_path.stat().st_size,
                    final_size=image_path.stat().st_size,
                    resized=False,
                )

            sender = self.create_sender(request_func=fake_request, resize_func=fake_resize)
            payload = sender.send_photo(image_path, caption="hello")

        self.assertEqual(payload["result"]["message_id"], 1)
        self.assertEqual(request_calls[0]["data"]["caption"], "hello")
        self.assertIn("resize called", self.messages)

    def test_send_group_rejects_more_than_limit(self):
        sender = self.create_sender()
        with self.assertRaises(TelegramSenderError):
            sender.send_group([f"img-{i}.jpg" for i in range(DEFAULT_GROUP_LIMIT + 1)])

    def test_send_group_builds_media_payload(self):
        request_calls = []

        def fake_request(**kwargs):
            request_calls.append(kwargs)
            media = json.loads(kwargs["data"]["media"])
            self.assertEqual(media[0]["caption"], "caption")
            self.assertEqual(media[1]["media"], "attach://photo1")
            self.assertEqual(sorted(kwargs["files"].keys()), ["photo0", "photo1"])
            return DummyResponse({"ok": True, "result": []})

        with tempfile.TemporaryDirectory() as tmpdir:
            image_paths = []
            for index in range(2):
                path = Path(tmpdir) / f"img-{index}.jpg"
                path.write_bytes(f"img-{index}".encode())
                image_paths.append(path)

            sender = self.create_sender(
                request_func=fake_request,
                resize_func=lambda path, reporter: PreparedFile(
                    path=Path(path),
                    temporary=False,
                    original_size=Path(path).stat().st_size,
                    final_size=Path(path).stat().st_size,
                    resized=False,
                ),
            )
            payload = sender.send_group(image_paths, caption="caption")

        self.assertEqual(payload["ok"], True)
        self.assertEqual(len(request_calls), 1)

    def test_request_retries_then_succeeds(self):
        calls = {"count": 0}
        sleep_calls = []

        def flaky_request(**kwargs):
            calls["count"] += 1
            if calls["count"] < 3:
                raise requests.ConnectionError("network")
            return DummyResponse({"ok": True, "result": {"message_id": 55}})

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "doc.bin"
            path.write_bytes(b"hello")
            sender = self.create_sender(
                request_func=flaky_request,
                sleep_func=sleep_calls.append,
            )
            payload = sender.send_document(path)

        self.assertEqual(payload["result"]["message_id"], 55)
        self.assertEqual(sleep_calls, [1, 2])

    def test_send_document_checks_size_limit(self):
        sender = self.create_sender()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "big.bin"
            path.write_bytes(b"x")
            with mock.patch.object(Path, "stat", return_value=mock.Mock(st_size=60 * 1024 * 1024)):
                with self.assertRaises(TelegramSenderError):
                    sender.send_document(path)


class ParserAndCliTests(unittest.TestCase):
    def test_parser_handles_send_group(self):
        parser = build_parser()
        args = parser.parse_args(["send-group", "a.jpg", "b.jpg", "--caption", "hi"])
        self.assertEqual(args.command, "send-group")
        self.assertEqual(args.images, ["a.jpg", "b.jpg"])
        self.assertEqual(args.caption, "hi")

    def test_main_dispatches_send_photo(self):
        with mock.patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "chat"},
            clear=True,
        ):
            with mock.patch("scripts.telegram_sender.TelegramSender") as sender_cls:
                sender_cls.return_value.send_photo.return_value = {"ok": True, "result": {"x": 1}}
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(["send-photo", "photo.jpg", "--caption", "hi"])
        self.assertEqual(exit_code, 0)
        sender_cls.return_value.send_photo.assert_called_once_with("photo.jpg", caption="hi")
        self.assertIn('"ok": true', stdout.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
