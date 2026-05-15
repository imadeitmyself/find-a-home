from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List

from .models import utcnow_iso


@dataclass
class SourceOutcome:
    source_name: str
    url: str
    outcome: str  # "ok" | "empty" | "http_error" | "network_error" | "error"
    candidate_count: int = 0
    error_detail: str = ""


class HealthTracker:
    FAILURE_ALERT_THRESHOLD = 3
    EMPTY_ALERT_THRESHOLD = 5

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_health (
                source_key TEXT PRIMARY KEY,
                source_name TEXT NOT NULL,
                last_ok_at TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                consecutive_empty INTEGER NOT NULL DEFAULT 0,
                last_outcome TEXT NOT NULL DEFAULT 'unknown',
                last_error TEXT NOT NULL DEFAULT '',
                last_checked_at TEXT NOT NULL,
                alerted_at TEXT,
                alert_cleared_at TEXT,
                historic_max_candidates INTEGER NOT NULL DEFAULT 0
            );
        """)
        self.conn.commit()

    def _key(self, name: str, url: str) -> str:
        return "%s|%s" % (name, url)

    def record(self, outcome: SourceOutcome) -> None:
        now = utcnow_iso()
        key = self._key(outcome.source_name, outcome.url)
        row = self.conn.execute(
            "SELECT * FROM source_health WHERE source_key = ?", (key,)
        ).fetchone()

        if outcome.outcome == "ok":
            historic_max = max(outcome.candidate_count, row["historic_max_candidates"] if row else 0)
            # Do NOT clear alerted_at here — get_recovered() needs it to detect recovery.
            # mark_recovered() clears it after the recovery notification is sent.
            self.conn.execute(
                """
                INSERT INTO source_health (source_key, source_name, last_ok_at, consecutive_failures,
                    consecutive_empty, last_outcome, last_error, last_checked_at, historic_max_candidates)
                VALUES (?, ?, ?, 0, 0, 'ok', '', ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    last_ok_at = excluded.last_ok_at,
                    consecutive_failures = 0,
                    consecutive_empty = 0,
                    last_outcome = 'ok',
                    last_error = '',
                    last_checked_at = excluded.last_checked_at,
                    historic_max_candidates = excluded.historic_max_candidates
                """,
                (key, outcome.source_name, now, now, historic_max),
            )
        elif outcome.outcome == "empty":
            consecutive_empty = (row["consecutive_empty"] + 1) if row else 1
            historic_max = row["historic_max_candidates"] if row else 0
            self.conn.execute(
                """
                INSERT INTO source_health (source_key, source_name, consecutive_failures,
                    consecutive_empty, last_outcome, last_error, last_checked_at, historic_max_candidates)
                VALUES (?, ?, 0, ?, 'empty', '', ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    consecutive_empty = excluded.consecutive_empty,
                    last_outcome = 'empty',
                    last_error = '',
                    last_checked_at = excluded.last_checked_at
                """,
                (key, outcome.source_name, consecutive_empty, now, historic_max),
            )
        else:
            consecutive_failures = (row["consecutive_failures"] + 1) if row else 1
            self.conn.execute(
                """
                INSERT INTO source_health (source_key, source_name, consecutive_failures,
                    consecutive_empty, last_outcome, last_error, last_checked_at, historic_max_candidates)
                VALUES (?, ?, ?, 0, ?, ?, ?, 0)
                ON CONFLICT(source_key) DO UPDATE SET
                    consecutive_failures = excluded.consecutive_failures,
                    last_outcome = excluded.last_outcome,
                    last_error = excluded.last_error,
                    last_checked_at = excluded.last_checked_at
                """,
                (
                    key,
                    outcome.source_name,
                    consecutive_failures,
                    outcome.outcome,
                    outcome.error_detail[:200],
                    now,
                ),
            )
        self.conn.commit()

    def get_newly_alertable(self) -> List[sqlite3.Row]:
        """Sources that just crossed the alert threshold and haven't been alerted yet."""
        return list(
            self.conn.execute(
                """
                SELECT * FROM source_health
                WHERE (
                    (consecutive_failures >= ? AND last_outcome NOT IN ('ok', 'empty'))
                    OR (consecutive_empty >= ? AND historic_max_candidates > 0)
                )
                AND alerted_at IS NULL
                """,
                (self.FAILURE_ALERT_THRESHOLD, self.EMPTY_ALERT_THRESHOLD),
            ).fetchall()
        )

    def get_recovered(self) -> List[sqlite3.Row]:
        """Sources that just recovered after having been alerted."""
        return list(
            self.conn.execute(
                """
                SELECT * FROM source_health
                WHERE last_outcome = 'ok'
                AND alerted_at IS NOT NULL
                AND (alert_cleared_at IS NULL OR alert_cleared_at < last_ok_at)
                """
            ).fetchall()
        )

    def mark_alerted(self, source_keys: List[str]) -> None:
        now = utcnow_iso()
        for key in source_keys:
            self.conn.execute(
                "UPDATE source_health SET alerted_at = ? WHERE source_key = ?", (now, key)
            )
        self.conn.commit()

    def mark_recovered(self, source_keys: List[str]) -> None:
        now = utcnow_iso()
        for key in source_keys:
            self.conn.execute(
                "UPDATE source_health SET alert_cleared_at = ?, alerted_at = NULL WHERE source_key = ?",
                (now, key),
            )
        self.conn.commit()

    def list_all(self) -> List[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM source_health ORDER BY source_name").fetchall())
