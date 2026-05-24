import os
import unittest

from rental_alert_bot.notifiers import (
    TELEGRAM_MAX_CHARS,
    TelegramNotifier,
    build_report_notifiers_from_env,
    chunk_text,
)


class ChunkTextTests(unittest.TestCase):
    def test_short_message_stays_single_chunk(self):
        self.assertEqual(chunk_text("hello\nworld"), ["hello\nworld"])

    def test_empty_message_yields_one_empty_chunk(self):
        self.assertEqual(chunk_text(""), [""])

    def test_long_message_splits_under_limit_without_losing_lines(self):
        lines = ["line %04d %s" % (i, "x" * 50) for i in range(400)]
        message = "\n".join(lines)
        self.assertGreater(len(message), TELEGRAM_MAX_CHARS)

        chunks = chunk_text(message)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), TELEGRAM_MAX_CHARS)
        # Every original line survives, in order.
        self.assertEqual("\n".join(chunks).split("\n"), lines)

    def test_single_oversized_line_is_hard_split(self):
        message = "z" * (TELEGRAM_MAX_CHARS * 2 + 5)
        chunks = chunk_text(message)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), TELEGRAM_MAX_CHARS)
        self.assertEqual("".join(chunks), message)


class ReportNotifierTests(unittest.TestCase):
    EMAIL_KEYS = [
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "MAILGUN_API_KEY", "MAILGUN_DOMAIN", "MAILGUN_TO", "MAILGUN_FROM",
        "EMAIL_SMTP_HOST", "EMAIL_FROM", "EMAIL_TO",
    ]

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self.EMAIL_KEYS}

    def tearDown(self):
        for key in self.EMAIL_KEYS:
            os.environ.pop(key, None)
            if self._saved.get(key) is not None:
                os.environ[key] = self._saved[key]

    def test_no_config_returns_empty(self):
        self.assertEqual(build_report_notifiers_from_env(), [])

    def test_telegram_and_mailgun_both_included_telegram_first(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
        os.environ["TELEGRAM_CHAT_ID"] = "123"
        os.environ["MAILGUN_API_KEY"] = "key"
        os.environ["MAILGUN_DOMAIN"] = "mg.example.com"
        os.environ["MAILGUN_TO"] = "me@example.com"

        channels = [getattr(n, "channel", "?") for n in build_report_notifiers_from_env()]
        self.assertEqual(channels, ["telegram", "mailgun"])


class TelegramSendReportTests(unittest.TestCase):
    def test_send_report_prepends_subject_and_chunks(self):
        notifier = TelegramNotifier(token="t", chat_id="c")
        captured = []
        notifier._send_chunk = captured.append  # type: ignore[method-assign]

        body = "\n".join("row %d %s" % (i, "y" * 60) for i in range(400))
        notifier.send_report("SUBJECT LINE", body)

        self.assertGreater(len(captured), 1)
        self.assertTrue(captured[0].startswith("SUBJECT LINE"))
        for chunk in captured:
            self.assertLessEqual(len(chunk), TELEGRAM_MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
