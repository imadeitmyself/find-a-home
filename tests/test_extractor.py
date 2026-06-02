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

    def test_cards_without_own_links_stay_distinct(self):
        html = """
        <html><body>
          <ul>
            <li class="property-card">
              <span>Mare Street, E8</span>
              <p>GBP 2,900 Pcm</p>
              <span>2 Bedrooms</span>
            </li>
            <li class="property-card">
              <span>Wilton Way, E8</span>
              <p>GBP 3,100 Pcm</p>
              <span>2 Bedrooms</span>
            </li>
          </ul>
        </body></html>
        """
        page_url = "https://www.foxtons.co.uk/flats-to-rent/e8/2-bedroom"
        listings = extract_listings("Foxtons E8", page_url, html, ["E8"])

        with_data = [l for l in listings if l.price_pcm in (2900, 3100)]
        self.assertEqual(len(with_data), 2)
        ids = {l.external_id for l in with_data}
        self.assertEqual(len(ids), 2)
        for listing in with_data:
            self.assertTrue(listing.metadata.get("search_page"))
            self.assertEqual(listing.url, page_url)

    def test_prefers_property_link_over_pagination(self):
        html = """
        <html><body>
          <li class="property-card">
            <a href="?page=2">Next page</a>
            <a href="/property-lettings/flat-to-rent-in-graham-road-e8">Graham Road, E8</a>
            <p>GBP 3,000 Pcm</p>
            <span>2 Bedrooms</span>
          </li>
        </body></html>
        """
        page_url = "https://www.dexters.co.uk/property-lettings/flats-to-rent-in-e8"
        listings = extract_listings("Dexters E8", page_url, html, ["E8"])
        listing = next(l for l in listings if l.price_pcm == 3000)
        self.assertEqual(
            listing.url,
            "https://www.dexters.co.uk/property-lettings/flat-to-rent-in-graham-road-e8",
        )
        self.assertFalse(listing.metadata.get("search_page"))

    def test_infers_postcode_area_from_url(self):
        html = """
        <html><body>
          <li class="property-card">
            <a href="/property-lettings/flat-to-rent-in-victoria-park">Victoria Park apartment</a>
            <p>GBP 3,200 Pcm</p>
            <span>2 Bedrooms</span>
          </li>
        </body></html>
        """
        page_url = "https://www.dexters.co.uk/property-lettings/flats-to-rent-in-e9"
        listings = extract_listings("Dexters E9", page_url, html, ["E9"])
        listing = next(l for l in listings if l.price_pcm == 3200)
        self.assertEqual(listing.postcode_area, "E9")
        self.assertTrue(listing.metadata.get("area_inferred"))

    def test_json_ld_real_url_wins_over_search_page_duplicate(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          {"@type": "Apartment", "name": "Graham Road, London, E8",
           "url": "/properties/lettings/graham-road-london-e8/HAC220227",
           "offers": {"price": "GBP 3200 pcm"}, "numberOfBedrooms": 2}
          </script>
          <li class="property-card">
            <span>Graham Road, London, E8</span>
            <p>GBP 3,200 Pcm</p>
            <span>2 Bedrooms</span>
          </li>
        </body></html>
        """
        page_url = "https://www.winkworth.co.uk/properties/lettings"
        listings = extract_listings("Winkworth E8", page_url, html, ["E8"])
        matches = [l for l in listings if l.price_pcm == 3200 and l.bedrooms == 2]
        self.assertEqual(len(matches), 1)
        self.assertFalse(matches[0].metadata.get("search_page"))
        self.assertTrue(matches[0].url.endswith("/HAC220227"))

    def test_recovers_deep_link_from_json_reference(self):
        # Mirrors Foxtons: the visible card yields a search-page URL because its anchor
        # has no text, while __NEXT_DATA__ holds streetName + a propertyReference that
        # matches the anchor href. The listing's URL should be patched to the deep link.
        html = """
        <html><body>
          <script type="application/json">
          {"results": [
             {"propertyReference": "chpk4331830", "postcodeShort": "E9",
              "streetName": "Frampton Park Road", "bedrooms": 2, "pricePcm": 3250}
          ]}
          </script>
          <a href="/properties-to-rent/e9/chpk4331830"></a>
          <a href="/properties-to-rent/e9/1-bedroom">1 bedroom properties to rent in E9</a>
          <li class="property-card">
            <span>Frampton Park Road, Hackney, E9</span>
            <p>2 Beds GBP 3,250 Pcm</p>
          </li>
        </body></html>
        """
        page_url = "https://www.foxtons.co.uk/properties-to-rent/e9?order_by=latest&page=1"
        listings = extract_listings("Foxtons (E9)", page_url, html, ["E9"])

        matches = [l for l in listings if l.price_pcm == 3250 and l.bedrooms == 2]
        self.assertEqual(len(matches), 1)
        listing = matches[0]
        self.assertEqual(
            listing.url, "https://www.foxtons.co.uk/properties-to-rent/e9/chpk4331830"
        )
        self.assertTrue(listing.metadata.get("deep_link"))

    def test_no_deep_link_patch_when_street_is_ambiguous(self):
        # Two flats on the same street with the same bed count produce two candidate URLs;
        # the signature is ambiguous, so neither visible card is mis-linked.
        html = """
        <html><body>
          <script type="application/json">
          {"results": [
             {"propertyReference": "chpk1111111", "postcodeShort": "E9",
              "streetName": "Morning Lane", "bedrooms": 2, "pricePcm": 3000},
             {"propertyReference": "chpk2222222", "postcodeShort": "E9",
              "streetName": "Morning Lane", "bedrooms": 2, "pricePcm": 3200}
          ]}
          </script>
          <a href="/properties-to-rent/e9/chpk1111111"></a>
          <a href="/properties-to-rent/e9/chpk2222222"></a>
          <li class="property-card"><span>Morning Lane, Hackney, E9</span><p>2 Beds GBP 3,000 Pcm</p></li>
        </body></html>
        """
        page_url = "https://www.foxtons.co.uk/properties-to-rent/e9?page=1"
        listings = extract_listings("Foxtons (E9)", page_url, html, ["E9"])
        matches = [l for l in listings if l.bedrooms == 2]
        self.assertTrue(matches)
        for listing in matches:
            self.assertFalse(listing.metadata.get("deep_link"))

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
