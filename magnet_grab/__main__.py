"""Command-line entrypoint: ``python -m magnet_grab <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from .config import Config, load_config, load_env_file
from .downloader import Downloader, format_completion_message
from .server import serve
from .telegram import TelegramClient, get_bot_info


def _build_telegram(config: Config) -> Optional[TelegramClient]:
    if not config.telegram_enabled:
        return None
    return TelegramClient(config.telegram_token, config.telegram_chat_id, config.request_timeout)


def _build_downloader(config: Config) -> Downloader:
    return Downloader(config, telegram=_build_telegram(config))


def cmd_serve(config: Config, args) -> int:
    serve(config, _build_downloader(config))
    return 0


def cmd_add(config: Config, args) -> int:
    downloader = _build_downloader(config)
    job = downloader.run_sync(args.magnet)
    if job.status == "done":
        print(format_completion_message(config, job))
        return 0
    print("Download failed: %s" % job.error, file=sys.stderr)
    return 1


def cmd_telegram_test(config: Config, args) -> int:
    if not config.telegram_enabled:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set.", file=sys.stderr)
        return 1
    print(get_bot_info(config.telegram_token, config.request_timeout))
    TelegramClient(config.telegram_token, config.telegram_chat_id).send_message(
        "🧲 magnet-grab is connected and ready."
    )
    print("Sent a test message to chat %s." % config.telegram_chat_id)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="magnet_grab", description=__doc__)
    parser.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="Run the HTTP server (the main mode)")

    add_parser = sub.add_parser("add", help="Download one magnet synchronously and exit")
    add_parser.add_argument("magnet", help="magnet:?xt=urn:btih:... link")

    sub.add_parser("telegram-test", help="Verify the Telegram bot token and send a test message")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_env_file(args.env_file)
    config = load_config()

    handlers = {"serve": cmd_serve, "add": cmd_add, "telegram-test": cmd_telegram_test}
    return handlers[args.command](config, args)


if __name__ == "__main__":
    raise SystemExit(main())
