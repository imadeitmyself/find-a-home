"""Configuration for magnet_grab, loaded from environment variables.

Reuses the same Telegram credentials as find-a-home (``TELEGRAM_BOT_TOKEN`` /
``TELEGRAM_CHAT_ID``) so a single bot covers both projects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: str = ".env") -> None:
    """Populate os.environ from a simple KEY=VALUE .env file (no override)."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    host: str
    port: int
    download_dir: Path
    public_url: str
    access_token: str
    telegram_token: str
    telegram_chat_id: str
    aria2c_path: str
    request_timeout: int
    telegram_poll: bool = True

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    def file_base_url(self) -> str:
        return "%s/files" % self.public_url.rstrip("/")


def load_config(env=None) -> Config:
    env = os.environ if env is None else env

    host = env.get("MAGNET_GRAB_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(env.get("MAGNET_GRAB_PORT", "8800") or "8800")
    download_dir = Path(env.get("MAGNET_GRAB_DOWNLOAD_DIR", "downloads").strip() or "downloads")

    public_url = env.get("MAGNET_GRAB_PUBLIC_URL", "").strip()
    if not public_url:
        # Best-effort default; set MAGNET_GRAB_PUBLIC_URL to your VPS host for
        # links that actually work from your phone.
        public_url = "http://localhost:%d" % port

    return Config(
        host=host,
        port=port,
        download_dir=download_dir.expanduser(),
        public_url=public_url.rstrip("/"),
        access_token=env.get("MAGNET_GRAB_TOKEN", "").strip(),
        telegram_token=env.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=env.get("TELEGRAM_CHAT_ID", "").strip(),
        aria2c_path=env.get("MAGNET_GRAB_ARIA2C", "aria2c").strip() or "aria2c",
        request_timeout=int(env.get("MAGNET_GRAB_TIMEOUT", "20") or "20"),
        telegram_poll=_env_bool(env.get("MAGNET_GRAB_TELEGRAM_POLL"), default=True),
    )


def _env_bool(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
