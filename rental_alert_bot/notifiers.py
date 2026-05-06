from __future__ import annotations

import os
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
import json
from email.message import EmailMessage
from typing import List, Optional

from .config import env_bool
from .models import Listing


class Notifier:
    channel = "unknown"

    def send(self, listing: Listing, message: str) -> None:
        raise NotImplementedError


class TelegramNotifier(Notifier):
    channel = "telegram"

    def __init__(self, token: str, chat_id: str, timeout_seconds: int = 15) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds

    def send(self, listing: Listing, message: str) -> None:
        payload = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": message,
                "disable_web_page_preview": "false",
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


def get_telegram_bot_info(token: str, timeout_seconds: int = 15) -> dict:
    return _telegram_get_json(token, "getMe", timeout_seconds)


def get_telegram_updates(token: str, timeout_seconds: int = 15) -> dict:
    return _telegram_get_json(token, "getUpdates", timeout_seconds)


def _telegram_get_json(token: str, method: str, timeout_seconds: int) -> dict:
    request = urllib.request.Request("https://api.telegram.org/bot%s/%s" % (token, method))
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("Telegram %s failed with HTTP %s: %s" % (method, exc.code, body)) from exc


class EmailNotifier(Notifier):
    channel = "email"

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        recipient: str,
        use_tls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.recipient = recipient
        self.use_tls = use_tls

    def send(self, listing: Listing, message: str) -> None:
        email = EmailMessage()
        email["From"] = self.sender
        email["To"] = self.recipient
        email["Subject"] = "Rental alert: %s" % listing.title
        email.set_content(message)

        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            if self.use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(email)


class CompositeNotifier(Notifier):
    channel = "composite"

    def __init__(self, notifiers: List[Notifier]) -> None:
        self.notifiers = notifiers

    def send(self, listing: Listing, message: str) -> None:
        for notifier in self.notifiers:
            notifier.send(listing, message)


class PrintNotifier(Notifier):
    channel = "print"

    def send(self, listing: Listing, message: str) -> None:
        print(message)


def build_notifier_from_env(timeout_seconds: int = 15, allow_print: bool = False) -> Notifier:
    notifiers: List[Notifier] = []
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        notifiers.append(TelegramNotifier(token=token, chat_id=chat_id, timeout_seconds=timeout_seconds))

    email_notifier = _email_notifier_from_env()
    if email_notifier:
        notifiers.append(email_notifier)

    if not notifiers and allow_print:
        return PrintNotifier()
    if not notifiers:
        raise RuntimeError("No notifier configured. Set Telegram or email values in .env.")
    if len(notifiers) == 1:
        return notifiers[0]
    return CompositeNotifier(notifiers)


def _email_notifier_from_env() -> Optional[EmailNotifier]:
    env = os.environ
    host = env.get("EMAIL_SMTP_HOST", "")
    sender = env.get("EMAIL_FROM", "")
    recipient = env.get("EMAIL_TO", "")
    if not host or not sender or not recipient:
        return None
    return EmailNotifier(
        host=host,
        port=int(env.get("EMAIL_SMTP_PORT", "587") or "587"),
        username=env.get("EMAIL_SMTP_USERNAME", ""),
        password=env.get("EMAIL_SMTP_PASSWORD", ""),
        sender=sender,
        recipient=recipient,
        use_tls=env_bool(env, "EMAIL_USE_TLS", True),
    )


def format_alert(listing: Listing) -> str:
    price = "GBP %s pcm" % listing.price_pcm if listing.price_pcm is not None else "Price unknown"
    beds = "%s bed" % listing.bedrooms if listing.bedrooms is not None else "Beds unknown"
    area = listing.postcode_area or "Area unknown"
    heading = "New rental match"
    if listing.metadata.get("recent_reason"):
        heading = "%s: %s" % (heading, listing.metadata["recent_reason"])
    return "\n".join(
        [
            heading,
            "%s" % listing.title,
            "%s | %s | %s" % (price, beds, area),
            "Source: %s" % listing.source,
            listing.url,
        ]
    )
