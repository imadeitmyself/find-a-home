"""magnet_grab — drop a magnet link, download the torrent on the VPS, get a Telegram ping with per-file download links.

Pure standard library. Shells out to ``aria2c`` for the actual BitTorrent work.
Reuses the Telegram bot token configured for find-a-home.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
