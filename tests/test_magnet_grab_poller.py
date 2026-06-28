import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from magnet_grab.config import Config, load_config
from magnet_grab.downloader import Downloader
from magnet_grab.poller import TelegramPoller, extract_magnets

MAGNET = "magnet:?xt=urn:btih:297C945AB4913AE6D215AA1FD61739A8B9A12534&dn=Demo"


def make_config(download_dir: Path) -> Config:
    return Config(
        host="0.0.0.0",
        port=8800,
        download_dir=download_dir,
        public_url="http://vps:8800",
        access_token="",
        telegram_token="tok",
        telegram_chat_id="555",
        aria2c_path="aria2c",
        request_timeout=20,
    )


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send_message(self, text):
        self.sent.append(text)


def _wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _runner(cmd):
    job_dir = next(Path(a[6:]) for a in cmd if a.startswith("--dir="))
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "file.bin").write_bytes(b"x")
    return subprocess.CompletedProcess(cmd, 0, "ok", "")


class ExtractMagnetTests(unittest.TestCase):
    def test_extracts_from_surrounding_text(self):
        text = "please grab %s thanks" % MAGNET
        self.assertEqual(extract_magnets(text), [MAGNET])

    def test_strips_trailing_punctuation(self):
        self.assertEqual(extract_magnets("(%s)" % MAGNET), [MAGNET])

    def test_no_magnet_returns_empty(self):
        self.assertEqual(extract_magnets("just a normal message"), [])


class PollerTests(unittest.TestCase):
    def _make(self, tmp):
        config = make_config(Path(tmp))
        fake = FakeTelegram()
        downloader = Downloader(config, telegram=fake, runner=_runner)
        poller = TelegramPoller(fake, downloader, allowed_chat_id="555")
        return config, fake, downloader, poller

    def test_message_with_magnet_starts_download_and_acks(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fake, downloader, poller = self._make(tmp)
            poller.handle_update(
                {"update_id": 1, "message": {"chat": {"id": 555}, "text": "grab %s" % MAGNET}}
            )
            _wait_until(lambda: downloader.jobs() and downloader.jobs()[0].status == "done")
            # ack first, completion message (with per-file link) second.
            self.assertTrue(any("Queued" in m for m in fake.sent))
            self.assertTrue(any("/files/" in m for m in fake.sent))

    def test_ignores_messages_from_other_chats(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fake, downloader, poller = self._make(tmp)
            poller.handle_update(
                {"update_id": 1, "message": {"chat": {"id": 999}, "text": MAGNET}}
            )
            self.assertEqual(fake.sent, [])
            self.assertEqual(downloader.jobs(), [])

    def test_non_magnet_message_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fake, downloader, poller = self._make(tmp)
            poller.handle_update(
                {"update_id": 1, "message": {"chat": {"id": 555}, "text": "hello bot"}}
            )
            self.assertEqual(fake.sent, [])

    def test_invalid_magnet_text_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fake, downloader, poller = self._make(tmp)
            poller.handle_update(
                {"update_id": 1, "message": {"chat": {"id": 555}, "text": "magnet:?dn=nohash"}}
            )
            self.assertTrue(any("Not a usable magnet" in m for m in fake.sent))


class PollLoopTests(unittest.TestCase):
    def test_run_forever_processes_one_batch_then_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            fake = FakeTelegram()
            downloader = Downloader(config, telegram=fake, runner=_runner)

            class OneShotClient(FakeTelegram):
                def __init__(self, poller_box):
                    super().__init__()
                    self.poller_box = poller_box
                    self.calls = 0

                def get_updates(self, offset=None, long_poll_seconds=30):
                    self.calls += 1
                    if self.calls == 1:
                        return [
                            {"update_id": 7, "message": {"chat": {"id": 555}, "text": MAGNET}}
                        ]
                    self.poller_box[0].stop()  # stop after the first batch
                    return []

            box = []
            client = OneShotClient(box)
            downloader.telegram = client
            poller = TelegramPoller(client, downloader, allowed_chat_id="555", long_poll_seconds=0)
            box.append(poller)
            poller.run_forever()

            self.assertEqual(poller._offset, 8)  # update_id + 1
            self.assertTrue(any("Queued" in m for m in client.sent))


class ConfigPollFlagTests(unittest.TestCase):
    def test_poll_enabled_by_default(self):
        config = load_config({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"})
        self.assertTrue(config.telegram_poll)

    def test_poll_can_be_disabled(self):
        config = load_config(
            {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "MAGNET_GRAB_TELEGRAM_POLL": "false"}
        )
        self.assertFalse(config.telegram_poll)


if __name__ == "__main__":
    unittest.main()
