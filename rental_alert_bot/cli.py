from __future__ import annotations

import argparse
import logging
import logging.handlers
import pathlib
from typing import Optional

from .agent_directory import load_agent_directory
from .config import load_config, load_env_file, load_live_sources, load_source_file
from .notifiers import (
    build_email_notifier_from_env,
    build_notifier_from_env,
    format_alert,
    get_telegram_bot_info,
    get_telegram_updates,
    _mailgun_notifier_from_env,
)
from .reports import build_daily_report
from .runner import run_forever, run_once
from .store import ListingStore
from .models import Listing
from .supabase_export import write_agent_directory_seed, write_agent_seed

LOG_RETENTION_DAYS = 30


def _setup_logging(log_dir: str = "logs") -> None:
    pathlib.Path(log_dir).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger("rental_alert_bot")
    root.setLevel(logging.DEBUG)
    fh = logging.handlers.RotatingFileHandler(
        pathlib.Path(log_dir) / "crawl.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor rental listings and alert on new matches.")
    parser.add_argument("--env", default=".env", help="Path to env file. Default: .env")

    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in [
        "dry-run",
        "seed-current",
        "run",
        "test-alert",
        "test-mailgun",
        "recent-alerts",
        "telegram-info",
        "health-status",
        "daily-report",
        "export-supabase-seed",
        "export-supabase-directory-seed",
    ]:
        command = subparsers.add_parser(name)
        command.add_argument("--config", default="config.json", help="Path to config JSON. Default: config.json")
        if name == "run":
            command.add_argument("--once", action="store_true", help="Run one poll cycle and exit.")
            command.add_argument("--interval", type=int, default=None, help="Override polling interval in seconds.")
        if name == "recent-alerts":
            command.add_argument("--minutes", type=int, default=60, help="Only alert explicit recent markers within this many minutes.")
            command.add_argument("--dry-run", action="store_true", help="Print qualifying recent matches without sending alerts.")
        if name == "daily-report":
            command.add_argument("--dry-run", action="store_true", help="Print the report to stdout instead of emailing it.")
            command.add_argument("--no-purge", action="store_true", help="Skip the 30-day log purge.")
        if name == "export-supabase-seed":
            command.add_argument(
                "--agents",
                default="data/agent_rental_listing_urls_e8_e9_n1_e2.csv",
                help="Path to agents CSV. Default: data/agent_rental_listing_urls_e8_e9_n1_e2.csv",
            )
            command.add_argument("--output", default="supabase/seed.sql", help="Output SQL file. Default: supabase/seed.sql")
        if name == "export-supabase-directory-seed":
            command.add_argument(
                "--directory",
                default="onthemarket_recent_agents_e8_e9_e2_n1.csv",
                help="Path to root agent directory CSV.",
            )
            command.add_argument(
                "--output",
                default="supabase/agent_directory_seed.sql",
                help="Output SQL file. Default: supabase/agent_directory_seed.sql",
            )

    args = parser.parse_args(argv)
    _setup_logging()
    load_env_file(args.env)
    config = load_config(args.config)

    if args.command == "dry-run":
        run_once(config=config, store=None, notifier=None, dry_run=True, seed=False)
        return 0

    if args.command == "seed-current":
        store = ListingStore(config.database_path)
        try:
            run_once(config=config, store=store, notifier=None, dry_run=False, seed=True)
            return 0
        finally:
            store.close()

    if args.command == "test-mailgun":
        import os
        mailgun = _mailgun_notifier_from_env(config.request_timeout_seconds)
        if not mailgun:
            print("Mailgun not configured. Add MAILGUN_API_KEY, MAILGUN_DOMAIN, MAILGUN_TO to .env")
            return 1
        try:
            mailgun.send_text(
                "find-a-home test email\n\n"
                "If you received this, Mailgun is configured correctly.\n"
                "Domain: %s\nTo: %s" % (mailgun.domain, mailgun.recipient)
            )
            print("Test email sent via Mailgun to %s" % mailgun.recipient)
            return 0
        except Exception as exc:
            print("Mailgun test failed: %s" % exc)
            return 1

    if args.command == "test-alert":
        notifier = build_notifier_from_env(config.request_timeout_seconds, allow_print=False)
        listing = Listing(
            source="test",
            external_id="test",
            url="https://example.com/test-listing",
            title="Test 2 bed rental alert",
            raw_text="Test 2 bedroom E9 GBP 3000 pcm",
            price_pcm=3000,
            bedrooms=2,
            postcode_area="E9",
        )
        try:
            notifier.send(listing, format_alert(listing))
        except Exception as exc:
            print("Test alert failed: %s" % exc)
            return 1
        print("Sent test alert via %s" % getattr(notifier, "channel", "unknown"))
        return 0

    if args.command == "recent-alerts":
        store = ListingStore(config.database_path)
        notifier = None if args.dry_run else build_notifier_from_env(config.request_timeout_seconds, allow_print=False)
        try:
            run_once(
                config=config,
                store=store if not args.dry_run else None,
                notifier=notifier,
                dry_run=args.dry_run,
                seed=False,
                recent_only_minutes=args.minutes,
            )
            return 0
        finally:
            store.close()

    if args.command == "health-status":
        store = ListingStore(config.database_path)
        try:
            rows = store.health.list_all()
            if not rows:
                print("No health data yet. Run the scraper first.")
                return 0
            print("%-40s %-10s %5s %5s %s" % ("Source", "Outcome", "Fails", "Empty", "Last checked"))
            for row in rows:
                print("%-40s %-10s %5s %5s %s" % (
                    row["source_name"][:40],
                    row["last_outcome"],
                    row["consecutive_failures"],
                    row["consecutive_empty"],
                    (row["last_checked_at"] or "")[:19],
                ))
            return 0
        finally:
            store.close()

    if args.command == "daily-report":
        store = ListingStore(config.database_path)
        try:
            sources = load_live_sources(config)
            subject, body = build_daily_report(store.health, sources)
            if args.dry_run:
                print("Subject: %s" % subject)
                print()
                print(body)
                return 0
            notifier = build_email_notifier_from_env(config.request_timeout_seconds)
            if notifier is None:
                print("No email notifier configured. Set MAILGUN_* or EMAIL_SMTP_* in .env.")
                return 1
            try:
                notifier.send_report(subject, body)
            except Exception as exc:
                print("Daily report send failed: %s" % exc)
                return 1
            print("Sent daily report via %s" % getattr(notifier, "channel", "unknown"))
            if not args.no_purge:
                deleted = store.health.purge_log_older_than(LOG_RETENTION_DAYS)
                print("Purged %d log rows older than %d days" % (deleted, LOG_RETENTION_DAYS))
            return 0
        finally:
            store.close()

    if args.command == "telegram-info":
        import os

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            print("TELEGRAM_BOT_TOKEN is not set.")
            return 1
        info = get_telegram_bot_info(token, config.request_timeout_seconds)
        bot = info.get("result", {})
        username = bot.get("username")
        if username:
            print("Bot username: @%s" % username)
        else:
            print("Bot info: %s" % bot)

        updates = get_telegram_updates(token, config.request_timeout_seconds)
        chats = []
        for update in updates.get("result", []):
            message = update.get("message") or update.get("channel_post") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id and chat_id not in [item[0] for item in chats]:
                chats.append((chat_id, chat.get("type"), chat.get("username") or chat.get("first_name") or chat.get("title")))
        if not chats:
            print("No recent chats found. Open Telegram, message the bot, then rerun this command.")
        else:
            print("Recent chat ids:")
            for chat_id, chat_type, name in chats:
                print("- %s (%s%s)" % (chat_id, chat_type or "unknown", ", %s" % name if name else ""))
        return 0

    if args.command == "export-supabase-seed":
        sources = load_source_file(args.agents)
        count = write_agent_seed(sources, args.output)
        print("Wrote %s agent source rows to %s" % (count, args.output))
        return 0

    if args.command == "export-supabase-directory-seed":
        entries = load_agent_directory(args.directory)
        count = write_agent_directory_seed(entries, args.output)
        print("Wrote %s agent directory rows to %s" % (count, args.output))
        return 0

    if args.command == "run":
        store = ListingStore(config.database_path)
        notifier = build_notifier_from_env(config.request_timeout_seconds, allow_print=False)
        try:
            if args.once:
                run_once(config=config, store=store, notifier=notifier, dry_run=False, seed=False)
                return 0
            run_forever(config=config, store=store, notifier=notifier, interval_seconds=args.interval)
            return 0
        finally:
            store.close()

    parser.error("Unknown command")
    return 2
