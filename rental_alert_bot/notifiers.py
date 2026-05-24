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

    def send_text(self, message: str) -> None:
        """Send a plain text message (no Listing context). Used for health alerts."""
        raise NotImplementedError

    def send_report(self, subject: str, body: str) -> None:
        """Send a long-form report. Defaults to send_text; channels with subjects override."""
        self.send_text(body)


class TelegramNotifier(Notifier):
    channel = "telegram"

    def __init__(self, token: str, chat_id: str, timeout_seconds: int = 15) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds

    def send(self, listing: Listing, message: str) -> None:
        self.send_text(message)

    def send_text(self, message: str) -> None:
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


class MailgunNotifier(Notifier):
    """Sends email via the Mailgun HTTP API (no SMTP dependency)."""

    channel = "mailgun"

    def __init__(
        self,
        api_key: str,
        domain: str,
        sender: str,
        recipient: str,
        timeout_seconds: int = 15,
        api_base: str = "https://api.mailgun.net",
    ) -> None:
        self.api_key = api_key
        self.domain = domain
        self.sender = sender
        self.recipient = recipient
        self.timeout_seconds = timeout_seconds
        self.api_base = api_base.rstrip("/")

    def send(self, listing: Listing, message: str) -> None:
        self._post("New rental: %s" % listing.title, message)

    def send_text(self, message: str) -> None:
        self._post("find-a-home: crawler alert", message)

    def send_report(self, subject: str, body: str) -> None:
        self._post(subject, body)

    def _post(self, subject: str, body: str) -> None:
        url = "%s/v3/%s/messages" % (self.api_base, self.domain)
        payload = urllib.parse.urlencode(
            {"from": self.sender, "to": self.recipient, "subject": subject, "text": body}
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": "Basic %s" % __import__("base64").b64encode(
                    ("api:%s" % self.api_key).encode()
                ).decode(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status >= 300:
                    raise RuntimeError("Mailgun send failed with HTTP %s" % response.status)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError("Mailgun send failed with HTTP %s: %s" % (exc.code, body_text)) from exc


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
        self._send_email("Rental alert: %s" % listing.title, message)

    def send_text(self, message: str) -> None:
        self._send_email("find-a-home: health alert", message)

    def send_report(self, subject: str, body: str) -> None:
        self._send_email(subject, body)

    def _send_email(self, subject: str, body: str) -> None:
        email = EmailMessage()
        email["From"] = self.sender
        email["To"] = self.recipient
        email["Subject"] = subject
        email.set_content(body)

        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            if self.use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(email)


class CompositeNotifier(Notifier):
    channel = "composite"

    def __init__(self, notifiers: List[Notifier], health_notifiers: Optional[List[Notifier]] = None) -> None:
        self.notifiers = notifiers
        self._health_notifiers = health_notifiers if health_notifiers is not None else notifiers

    def send(self, listing: Listing, message: str) -> None:
        for notifier in self.notifiers:
            notifier.send(listing, message)

    def send_text(self, message: str) -> None:
        for notifier in self._health_notifiers:
            notifier.send_text(message)


class PrintNotifier(Notifier):
    channel = "print"

    def send(self, listing: Listing, message: str) -> None:
        print(message)

    def send_text(self, message: str) -> None:
        print(message)


def build_notifier_from_env(timeout_seconds: int = 15, allow_print: bool = False) -> Notifier:
    notifiers: List[Notifier] = []
    telegram_notifiers: List[Notifier] = []

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        tg = TelegramNotifier(token=token, chat_id=chat_id, timeout_seconds=timeout_seconds)
        notifiers.append(tg)
        telegram_notifiers.append(tg)

    mailgun = _mailgun_notifier_from_env(timeout_seconds)
    if mailgun:
        notifiers.append(mailgun)
    elif _email_notifier_from_env():
        notifiers.append(_email_notifier_from_env())  # type: ignore[arg-type]

    if not notifiers and allow_print:
        return PrintNotifier()
    if not notifiers:
        raise RuntimeError("No notifier configured. Set TELEGRAM_BOT_TOKEN or MAILGUN_API_KEY in .env.")
    if len(notifiers) == 1:
        return notifiers[0]
    # Health alerts (tracker status) go to Telegram only; listing alerts go to all notifiers.
    health_notifiers = telegram_notifiers if telegram_notifiers else notifiers
    return CompositeNotifier(notifiers, health_notifiers=health_notifiers)


def build_email_notifier_from_env(timeout_seconds: int = 15) -> Optional[Notifier]:
    """Returns the configured email notifier (Mailgun or SMTP) without Telegram."""
    mailgun = _mailgun_notifier_from_env(timeout_seconds)
    if mailgun:
        return mailgun
    return _email_notifier_from_env()


def _mailgun_notifier_from_env(timeout_seconds: int = 15) -> Optional[MailgunNotifier]:
    env = os.environ
    api_key = env.get("MAILGUN_API_KEY", "").strip()
    domain = env.get("MAILGUN_DOMAIN", "").strip()
    recipient = env.get("MAILGUN_TO", env.get("EMAIL_TO", "")).strip()
    if not api_key or not domain or not recipient:
        return None
    sender = env.get("MAILGUN_FROM", "postmaster@%s" % domain).strip()
    api_base = env.get("MAILGUN_API_BASE", "https://api.mailgun.net").strip()
    return MailgunNotifier(
        api_key=api_key,
        domain=domain,
        sender=sender,
        recipient=recipient,
        timeout_seconds=timeout_seconds,
        api_base=api_base,
    )


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


def format_health_alert(rows: list) -> str:
    lines = ["Crawler health issue — %s source(s) need attention" % len(rows), ""]
    for row in rows:
        name = row["source_name"]
        outcome = row["last_outcome"]
        if outcome == "empty":
            count = row["consecutive_empty"]
            lines.append("%s: 0 results for %s runs (was finding listings)" % (name, count))
        else:
            count = row["consecutive_failures"]
            detail = row["last_error"]
            label = {"http_error": "HTTP error", "network_error": "network error"}.get(outcome, "error")
            lines.append(
                "%s: %s for %s runs%s"
                % (name, label, count, (" — %s" % detail) if detail else "")
            )
    return "\n".join(lines)


def format_health_recovery(rows: list) -> str:
    lines = ["Crawler recovered — %s source(s) back online" % len(rows), ""]
    for row in rows:
        lines.append("%s: OK" % row["source_name"])
    return "\n".join(lines)


def format_alert(listing: Listing) -> str:
    price = "GBP %s pcm" % listing.price_pcm if listing.price_pcm is not None else "Price unknown"
    beds = "%s bed" % listing.bedrooms if listing.bedrooms is not None else "Beds unknown"
    area = listing.postcode_area or "Area unknown"
    heading = "New rental match"
    if listing.metadata.get("recent_reason"):
        heading = "%s: %s" % (heading, listing.metadata["recent_reason"])
    lines = [
        heading,
        "%s" % listing.title,
        "%s | %s | %s" % (price, beds, area),
        "Source: %s" % listing.source,
    ]
    if listing.metadata.get("search_page"):
        lines.append("Search page (open and find this listing): %s" % listing.url)
    else:
        lines.append(listing.url)
    return "\n".join(lines)
