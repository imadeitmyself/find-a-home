from __future__ import annotations

import argparse
from typing import Optional

from .agent_directory import load_agent_directory
from .config import load_config, load_env_file, load_source_file
from .notifiers import build_notifier_from_env, format_alert, get_telegram_bot_info, get_telegram_updates
from .runner import run_forever, run_once
from .store import ListingStore
from .models import Listing
from .supabase_export import write_agent_directory_seed, write_agent_seed


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor rental listings and alert on new matches.")
    parser.add_argument("--env", default=".env", help="Path to env file. Default: .env")

    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in [
        "dry-run",
        "seed-current",
        "run",
        "test-alert",
        "recent-alerts",
        "telegram-info",
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
