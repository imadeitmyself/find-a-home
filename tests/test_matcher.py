import unittest

from rental_alert_bot.matcher import match_listing
from rental_alert_bot.models import Criteria, Listing, SourceConfig


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.criteria = Criteria(
            postcode_areas=["E9", "E8", "N1", "E2"],
            bedrooms=2,
            min_price_pcm=2750,
            max_price_pcm=3750,
            exclude_keywords=["short let"],
        )
        self.source = SourceConfig(name="Test", urls=["https://example.com"])

    def listing(self, **overrides):
        data = {
            "source": "Test",
            "external_id": "1",
            "url": "https://example.com/properties/lettings/test",
            "title": "Test E9",
            "raw_text": "Test E9 2 bedroom GBP 3000 pcm",
            "price_pcm": 3000,
            "bedrooms": 2,
            "postcode_area": "E9",
        }
        data.update(overrides)
        return Listing(**data)

    def test_accepts_matching_listing(self):
        result = match_listing(self.listing(), self.criteria, self.source)
        self.assertTrue(result.accepted)

    def test_rejects_wrong_price(self):
        result = match_listing(self.listing(price_pcm=4000), self.criteria, self.source)
        self.assertFalse(result.accepted)
        self.assertIn("price above range", result.reasons)

    def test_rejects_let_listing(self):
        result = match_listing(self.listing(raw_text="Test E9 GBP 3000 pcm Let"), self.criteria, self.source)
        self.assertFalse(result.accepted)
        self.assertIn("unavailable/let", result.reasons)

    def test_rejects_short_let(self):
        result = match_listing(self.listing(raw_text="Short let Test E9 2 bedroom GBP 3000 pcm"), self.criteria, self.source)
        self.assertFalse(result.accepted)
        self.assertIn("excluded keyword: short let", result.reasons)

    def test_rejects_generic_search_page_match(self):
        result = match_listing(
            self.listing(
                title="Properties to rent",
                url="https://www.foxtons.co.uk/properties-to-rent/london",
            ),
            self.criteria,
            self.source,
        )
        self.assertFalse(result.accepted)
        self.assertIn("generic search/listing page", result.reasons)


if __name__ == "__main__":
    unittest.main()
