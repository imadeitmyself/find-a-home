"""Minimal Telegram sender (standard library only).

Mirrors find-a-home's notifier so the same bot token works for both projects.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import List

TELEGRAM_MAX_CHARS = 4000  # Telegram hard-limits a message to 4096; leave headroom.


def chunk_text(text: str, limit: int = TELEGRAM_MAX_CHARS) -> List[str]:
    """Split text into <=limit chunks, preferring line breaks; hard-split long lines."""
    chunks: List[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            head, line = line[:limit], line[limit:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


class TelegramClient:
    def __init__(self, token: str, chat_id: str, timeout_seconds: int = 20) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds

    def send_message(self, message: str) -> None:
        for chunk in chunk_text(message):
            self._send_chunk(chunk)

    def _send_chunk(self, message: str) -> None:
        payload = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": message,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % self.token,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status >= 300:
                    raise RuntimeError("Telegram send failed with HTTP %s" % response.status)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError("Telegram send failed with HTTP %s: %s" % (exc.code, body)) from exc


def get_bot_info(token: str, timeout_seconds: int = 20) -> str:
    request = urllib.request.Request("https://api.telegram.org/bot%s/getMe" % token)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8", errors="replace")
