import tempfile
import unittest

from rental_alert_bot.agent_directory import load_agent_directory


class AgentDirectoryTests(unittest.TestCase):
    def test_loads_root_directory_shape(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv") as handle:
            handle.write("agent_name,owned_website_url,status,evidence_or_note\n")
            handle.write("Dexters,https://www.dexters.co.uk,confirmed_recent,from OTM\n")
            handle.flush()

            entries = load_agent_directory(handle.name)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].agent_name, "Dexters")
        self.assertEqual(entries[0].owned_website_url, "https://www.dexters.co.uk")
        self.assertEqual(entries[0].status, "confirmed_recent")


if __name__ == "__main__":
    unittest.main()
