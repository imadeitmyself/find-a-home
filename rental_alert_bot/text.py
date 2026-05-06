from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, List, Optional


POSTCODE_AREA_RE = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\b", re.IGNORECASE)
PCM_RE = re.compile(
    r"(?:\u00a3|GBP\s*)\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:pcm|p\.?c\.?m\.?|per\s+month|pm|p/m|monthly)",
    re.IGNORECASE,
)
PW_RE = re.compile(
    r"(?:\u00a3|GBP\s*)\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:pw|p\.?w\.?|per\s+week|weekly)",
    re.IGNORECASE,
)
BED_NUMBER_RE = re.compile(r"\b([1-9]\d*)\s*(?:bed|beds|bedroom|bedrooms)\b", re.IGNORECASE)
BED_PLUS_RE = re.compile(r"\b([1-9]\d*)\s*\+\s*(?:bed|beds|bedroom|bedrooms)\b", re.IGNORECASE)
STUDIO_RE = re.compile(r"\bstudio\b", re.IGNORECASE)

WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
}
WORD_BED_RE = re.compile(
    r"\b(one|two|three|four|five|six)[-\s]+(?:bed|beds|bedroom|bedrooms)\b",
    re.IGNORECASE,
)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def text_lines(text: str) -> List[str]:
    lines = []
    for line in re.split(r"[\r\n]+", text or ""):
        cleaned = normalize_space(line)
        if cleaned:
            lines.append(cleaned)
    return lines


def money_to_int(value: str) -> int:
    return int(Decimal(value.replace(",", "")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def weekly_to_pcm(value: str) -> int:
    weekly = Decimal(value.replace(",", ""))
    monthly = (weekly * Decimal(52) / Decimal(12)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(monthly)


def parse_price_pcm(text: str) -> Optional[int]:
    pcm_match = PCM_RE.search(text or "")
    if pcm_match:
        return money_to_int(pcm_match.group(1))

    pw_match = PW_RE.search(text or "")
    if pw_match:
        return weekly_to_pcm(pw_match.group(1))

    return None


def parse_bedrooms(text: str) -> Optional[int]:
    if not text:
        return None
    if STUDIO_RE.search(text):
        return 0
    if BED_PLUS_RE.search(text):
        return None

    number_match = BED_NUMBER_RE.search(text)
    if number_match:
        return int(number_match.group(1))

    word_match = WORD_BED_RE.search(text)
    if word_match:
        return WORD_NUMBERS[word_match.group(1).lower()]

    return None


def parse_postcode_area(text: str, allowed_areas: Optional[Iterable[str]] = None) -> Optional[str]:
    allowed = {area.upper() for area in allowed_areas} if allowed_areas else None
    for match in POSTCODE_AREA_RE.finditer(text or ""):
        area = match.group(1).upper()
        if allowed is None or area in allowed:
            return area
    return None


def contains_keyword(text: str, keywords: Iterable[str]) -> Optional[str]:
    lowered = (text or "").lower()
    for keyword in keywords:
        if keyword.lower() in lowered:
            return keyword
    return None


def is_unavailable_text(text: str) -> bool:
    normalized = normalize_space(text).lower()
    lines = [line.lower() for line in text_lines(text)]

    if any(line in {"let", "let agreed", "reserved", "under offer"} for line in lines):
        return True

    unavailable_patterns = [
        r"\blet\s+agreed\b",
        r"\bnow\s+let\b",
        r"\brecently\s+let\b",
        r"\bunder\s+offer\b",
        r"\breserved\b",
        r"\bpcm\s+let\b",
        r"\bpw\s+let\b",
        r"\bper\s+week\s+let\b",
        r"\bper\s+month\s+let\b",
    ]
    return any(re.search(pattern, normalized) for pattern in unavailable_patterns)


def is_sale_url_or_text(url: str, text: str) -> bool:
    lowered_url = (url or "").lower()
    if any(part in lowered_url for part in ["/for-sale", "/sale/", "/sales/", "properties-for-sale"]):
        return True
    normalized = normalize_space(text).lower()
    return bool(re.search(r"\b(for sale|sold stc|guide price|leasehold|freehold)\b", normalized))


def is_generic_listing_title(title: str, url: str) -> bool:
    normalized_title = normalize_space(title).lower()
    generic_titles = {
        "properties to rent",
        "property to rent",
        "flats to rent",
        "apartments to rent",
        "houses to rent",
        "build to rent",
        "yes, value my home for free",
        "log in to reveal them",
    }
    if normalized_title in generic_titles:
        return True

    normalized_url = (url or "").rstrip("/").lower()
    generic_suffixes = (
        "/properties-to-rent/london",
        "/property-to-rent",
        "/properties/lettings",
        "/to-rent",
    )
    return any(normalized_url.endswith(suffix) for suffix in generic_suffixes)
