import tempfile
import unittest

from rental_alert_bot.config import load_source_file


class SourceFileTests(unittest.TestCase):
    def test_loads_agent_csv_sources(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv") as handle:
            handle.write("enabled,name,postcode_area,listing_url,excluded_keywords,notes\n")
            handle.write("true,Example,E9,https://example.com/to-rent,short let;holiday let,\n")
            handle.flush()

            sources = load_source_file(handle.name)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].name, "Example (E9)")
        self.assertEqual(sources[0].urls, ["https://example.com/to-rent"])
        self.assertEqual(sources[0].excluded_keywords, ["holiday let", "short let"])

    def test_dedupes_repeated_postcode_rows_by_url(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv") as handle:
            handle.write("enabled,name,postcode_area,listing_url,excluded_keywords,notes\n")
            handle.write("true,Example,E9,https://example.com/to-rent/,short let,\n")
            handle.write("true,Example,E8,https://example.com/to-rent,holiday let,\n")
            handle.flush()

            sources = load_source_file(handle.name)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].name, "Example (E8/E9)")
        self.assertEqual(sources[0].urls, ["https://example.com/to-rent/"])
        self.assertEqual(sources[0].excluded_keywords, ["holiday let", "short let"])


if __name__ == "__main__":
    unittest.main()
