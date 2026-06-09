# Find A Home

Fast rental listing monitor for estate-agent websites. The MVP is tuned for:

- Areas: E9, E8, N1, E2
- Beds: exactly 2
- Budget: GBP 2,750 to GBP 3,750 pcm
- Instant listing alerts: email (Mailgun first, SMTP fallback)
- Telegram: daily 07:00 high-level source-health digest

The app avoids scraping OnTheMarket directly by default. It polls estate-agent pages, extracts listing candidates from HTML/JSON-LD, filters them, deduplicates them in SQLite, emails new matches instantly, and sends Telegram a daily health digest.

## Quick Start

```bash
cd /Users/thibault/dev/find-a-home
cp .env.example .env
python3 -m rental_alert_bot seed-current --config config.json
python3 -m rental_alert_bot run --config config.json
```

Use `seed-current` once before the real monitor so existing listings are stored without alerting. After that, `run` only alerts on new matches.

Before running continuously, edit `config.json` and replace the `user_agent` email with your address.

## Email Setup

Instant property alerts are email-only. Configure Mailgun with `MAILGUN_API_KEY`,
`MAILGUN_DOMAIN`, and `MAILGUN_TO`, or configure SMTP with `EMAIL_SMTP_HOST`,
`EMAIL_FROM`, and `EMAIL_TO`.

You can test instant listing delivery with:

```bash
python3 -m rental_alert_bot test-alert --config config.json
```

## Telegram Setup

1. Message `@BotFather` in Telegram and create a bot.
2. Put the bot token in `.env` as `TELEGRAM_BOT_TOKEN`.
3. Send your bot any message.
4. Visit `https://api.telegram.org/bot<token>/getUpdates` in a browser and copy your chat id into `TELEGRAM_CHAT_ID`.

Telegram is only used for the daily source-health digest. You can verify the
configured bot/chat with:

```bash
python3 -m rental_alert_bot telegram-info --config config.json
```

## Commands

```bash
python3 -m rental_alert_bot dry-run --config config.json
python3 -m rental_alert_bot seed-current --config config.json
python3 -m rental_alert_bot run --config config.json --once
python3 -m rental_alert_bot run --config config.json --interval 60
python3 -m rental_alert_bot run --config config.json --once --schedule-group fast
python3 -m rental_alert_bot test-alert --config config.json
```

- `dry-run` fetches sources and prints accepted/rejected candidates without writing to SQLite or sending alerts.
- `seed-current` stores current accepted matches without sending alerts.
- `run` polls forever unless `--once` is supplied.
- `run --once --schedule-group fast|standard|stale` supports separate cron cadences.
  Foxtons and Savills are always `fast`; other sources become `stale` after
  three days without any extracted candidates.

## Notifications

- **Property matches** are emailed instantly through Mailgun, or SMTP fallback.
  Telegram never receives listing-level alerts.
- **Tracker health** (which sources are working vs. failing) is delivered to Telegram
  once a day via the `daily-report` command. Per-run polling still records every
  outcome, but Telegram only gets the high-level digest.

### Daily tracker-health report

```bash
python3 -m rental_alert_bot daily-report --config config.json            # send to Telegram
python3 -m rental_alert_bot daily-report --config config.json --dry-run  # print to stdout, send nothing
```

The report is Telegram-only and requires `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`.
Long reports are automatically split into multiple Telegram messages (4096-char limit).

To run it automatically at 07:00 on a systemd VPS, install the timer:

```bash
sudo cp deploy/find-a-home-daily-report.service.example /etc/systemd/system/find-a-home-daily-report.service
sudo cp deploy/find-a-home-daily-report.timer.example   /etc/systemd/system/find-a-home-daily-report.timer
sudo systemctl daemon-reload
sudo systemctl enable --now find-a-home-daily-report.timer
systemctl list-timers find-a-home-daily-report.timer   # confirm next run
```

The timer fires at 07:00 in the server's local timezone — set it with
`sudo timedatectl set-timezone Europe/London` if needed.

## Docker VPS Deployment

```bash
cd /Users/thibault/dev/find-a-home
cp .env.example .env
docker compose up -d --build
```

On a small VPS, keep `data/` mounted so the seen-listing database survives restarts.

There is also a `deploy/find-a-home.service.example` file if you prefer `systemd` over Docker.
For source-specific scheduling, `deploy/find-a-home.crontab.example` runs Foxtons
and Savills every 5 minutes, productive sources every 15 minutes, and stale
sources once an hour. Set `CRON_SCHEDULER_ENABLED=true` in `.env` when installing
these jobs so a legacy continuous systemd service stays idle.

## Tuning Sources

Edit `data/agent_rental_listing_urls_e8_e9_n1_e2.csv` to add or disable estate-agent URLs. The monitor reloads this file on every polling cycle, so changes take effect while the app is running.

Your root file `onthemarket_recent_agents_e8_e9_e2_n1.csv` is useful as the agent directory. It has homepages, not crawlable rental search URLs, so the monitor does not poll it directly. Convert each promising homepage into a specific rental listings URL, then add that URL to `data/agent_rental_listing_urls_e8_e9_n1_e2.csv`.

CSV columns:

- `enabled`: `true` or `false`
- `name`: estate-agent name
- `postcode_area`: one of `E9`, `E8`, `N1`, `E2`, or blank
- `listing_url`: the agent's rental search/listings page
- `excluded_keywords`: optional `;`-separated source-specific exclusions
- `notes`: ignored by the app

Each row needs a rental listings/search URL, not just the agent homepage. The extractor is intentionally generic: it looks for listing-like blocks, JSON-LD, prices, bedrooms, postcode areas, and rental URLs.

Keep `respect_robots_txt` enabled unless you have a clear reason and permission to do otherwise. Set a useful `user_agent` with your contact email before running this continuously.

Supabase is optional for this MVP. Use the local CSV first; move the agent list into Supabase later if you want multi-device editing, history, or a small admin UI.

## Optional Supabase Sync

The repo includes a minimal Supabase migration for `public.agent_sources` and a seed exporter from the active source CSV.

```bash
python3 -m rental_alert_bot export-supabase-seed \
  --agents data/agent_rental_listing_urls_e8_e9_n1_e2.csv \
  --output supabase/seed.sql
python3 -m rental_alert_bot export-supabase-directory-seed \
  --directory onthemarket_recent_agents_e8_e9_e2_n1.csv \
  --output supabase/agent_directory_seed.sql
supabase login
supabase link
supabase db push --include-seed
```

Per Supabase’s CLI docs, `supabase db push` applies local migrations to a linked remote project, and `--include-seed` includes `supabase/seed.sql`. See:

To load the separate directory seed after pushing migrations, run it with a Postgres client against your Supabase database URL:

```bash
psql "$SUPABASE_DB_URL" -f supabase/agent_directory_seed.sql
```

- https://supabase.com/docs/reference/cli/introduction
- https://supabase.com/docs/guides/deployment/database-migrations
- https://supabase.com/docs/guides/database/import-data

## Tests

```bash
python3 -m unittest discover -s tests
```
