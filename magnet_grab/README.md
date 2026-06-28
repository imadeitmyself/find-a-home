# magnet-grab

Drop a magnet link from your phone → the VPS downloads the torrent → you get a
Telegram message with one download link per file, so you can pull the files
straight onto your phone afterwards.

It lives in the same repo as find-a-home and **reuses the same Telegram bot**
(`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`) — no second bot needed. Pure Python
standard library; the only external dependency is the `aria2c` binary, which
does the actual BitTorrent work.

## Two ways to trigger a download from your phone

1. **Text the bot a magnet link** (easiest on the move). With `serve` running,
   just send the magnet as a Telegram message to the same bot find-a-home uses.
   It replies `🧲 Queued: …`, downloads it, then sends the per-file links. Only
   messages from your `TELEGRAM_CHAT_ID` are accepted. Disable with
   `MAGNET_GRAB_TELEGRAM_POLL=false`.
2. **The web form** — open the bookmarked page and paste a magnet. Handy when
   you want to see recent downloads and browse files.

## How it works

1. You send the bot a magnet (or paste it into the web page on the VPS), e.g.
   `magnet:?xt=urn:btih:297C945AB4913AE6D215AA1FD61739A8B9A12534`.
2. The server hands the magnet to `aria2c`, which downloads it into a folder
   named after the torrent's infohash.
3. When it finishes, you get a Telegram message:

   ```
   ✅ Torrent ready: Cool Release
   2 files · 1.4 GB

   movie.mkv: http://YOUR_VPS_IP:8800/files/297c.../movie.mkv?token=...
   subtitles.srt: http://YOUR_VPS_IP:8800/files/297c.../subtitles.srt?token=...
   ```

4. Tap any link to download that file directly onto your phone. The file server
   supports HTTP range requests, so downloads resume and stream cleanly.

## Setup on the VPS

`magnet-grab` shares find-a-home's checkout, virtualenv, and `.env`.

```bash
# 1. Install the torrent engine
sudo apt update && sudo apt install -y aria2

# 2. Add magnet-grab settings to the existing find-a-home .env
#    (see magnet_grab/.env.example for the full list)
cat magnet_grab/.env.example   # copy the new keys into your .env, then edit

# 3. Confirm the Telegram bot works (reuses find-a-home's token)
python -m magnet_grab telegram-test

# 4. Run the server
python -m magnet_grab serve
```

Then open `http://YOUR_VPS_IP:8800/?token=YOUR_TOKEN` on your phone and bookmark
it. Make sure the port is reachable (open the firewall / security group for the
chosen `MAGNET_GRAB_PORT`).

### Required settings

| Variable | What it is |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Reused from find-a-home. |
| `MAGNET_GRAB_TOKEN` | Shared secret required on every request and baked into the download links. **Set this** — otherwise anyone who finds the port can add torrents and browse your files. |
| `MAGNET_GRAB_PUBLIC_URL` | The base URL your phone uses to reach the VPS (public IP or domain, no trailing slash). The Telegram download links are built from it. |

See [`magnet_grab/.env.example`](.env.example) for the rest (download dir, host,
port, aria2c path).

## Run as a service (systemd)

```bash
sudo cp deploy/magnet-grab.service.example /etc/systemd/system/magnet-grab.service
# edit WorkingDirectory / EnvironmentFile / ExecStart paths if your checkout differs
sudo systemctl daemon-reload
sudo systemctl enable --now magnet-grab
journalctl -u magnet-grab -f      # watch the logs
```

## Commands

```bash
python -m magnet_grab serve            # HTTP server + Telegram magnet-trigger (normal mode)
python -m magnet_grab poll             # only the Telegram magnet-trigger (no HTTP server)
python -m magnet_grab add "magnet:?xt=urn:btih:..."   # download one magnet now, then exit
python -m magnet_grab telegram-test    # verify the bot token + send a test message
```

`add` is handy for testing from an SSH session: it downloads synchronously,
sends the same Telegram notification, and prints the per-file links to stdout.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/?token=...` | The phone-friendly form. |
| `POST`/`GET` | `/add?token=...` | Add a magnet (`magnet=` field/param). GET lets you build a one-tap shortcut. |
| `GET` | `/files/<infohash>/...?token=...` | Browse and download finished files. |

## Security notes

- Always set `MAGNET_GRAB_TOKEN`. It gates the web UI **and** the file
  downloads, and is embedded in the links sent to Telegram.
- For real privacy put it behind HTTPS (e.g. a reverse proxy / Caddy) so the
  token and files aren't sent in clear text. The token is passed as a query
  parameter, which is fine over TLS.
- Only download content you are legally allowed to.

## Tests

```bash
python3 -m unittest tests.test_magnet_grab
```
