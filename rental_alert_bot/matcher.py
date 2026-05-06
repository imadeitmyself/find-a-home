from __future__ import annotations

from .models import Criteria, Listing, MatchResult, SourceConfig
from .text import contains_keyword, is_generic_listing_title, is_sale_url_or_text, is_unavailable_text


def match_listing(listing: Listing, criteria: Criteria, source: SourceConfig) -> MatchResult:
    reasons = []

    if is_sale_url_or_text(listing.url, listing.raw_text):
        reasons.append("sale listing")

    if is_generic_listing_title(listing.title, listing.url):
        reasons.append("generic search/listing page")

    if is_unavailable_text(listing.raw_text):
        reasons.append("unavailable/let")

    excluded_keyword = contains_keyword(listing.raw_text, list(criteria.exclude_keywords) + list(source.excluded_keywords))
    if excluded_keyword:
        reasons.append("excluded keyword: %s" % excluded_keyword)

    if listing.price_pcm is None:
        reasons.append("missing price")
    elif listing.price_pcm < criteria.min_price_pcm:
        reasons.append("price below range")
    elif listing.price_pcm > criteria.max_price_pcm:
        reasons.append("price above range")

    if listing.bedrooms is None:
        reasons.append("missing bedrooms")
    elif listing.bedrooms != criteria.bedrooms:
        reasons.append("bedroom mismatch")

    if listing.postcode_area is None:
        if not criteria.allow_unknown_area:
            reasons.append("missing postcode area")
    elif listing.postcode_area.upper() not in set(criteria.postcode_areas):
        reasons.append("postcode area mismatch")

    return MatchResult(accepted=not reasons, reasons=reasons)
