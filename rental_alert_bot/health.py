from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Tuple

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
    FAILING_OUTCOMES = ("http_error", "network_error", "error")

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

            CREATE TABLE IF NOT EXISTS source_outcome_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                source_name TEXT NOT NULL,
                url TEXT NOT NULL,
                outcome TEXT NOT NULL,
                error_detail TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_source_outcome_log_recorded_at
                ON source_outcome_log(recorded_at);
        """)
        self.conn.commit()

    def _key(self, name: str, url: str) -> str:
        return "%s|%s" % (name, url)

    def make_key(self, name: str, url: str) -> str:
        return self._key(name, url)

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

        self.conn.execute(
            """
            INSERT INTO source_outcome_log (recorded_at, source_name, url, outcome, error_detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (now, outcome.source_name, outcome.url, outcome.outcome, outcome.error_detail[:200]),
        )
        self.conn.commit()

    def get_window_summary(
        self, start_iso: str, end_iso: str
    ) -> Tuple[List[sqlite3.Row], Dict[Tuple[str, str], str]]:
        """Counts grouped by (source_name, url, outcome) over [start, end), plus the most
        recent error detail per (source_name, url) for failing outcomes."""
        counts = list(
            self.conn.execute(
                """
                SELECT source_name, url, outcome, COUNT(*) AS count
                FROM source_outcome_log
                WHERE recorded_at >= ? AND recorded_at < ?
                GROUP BY source_name, url, outcome
                """,
                (start_iso, end_iso),
            ).fetchall()
        )

        error_rows = self.conn.execute(
            """
            SELECT source_name, url, error_detail
            FROM source_outcome_log
            WHERE recorded_at >= ? AND recorded_at < ?
              AND outcome NOT IN ('ok', 'empty', 'skip')
              AND error_detail != ''
            ORDER BY recorded_at DESC
            """,
            (start_iso, end_iso),
        ).fetchall()
        last_error: Dict[Tuple[str, str], str] = {}
        for row in error_rows:
            key = (row["source_name"], row["url"])
            if key not in last_error:
                last_error[key] = row["error_detail"]
        return counts, last_error

    def last_ok_index(self) -> Dict[str, str]:
        """source_key → last_ok_at ISO for sources that have ever fetched OK."""
        return {
            row["source_key"]: row["last_ok_at"]
            for row in self.conn.execute(
                "SELECT source_key, last_ok_at FROM source_health WHERE last_ok_at IS NOT NULL"
            ).fetchall()
        }

    def historic_max_index(self) -> Dict[str, int]:
        """source_key → historic_max_candidates."""
        return {
            row["source_key"]: int(row["historic_max_candidates"])
            for row in self.conn.execute(
                "SELECT source_key, historic_max_candidates FROM source_health"
            ).fetchall()
        }

    def purge_log_older_than(self, days: int) -> int:
        cur = self.conn.execute(
            "DELETE FROM source_outcome_log WHERE recorded_at < datetime('now', ?)",
            ("-%d days" % int(days),),
        )
        self.conn.commit()
        return cur.rowcount or 0

    def log_earliest(self) -> str:
        row = self.conn.execute(
            "SELECT MIN(recorded_at) AS first FROM source_outcome_log"
        ).fetchone()
        return row["first"] if row and row["first"] else ""

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
