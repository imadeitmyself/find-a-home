import contextlib
import io
import tempfile
import unittest

from rental_alert_bot.models import AppConfig, Criteria, Listing
from rental_alert_bot.runner import _process_candidates
from rental_alert_bot.store import ListingStore
from rental_alert_bot.models import SourceConfig


class FailingNotifier:
    channel = "test"

    def send(self, listing, message):
        raise RuntimeError("send failed")


class StoreTests(unittest.TestCase):
    def test_upsert_seen_returns_new_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ListingStore("%s/listings.sqlite3" % tmp)
            listing = Listing(
                source="Test",
                external_id="abc",
                url="https://example.com/listing",
                title="Example",
                raw_text="Example E9 2 bedroom GBP 3000 pcm",
                price_pcm=3000,
                bedrooms=2,
                postcode_area="E9",
            )
            self.assertTrue(store.upsert_seen(listing))
            self.assertFalse(store.upsert_seen(listing))
            self.assertEqual(store.count(), 1)
            store.close()

    def test_failed_alert_does_not_mark_listing_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ListingStore("%s/listings.sqlite3" % tmp)
            source = SourceConfig(name="Test", urls=["https://example.com"])
            config = AppConfig(
                poll_interval_seconds=90,
                request_timeout_seconds=15,
                respect_robots_txt=True,
                database_path=store.path,
                user_agent="test",
                criteria=Criteria(
                    postcode_areas=["E9"],
                    bedrooms=2,
                    min_price_pcm=2750,
                    max_price_pcm=3750,
                ),
                sources=[source],
            )
            listing = Listing(
                source="Test",
                external_id="alert-fail",
                url="https://example.com/listing",
                title="Example",
                raw_text="Example E9 2 bedroom GBP 3000 pcm",
                price_pcm=3000,
                bedrooms=2,
                postcode_area="E9",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                accepted = _process_candidates(
                    config=config,
                    source=source,
                    candidates=[listing],
                    store=store,
                    notifier=FailingNotifier(),
                    dry_run=False,
                    seed=False,
                )

            self.assertEqual(accepted, 1)
            self.assertEqual(store.count(), 0)
            store.close()

    def test_dedupes_same_external_id_across_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ListingStore("%s/listings.sqlite3" % tmp)
            first = Listing(
                source="Foxtons E9",
                external_id="same-property",
                url="https://www.foxtons.co.uk/properties-to-rent/e8/example",
                title="Example",
                raw_text="Example E8 2 bedroom GBP 3000 pcm",
                price_pcm=3000,
                bedrooms=2,
                postcode_area="E8",
            )
            second = Listing(
                source="Foxtons E8",
                external_id="same-property",
                url="https://www.foxtons.co.uk/properties-to-rent/e8/example",
                title="Example",
                raw_text="Example E8 2 bedroom GBP 3000 pcm",
                price_pcm=3000,
                bedrooms=2,
                postcode_area="E8",
            )

            self.assertTrue(store.upsert_seen(first))
            self.assertFalse(store.upsert_seen(second))
            self.assertEqual(store.count(), 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
