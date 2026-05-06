import tempfile
import unittest

from rental_alert_bot.models import SourceConfig
from rental_alert_bot.agent_directory import AgentDirectoryEntry
from rental_alert_bot.supabase_export import write_agent_directory_seed, write_agent_seed


class SupabaseExportTests(unittest.TestCase):
    def test_writes_seed_sql(self):
        with tempfile.NamedTemporaryFile("r", suffix=".sql") as handle:
            count = write_agent_seed(
                [
                    SourceConfig(
                        name="Agent's E9",
                        urls=["https://example.com/e9"],
                        enabled=True,
                        excluded_keywords=["short let"],
                    )
                ],
                handle.name,
            )
            sql = handle.read()

        self.assertEqual(count, 1)
        self.assertIn("insert into public.agent_sources", sql)
        self.assertIn("'Agent''s E9'", sql)
        self.assertIn("array['short let']::text[]", sql)
        self.assertIn("on conflict (listing_url)", sql)

    def test_writes_directory_seed_sql(self):
        with tempfile.NamedTemporaryFile("r", suffix=".sql") as handle:
            count = write_agent_directory_seed(
                [
                    AgentDirectoryEntry(
                        agent_name="Dexters",
                        owned_website_url="https://www.dexters.co.uk",
                        status="confirmed_recent",
                        evidence_or_note="from OTM",
                    )
                ],
                handle.name,
            )
            sql = handle.read()

        self.assertEqual(count, 1)
        self.assertIn("insert into public.agent_directory", sql)
        self.assertIn("'https://www.dexters.co.uk'", sql)
        self.assertIn("on conflict (owned_website_url)", sql)


if __name__ == "__main__":
    unittest.main()
