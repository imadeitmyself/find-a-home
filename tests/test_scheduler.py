import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from rental_alert_bot.health import HealthTracker
from rental_alert_bot.models import SourceConfig
from rental_alert_bot.runner import run_forever
from rental_alert_bot.scheduler import select_sources_for_schedule


NOW = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.health = HealthTracker(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_foxtons_and_savills_are_always_fast(self):
        sources = [
            SourceConfig(name="Foxtons (E9)", urls=["https://foxtons.example/e9"]),
            SourceConfig(name="Savills (E8)", urls=["https://savills.example/e8"]),
        ]

        selected = select_sources_for_schedule(sources, self.health, "fast", now=NOW)

        self.assertEqual([source.name for source in selected], ["Foxtons (E9)", "Savills (E8)"])

    def test_recently_productive_source_is_standard(self):
        source = SourceConfig(name="Dexters (E9)", urls=["https://dexters.example/e9"])
        self._record_history(source, first_checked="2026-06-01T12:00:00+00:00", last_ok="2026-06-08T12:00:00+00:00")

        selected = select_sources_for_schedule([source], self.health, "standard", now=NOW)

        self.assertEqual(selected, [source])

    def test_source_without_candidates_for_three_days_is_stale(self):
        source = SourceConfig(name="Home Made (E8)", urls=["https://homemade.example/e8"])
        self._record_history(source, first_checked="2026-05-20T12:00:00+00:00", last_ok="2026-06-05T12:00:00+00:00")

        selected = select_sources_for_schedule([source], self.health, "stale", now=NOW)

        self.assertEqual(selected, [source])

    def test_never_successful_source_becomes_stale_after_three_days(self):
        source = SourceConfig(name="Empty Agent (E2)", urls=["https://empty.example/e2"])
        self._record_history(source, first_checked="2026-06-01T12:00:00+00:00")

        selected = select_sources_for_schedule([source], self.health, "stale", now=NOW)

        self.assertEqual(selected, [source])

    def test_new_never_successful_source_stays_standard_during_warmup(self):
        source = SourceConfig(name="New Agent (N1)", urls=["https://new.example/n1"])
        self._record_history(source, first_checked="2026-06-08T12:00:00+00:00")

        selected = select_sources_for_schedule([source], self.health, "standard", now=NOW)

        self.assertEqual(selected, [source])

    @patch.dict("os.environ", {"CRON_SCHEDULER_ENABLED": "true"})
    def test_continuous_runner_stays_idle_when_cron_scheduler_is_enabled(self):
        with patch(
            "rental_alert_bot.runner.time.sleep",
            side_effect=RuntimeError("stop idle loop"),
        ) as sleep:
            with self.assertRaisesRegex(RuntimeError, "stop idle loop"):
                run_forever(config=None, store=None, notifier=None)

        sleep.assert_called_once_with(3600)

    def _record_history(self, source, first_checked, last_ok=None):
        url = source.urls[0]
        key = self.health.make_key(source.name, url)
        self.conn.execute(
            """
            INSERT INTO source_health (
                source_key, source_name, last_ok_at, last_checked_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (key, source.name, last_ok, first_checked),
        )
        self.conn.execute(
            """
            INSERT INTO source_outcome_log (
                recorded_at, source_name, url, outcome
            )
            VALUES (?, ?, ?, ?)
            """,
            (first_checked, source.name, url, "ok" if last_ok else "empty"),
        )
        self.conn.commit()


if __name__ == "__main__":
    unittest.main()
