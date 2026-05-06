from __future__ import annotations

import time
from typing import Iterable, Optional

from .config import load_live_sources
from .extractor import extract_listings
from .http import RobotsCache, fetch_text
from .matcher import match_listing
from .models import AppConfig, Listing, SourceConfig
from .notifiers import Notifier, format_alert
from .store import ListingStore
from .text import recent_listing_reason


def run_forever(
    config: AppConfig,
    store: ListingStore,
    notifier: Notifier,
    interval_seconds: Optional[int] = None,
) -> None:
    interval = interval_seconds or config.poll_interval_seconds
    while True:
        run_once(config=config, store=store, notifier=notifier, dry_run=False, seed=False)
        time.sleep(interval)


def run_once(
    config: AppConfig,
    store: Optional[ListingStore],
    notifier: Optional[Notifier],
    dry_run: bool,
    seed: bool,
    recent_only_minutes: Optional[int] = None,
) -> int:
    robots = RobotsCache(config.user_agent, config.request_timeout_seconds)
    accepted_count = 0

    for source in load_live_sources(config):
        if not source.enabled:
            continue
        for url in source.urls:
            print("Fetching %s: %s" % (source.name, url), flush=True)
            if config.respect_robots_txt and not robots.can_fetch(url):
                print("SKIP robots.txt disallows %s" % url, flush=True)
                continue
            try:
                html = fetch_text(url, config.user_agent, config.request_timeout_seconds)
            except Exception as exc:
                print("ERROR %s" % exc, flush=True)
                continue

            candidates = extract_listings(source.name, url, html, allowed_areas=config.criteria.postcode_areas)
            print("Found %s candidates on %s" % (len(candidates), source.name), flush=True)
            accepted_count += _process_candidates(
                config,
                source,
                candidates,
                store,
                notifier,
                dry_run,
                seed,
                recent_only_minutes=recent_only_minutes,
            )

    return accepted_count


def _process_candidates(
    config: AppConfig,
    source: SourceConfig,
    candidates: Iterable[Listing],
    store: Optional[ListingStore],
    notifier: Optional[Notifier],
    dry_run: bool,
    seed: bool,
    recent_only_minutes: Optional[int] = None,
) -> int:
    accepted_count = 0
    for listing in candidates:
        result = match_listing(listing, config.criteria, source)
        if not result.accepted:
            if dry_run:
                print("REJECT %s :: %s" % (listing.title, ", ".join(result.reasons)), flush=True)
            continue

        if recent_only_minutes is not None:
            recent_reason = recent_listing_reason(
                "%s %s" % (listing.title, listing.raw_text),
                max_age_minutes=recent_only_minutes,
            )
            if recent_reason is None:
                if dry_run:
                    print(
                        "REJECT_RECENCY %s :: no explicit <=%s minute listing marker"
                        % (listing.title, recent_only_minutes),
                        flush=True,
                    )
                continue
            listing.metadata["recent_reason"] = recent_reason

        accepted_count += 1
        message = format_alert(listing)
        if dry_run:
            print("MATCH\n%s\n" % message, flush=True)
            continue

        if store is None:
            raise RuntimeError("Store is required unless dry_run is true.")

        if seed:
            is_new = store.upsert_seen(listing)
            if is_new:
                print("SEEDED %s" % listing.title, flush=True)
            continue

        if store.has_seen(listing):
            store.upsert_seen(listing)
            continue

        if notifier is None:
            raise RuntimeError("Notifier is required unless dry_run or seed is true.")
        try:
            notifier.send(listing, message)
        except Exception as exc:
            print("ERROR alert failed for %s: %s" % (listing.title, exc), flush=True)
            continue
        store.upsert_seen(listing)
        store.mark_alerted(listing, getattr(notifier, "channel", "unknown"), message)
        print("ALERTED %s" % listing.title, flush=True)

    return accepted_count
