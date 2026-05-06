import unittest

from rental_alert_bot.text import (
    is_unavailable_text,
    parse_bedrooms,
    parse_postcode_area,
    parse_price_pcm,
    recent_listing_reason,
)


class TextParsingTests(unittest.TestCase):
    def test_parse_pcm_prices(self):
        self.assertEqual(parse_price_pcm("GBP 3,250 pcm"), 3250)
        self.assertEqual(parse_price_pcm("\u00a3635 per week (\u00a32,750 per month)"), 2750)
        self.assertEqual(parse_price_pcm("\u00a3576 Pw / \u00a32,496 Pcm"), 2496)

    def test_parse_weekly_price_when_pcm_absent(self):
        self.assertEqual(parse_price_pcm("\u00a3692 pw"), 2999)

    def test_parse_bedrooms(self):
        self.assertEqual(parse_bedrooms("2 Bedrooms"), 2)
        self.assertEqual(parse_bedrooms("two-bedroom flat"), 2)
        self.assertEqual(parse_bedrooms("Studio flat"), 0)
        self.assertIsNone(parse_bedrooms("2+ bedrooms"))

    def test_parse_postcode_area(self):
        self.assertEqual(parse_postcode_area("Cadogan Terrace, E9", ["E9", "E8"]), "E9")
        self.assertIsNone(parse_postcode_area("Boleyn Road, N16", ["N1"]))

    def test_unavailable_detection(self):
        self.assertTrue(is_unavailable_text("GBP 2,999 Pcm Let (Tenant Info)"))
        self.assertTrue(is_unavailable_text("LET\n2 bedroom flat"))
        self.assertFalse(is_unavailable_text("2 bedroom flat to let in E9"))

    def test_recent_listing_reason(self):
        self.assertEqual(recent_listing_reason("Added 35 minutes ago"), "explicitly marked 35 minutes ago")
        self.assertEqual(recent_listing_reason("Listed less than an hour ago"), "explicitly marked less than an hour ago")
        self.assertEqual(recent_listing_reason("Just added"), "explicitly marked just added/updated")
        self.assertIsNone(recent_listing_reason("Added 2 hours ago"))
        self.assertIsNone(recent_listing_reason("Added today"))


if __name__ == "__main__":
    unittest.main()
