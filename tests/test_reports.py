import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from rental_alert_bot.health import HealthTracker, SourceOutcome
from rental_alert_bot.models import SourceConfig
from rental_alert_bot.reports import build_daily_report


def _mk_health() -> HealthTracker:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return HealthTracker(conn)


def _record(health: HealthTracker, source: str, url: str, outcome: str, err: str = "") -> None:
    health.record(
        SourceOutcome(source_name=source, url=url, outcome=outcome, candidate_count=1, error_detail=err)
    )


class DailyReportTests(unittest.TestCase):
    def test_classifies_working_failing_and_changes(self) -> None:
        health = _mk_health()
        # Seed history (>24h ago: prior window)
        with health.conn:
            # Backdate: directly insert old log rows
            old_ts = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
            health.conn.execute(
                "INSERT INTO source_outcome_log (recorded_at, source_name, url, outcome, error_detail) VALUES (?, ?, ?, ?, '')",
                (old_ts, "Foxtons (E9)", "https://foxtons.example/e9", "ok"),
            )
            health.conn.execute(
                "INSERT INTO source_outcome_log (recorded_at, source_name, url, outcome, error_detail) VALUES (?, ?, ?, ?, ?)",
                (old_ts, "FJL (E2)", "https://fjl.example/e2", "http_error", "HTTP 500"),
            )
        # Mark Foxtons as having had history (so historic_max > 0)
        _record(health, "Foxtons (E9)", "https://foxtons.example/e9", "ok")
        # Now: Foxtons regresses (last 24h: failing)
        for _ in range(5):
            _record(health, "Foxtons (E9)", "https://foxtons.example/e9", "http_error", "Timeout")
        # FJL recovers
        _record(health, "FJL (E2)", "https://fjl.example/e2", "ok")
        # A persistently working agency
        for _ in range(3):
            _record(health, "Bigmove (N1)", "https://bigmove.example/n1", "ok")

        sources = [
            SourceConfig(name="Foxtons (E9)", urls=["https://foxtons.example/e9"], tier=1),
            SourceConfig(name="FJL (E2)", urls=["https://fjl.example/e2"], tier=2),
            SourceConfig(name="Bigmove (N1)", urls=["https://bigmove.example/n1"], tier=3),
        ]
        subject, body = build_daily_report(health, sources)

        self.assertIn("find-a-home health:", subject)
        self.assertIn("failing", subject)
        self.assertIn("CHANGED SINCE PRIOR 24H", body)
        self.assertIn("Foxtons (E9)", body)  # Should appear in failing/changed
        self.assertIn("FAILING", body)
        self.assertIn("SUMMARY", body)
        self.assertIn("Working: 2 urls", body)

    def test_flags_enabled_sources_missing_from_window(self) -> None:
        health = _mk_health()
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        with health.conn:
            health.conn.execute(
                "INSERT INTO source_outcome_log (recorded_at, source_name, url, outcome, error_detail) VALUES (?, ?, ?, ?, '')",
                (old_ts, "Stale Agent (E9)", "https://stale.example/e9", "ok"),
            )

        sources = [
            SourceConfig(name="Fresh Agent (E9)", urls=["https://fresh.example/e9"], tier=1),
            SourceConfig(name="Stale Agent (E9)", urls=["https://stale.example/e9"], tier=2),
            SourceConfig(name="Never Agent (E9)", urls=["https://never.example/e9"], tier=3),
        ]
        _record(health, "Fresh Agent (E9)", "https://fresh.example/e9", "ok")

        subject, body = build_daily_report(health, sources)

        self.assertIn("1/3 checked", subject)
        self.assertIn("2 stale", subject)
        self.assertIn("STALE OR NEVER CHECKED", body)
        self.assertIn("Stale Agent (E9)", body)
        self.assertIn("last checked", body)
        self.assertIn("Never Agent (E9)", body)
        self.assertIn("never checked", body)

    def test_empty_log_still_renders(self) -> None:
        health = _mk_health()
        subject, body = build_daily_report(health, sources=[])
        self.assertIn("find-a-home health:", subject)
        self.assertIn("COVERAGE: no enabled URLs configured", body)
        self.assertNotIn("New rental", body)


if __name__ == "__main__":
    unittest.main()
