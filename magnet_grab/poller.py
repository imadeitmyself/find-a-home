"""Telegram long-poll trigger: text a magnet to the bot and it downloads it.

This is the most friction-free way to start a download from a phone while on the
move — no web page, no token in a URL. Just send the magnet link as a message to
the same bot that find-a-home uses, and you get the per-file links back when it's
done. Only messages from the configured chat id are accepted.
"""

from __future__ import annotations

import logging
import re
import time
from typing import List

from .downloader import Downloader
from .magnet import parse_magnet
from .telegram import TelegramClient

logger = logging.getLogger("magnet_grab")

# Magnet links run to the next whitespace; tolerate trailing punctuation.
_MAGNET_RE = re.compile(r"magnet:\?[^\s]+", re.IGNORECASE)


def extract_magnets(text: str) -> List[str]:
    return [m.rstrip(").,]") for m in _MAGNET_RE.findall(text or "")]


class TelegramPoller:
    def __init__(
        self,
        client: TelegramClient,
        downloader: Downloader,
        allowed_chat_id: str,
        long_poll_seconds: int = 30,
        error_backoff_seconds: int = 5,
    ) -> None:
        self.client = client
        self.downloader = downloader
        self.allowed_chat_id = str(allowed_chat_id)
        self.long_poll_seconds = long_poll_seconds
        self.error_backoff_seconds = error_backoff_seconds
        self._offset = None
        self._running = False

    def run_forever(self) -> None:
        self._running = True
        logger.info("Telegram trigger active — send a magnet link to the bot to start a download.")
        while self._running:
            try:
                updates = self.client.get_updates(self._offset, self.long_poll_seconds)
            except Exception as exc:  # noqa: BLE001 - keep polling through transient errors
                logger.warning("getUpdates failed (%s); retrying", exc)
                time.sleep(self.error_backoff_seconds)
                continue
            for update in updates:
                self._offset = max(self._offset or 0, update.get("update_id", 0) + 1)
                try:
                    self.handle_update(update)
                except Exception as exc:  # noqa: BLE001 - one bad message must not stop the loop
                    logger.error("Failed to handle update: %s", exc)

    def stop(self) -> None:
        self._running = False

    def handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("channel_post")
        if not message:
            return
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            logger.info("Ignoring message from unauthorized chat %s", chat_id)
            return
        text = message.get("text", "") or message.get("caption", "")
        magnets = extract_magnets(text)
        if not magnets:
            return
        for magnet in magnets:
            try:
                job = self.downloader.submit(magnet)
            except ValueError as exc:
                self._reply("⚠️ Not a usable magnet link: %s" % exc)
                continue
            self._reply("🧲 Queued: %s\nYou'll get the download links here when it's ready." % job.name)

    def _reply(self, text: str) -> None:
        try:
            self.client.send_message(text)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send Telegram reply: %s", exc)
