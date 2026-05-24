from __future__ import annotations

import logging
import time
from typing import Iterable, List, Optional, Tuple

from .browser import BrowserFetcher
from .config import load_live_sources
from .extractor import extract_listings
from .health import SourceOutcome
from .http import RobotsCache, fetch_text
from .matcher import match_listing
from .models import AppConfig, Listing, SourceConfig
from .notifiers import Notifier, format_alert
from .store import ListingStore
from .text import recent_listing_reason

logger = logging.getLogger(__name__)


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
    outcomes: List[SourceOutcome] = []

    live_sources = [s for s in load_live_sources(config) if s.enabled]
    plain_sources = [s for s in live_sources if s.tier == 3]
    browser_sources = [s for s in live_sources if s.tier in (1, 2)]

    # --- Tier 3: plain urllib ---
    for source in plain_sources:
        for url in source.urls:
            html, outcome = _fetch_plain(source, url, robots, config)
            if html is not None:
                candidates = _extract_and_count(html, source, url, config, outcome)
                accepted_count += _process_candidates(
                    config, source, candidates, store, notifier, dry_run, seed, recent_only_minutes,
                )
            if outcome.outcome not in ("skip",):
                outcomes.append(outcome)

    # --- Tier 1: Camoufox + proxy ---
    tier1_sources = [s for s in browser_sources if s.tier == 1]
    tier2_sources = [s for s in browser_sources if s.tier == 2]

    for tier_sources, use_proxy, label in [
        (tier1_sources, True, "T1"),
        (tier2_sources, False, "T2"),
    ]:
        if not tier_sources:
            continue
        try:
            with BrowserFetcher(timeout_seconds=config.request_timeout_seconds, use_proxy=use_proxy) as fetcher:
                if use_proxy and fetcher._proxy:
                    logger.info("Browser [%s]: proxy enabled via %s", label, fetcher._proxy.get("server", ""))
                elif use_proxy:
                    logger.info("Browser [%s]: no proxy configured, running headless-only", label)
                for source in tier_sources:
                    for url in source.urls:
                        html, outcome = _fetch_browser(source, url, robots, config, fetcher)
                        if html is not None:
                            candidates = _extract_and_count(html, source, url, config, outcome)
                            accepted_count += _process_candidates(
                                config, source, candidates, store, notifier,
                                dry_run, seed, recent_only_minutes,
                            )
                        if outcome.outcome not in ("skip",):
                            outcomes.append(outcome)
        except Exception as exc:
            logger.error("Browser [%s] unavailable: %s", label, exc)
            for source in tier_sources:
                for url in source.urls:
                    outcomes.append(SourceOutcome(source.name, url, "error", error_detail=str(exc)))

    if not dry_run and store is not None:
        _process_health(outcomes, store)

    return accepted_count


def _fetch_plain(
    source: SourceConfig,
    url: str,
    robots: RobotsCache,
    config: AppConfig,
) -> Tuple[Optional[str], SourceOutcome]:
    """Returns (html, outcome). html is None on failure or skip."""
    logger.info("FETCH [T3] %s: %s", source.name, url)
    if config.respect_robots_txt and not robots.can_fetch(url):
        logger.info("SKIP robots.txt disallows %s", url)
        return None, SourceOutcome(source.name, url, "skip")
    try:
        html = fetch_text(url, config.user_agent, config.request_timeout_seconds)
        return html, SourceOutcome(source.name, url, "ok")
    except Exception as exc:
        error_str = str(exc)
        outcome_type = "http_error" if error_str.startswith("HTTP") else "network_error"
        logger.error("ERROR [T3] %s: %s", source.name, error_str)
        return None, SourceOutcome(source.name, url, outcome_type, error_detail=error_str)


def _fetch_browser(
    source: SourceConfig,
    url: str,
    robots: RobotsCache,
    config: AppConfig,
    fetcher: BrowserFetcher,
) -> Tuple[Optional[str], SourceOutcome]:
    """Returns (html, outcome). html is None on failure or skip."""
    tier_label = "T%s" % source.tier
    logger.info("FETCH [%s] %s: %s", tier_label, source.name, url)
    if config.respect_robots_txt and not robots.can_fetch(url):
        logger.info("SKIP robots.txt disallows %s", url)
        return None, SourceOutcome(source.name, url, "skip")
    try:
        html = fetcher.fetch(url)
        return html, SourceOutcome(source.name, url, "ok")
    except Exception as exc:
        error_str = str(exc)
        outcome_type = "http_error" if "HTTP" in error_str else "network_error"
        logger.error("ERROR [%s] %s: %s", tier_label, source.name, error_str)
        return None, SourceOutcome(source.name, url, outcome_type, error_detail=error_str)


def _extract_and_count(
    html: str,
    source: SourceConfig,
    url: str,
    config: AppConfig,
    outcome: SourceOutcome,
) -> List[Listing]:
    candidates = extract_listings(source.name, url, html, allowed_areas=config.criteria.postcode_areas)
    tier_label = "T%d" % source.tier
    if candidates:
        logger.info("RESULT [%s] %s: %d candidates", tier_label, source.name, len(candidates))
    else:
        logger.warning("RESULT [%s] %s: EMPTY", tier_label, source.name)
    outcome.outcome = "ok" if candidates else "empty"
    outcome.candidate_count = len(candidates)
    return candidates


def _process_health(outcomes: List[SourceOutcome], store: ListingStore) -> None:
    # Record every outcome so the daily report can summarise tracker health.
    # Instant per-run health alerts are intentionally not sent here: tracker
    # status (which trackers are working vs. failing) is delivered once a day
    # via the `daily-report` command instead, to avoid a flood of signals.
    for outcome in outcomes:
        store.health.record(outcome)


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
                logger.info("REJECT %s :: %s", listing.title, ", ".join(result.reasons))
            continue

        if recent_only_minutes is not None:
            recent_reason = recent_listing_reason(
                "%s %s" % (listing.title, listing.raw_text),
                max_age_minutes=recent_only_minutes,
            )
            if recent_reason is None:
                if dry_run:
                    logger.info(
                        "REJECT_RECENCY %s :: no explicit <=%s minute listing marker",
                        listing.title, recent_only_minutes,
                    )
                continue
            listing.metadata["recent_reason"] = recent_reason

        accepted_count += 1
        message = format_alert(listing)
        if dry_run:
            logger.info("MATCH %s", listing.title)
            continue

        if store is None:
            raise RuntimeError("Store is required unless dry_run is true.")

        if seed:
            is_new = store.upsert_seen(listing)
            if is_new:
                logger.info("SEEDED %s", listing.title)
            continue

        if store.has_seen(listing):
            store.upsert_seen(listing)
            continue

        if notifier is None:
            raise RuntimeError("Notifier is required unless dry_run or seed is true.")
        try:
            notifier.send(listing, message)
        except Exception as exc:
            logger.error("Alert failed for %s: %s", listing.title, exc)
            continue
        store.upsert_seen(listing)
        store.mark_alerted(listing, getattr(notifier, "channel", "unknown"), message)
        logger.info("ALERTED %s", listing.title)

    return accepted_count
