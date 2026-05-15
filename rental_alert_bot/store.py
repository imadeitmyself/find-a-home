from __future__ import annotations

import json
import os
import sqlite3
from typing import Iterable, Optional

from .health import HealthTracker
from .models import Listing, utcnow_iso


class ListingStore:
    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self.health = HealthTracker(self.conn)

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listings (
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                price_pcm INTEGER,
                bedrooms INTEGER,
                postcode_area TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_alerted_at TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (external_id)
            );

            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                channel TEXT NOT NULL,
                message TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def has_seen(self, listing: Listing) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM listings WHERE external_id = ?",
            (listing.external_id,),
        ).fetchone()
        return row is not None

    def upsert_seen(self, listing: Listing) -> bool:
        now = utcnow_iso()
        payload = json.dumps(_listing_payload(listing), sort_keys=True)
        existed = self.has_seen(listing)
        if existed:
            self.conn.execute(
                """
                UPDATE listings
                   SET source = ?, url = ?, title = ?, price_pcm = ?, bedrooms = ?, postcode_area = ?,
                       last_seen_at = ?, payload_json = ?
                 WHERE external_id = ?
                """,
                (
                    listing.source,
                    listing.url,
                    listing.title,
                    listing.price_pcm,
                    listing.bedrooms,
                    listing.postcode_area,
                    now,
                    payload,
                    listing.external_id,
                ),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO listings (
                    source, external_id, url, title, price_pcm, bedrooms, postcode_area,
                    first_seen_at, last_seen_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    listing.source,
                    listing.external_id,
                    listing.url,
                    listing.title,
                    listing.price_pcm,
                    listing.bedrooms,
                    listing.postcode_area,
                    now,
                    now,
                    payload,
                ),
            )
        self.conn.commit()
        return not existed

    def mark_alerted(self, listing: Listing, channel: str, message: str) -> None:
        now = utcnow_iso()
        self.conn.execute(
            """
            UPDATE listings
               SET last_alerted_at = ?
             WHERE external_id = ?
            """,
            (now, listing.external_id),
        )
        self.conn.execute(
            """
            INSERT INTO alert_history (source, external_id, sent_at, channel, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (listing.source, listing.external_id, now, channel, message),
        )
        self.conn.commit()

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS count FROM listings").fetchone()
        return int(row["count"])

    def list_recent(self, limit: int = 20) -> Iterable[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM listings ORDER BY first_seen_at DESC LIMIT ?",
            (limit,),
        )


def _listing_payload(listing: Listing) -> dict:
    return {
        "source": listing.source,
        "external_id": listing.external_id,
        "url": listing.url,
        "title": listing.title,
        "raw_text": listing.raw_text,
        "price_pcm": listing.price_pcm,
        "bedrooms": listing.bedrooms,
        "postcode_area": listing.postcode_area,
        "address": listing.address,
        "available_date": listing.available_date,
        "metadata": listing.metadata,
    }
