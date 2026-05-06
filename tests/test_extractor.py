import unittest

from rental_alert_bot.extractor import extract_listings


class ExtractorTests(unittest.TestCase):
    def test_extracts_listing_card(self):
        html = """
        <html><body>
          <ul>
            <li class="property-card">
              <a href="/property-lettings/flat-to-rent-in-cadogan-terrace-e9">Cadogan Terrace Victoria Park, E9</a>
              <p>GBP 680 Pw / GBP 2,947 Pcm</p>
              <span>2 Bedrooms</span>
              <p>A large two double bedroom apartment.</p>
            </li>
          </ul>
        </body></html>
        """

        listings = extract_listings("Dexters E9", "https://www.dexters.co.uk/property-lettings/flats-to-rent-in-e9", html, ["E9"])

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.title, "Cadogan Terrace Victoria Park, E9")
        self.assertEqual(listing.price_pcm, 2947)
        self.assertEqual(listing.bedrooms, 2)
        self.assertEqual(listing.postcode_area, "E9")
        self.assertEqual(
            listing.url,
            "https://www.dexters.co.uk/property-lettings/flat-to-rent-in-cadogan-terrace-e9",
        )

    def test_extracts_json_ld_listing(self):
        html = """
        <script type="application/ld+json">
        {
          "@type": "Apartment",
          "name": "Graham Road, London, E8",
          "url": "/properties/lettings/graham-road-london-e8/HAC220227",
          "offers": {"price": "GBP 3200 pcm"},
          "numberOfBedrooms": 2
        }
        </script>
        """

        listings = extract_listings("Winkworth", "https://www.winkworth.co.uk/search", html, ["E8"])

        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].price_pcm, 3200)
        self.assertEqual(listings[0].bedrooms, 2)
        self.assertEqual(listings[0].postcode_area, "E8")


if __name__ == "__main__":
    unittest.main()
