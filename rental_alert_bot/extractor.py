from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

from .models import Listing
from .text import normalize_space, parse_bedrooms, parse_postcode_area, parse_price_pcm, text_lines


PROPERTY_URL_HINTS = (
    "to-rent",
    "lettings",
    "letting",
    "property-lettings",
    "properties-for-letting",
    "flats-to-rent",
    "houses-to-rent",
    "/rent/",
    "/properties/lettings/",
)
SALE_URL_HINTS = ("for-sale", "/sale/", "/sales/", "properties-for-sale")
PAGINATION_HINTS = ("page=", "/page/", "?p=", "&p=", "pageno", "pagenumber")
NOISE_LINK_TEXT = {"", "image", "view", "details", "read more", "tenant info", "fees may apply"}
CANDIDATE_ATTR_RE = re.compile(r"(property|listing|result|card|tile|search|letting|rental)", re.IGNORECASE)
TITLE_NOISE_RE = re.compile(
    r"(?:£|gbp)\s*[\d,]+(?:\.\d+)?\s*"
    r"(?:pcm|pw|p\.?c\.?m\.?|p\.?w\.?|per\s+month|per\s+week|monthly|weekly|pm|p/m)?"
    r"|\b\d+\s*(?:bed|beds|bedroom|bedrooms)\b"
    r"|\bstudio\b",
    re.IGNORECASE,
)


@dataclass
class Link:
    href: str
    text: str


@dataclass
class Segment:
    text: str
    links: List[Link] = field(default_factory=list)


@dataclass
class Node:
    tag: str
    attrs: Dict[str, str]
    text_parts: List[str] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)

    def text(self) -> str:
        return normalize_space(" ".join(self.text_parts))


class ListingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: List[Node] = []
        self.segments: List[Segment] = []
        self.links: List[Link] = []
        self.text_parts: List[str] = []
        self.scripts: List[str] = []
        self._script_parts: Optional[List[str]] = None
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        lowered_tag = tag.lower()

        if lowered_tag in {"style", "noscript"}:
            self._skip_depth += 1
            return

        if lowered_tag == "script":
            script_type = attrs_dict.get("type", "").lower()
            script_id = attrs_dict.get("id", "").lower()
            if "json" in script_type or script_id == "__next_data__":
                self._script_parts = []
            else:
                self._skip_depth += 1
            return

        if self._skip_depth:
            return

        if lowered_tag in {"div", "li", "article", "section", "p", "br", "h1", "h2", "h3"}:
            self.text_parts.append("\n")
            if self.stack:
                self.stack[-1].text_parts.append("\n")

        self.stack.append(Node(lowered_tag, attrs_dict))

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)
            return
        if self._skip_depth:
            return

        cleaned = unescape(data)
        if not cleaned.strip():
            return
        self.text_parts.append(cleaned)
        if self.stack:
            self.stack[-1].text_parts.append(cleaned)

    def handle_endtag(self, tag: str) -> None:
        lowered_tag = tag.lower()

        if lowered_tag == "script":
            if self._script_parts is not None:
                script = "".join(self._script_parts).strip()
                if script:
                    self.scripts.append(script)
                self._script_parts = None
            elif self._skip_depth:
                self._skip_depth -= 1
            return

        if lowered_tag in {"style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return

        if self._skip_depth or not self.stack:
            return

        index = None
        for pos in range(len(self.stack) - 1, -1, -1):
            if self.stack[pos].tag == lowered_tag:
                index = pos
                break
        if index is None:
            return

        node = self.stack.pop(index)
        node_text = node.text()

        if node.tag == "a":
            href = node.attrs.get("href", "")
            link = Link(href=href, text=node_text)
            node.links.append(link)
            self.links.append(link)

        if _is_candidate_node(node):
            self.segments.append(Segment(text=node_text, links=list(node.links)))

        if self.stack:
            parent = self.stack[-1]
            if node_text:
                parent.text_parts.append(" " + node_text + " ")
            parent.links.extend(node.links)

        if lowered_tag in {"div", "li", "article", "section", "p", "h1", "h2", "h3"}:
            self.text_parts.append("\n")
            if self.stack:
                self.stack[-1].text_parts.append("\n")

    def full_text(self) -> str:
        return "\n".join(text_lines(" ".join(self.text_parts)))


def extract_listings(source: str, page_url: str, html: str, allowed_areas: Optional[Iterable[str]] = None) -> List[Listing]:
    parser = ListingHTMLParser()
    parser.feed(html)

    listings: List[Listing] = []
    for listing in _extract_from_json_scripts(source, page_url, parser.scripts, allowed_areas):
        listings.append(listing)

    for segment in parser.segments:
        listing = _listing_from_segment(source, page_url, segment, allowed_areas)
        if listing:
            listings.append(listing)

    # Free-text windowing is a fallback for pages without structured cards/JSON-LD.
    # When structured strategies already found listings, windows only add noisy
    # multi-card blobs, so skip them.
    if not listings:
        for segment in _window_segments_from_text(parser.full_text(), parser.links):
            listing = _listing_from_segment(source, page_url, segment, allowed_areas)
            if listing:
                listings.append(listing)

    return _dedupe_listings(listings)


def _is_candidate_node(node: Node) -> bool:
    attrs = " ".join([node.attrs.get("class", ""), node.attrs.get("id", ""), node.attrs.get("data-testid", "")])
    if node.tag in {"article", "li"}:
        return True
    if node.tag in {"div", "section"} and CANDIDATE_ATTR_RE.search(attrs):
        return True
    if node.tag == "a" and looks_like_property_url(node.attrs.get("href", "")):
        return True
    return False


def looks_like_property_url(url: str) -> bool:
    lowered = (url or "").lower()
    if not lowered or lowered.startswith("#") or lowered.startswith("javascript:"):
        return False
    if any(hint in lowered for hint in SALE_URL_HINTS):
        return False
    if any(hint in lowered for hint in PAGINATION_HINTS):
        return False
    return any(hint in lowered for hint in PROPERTY_URL_HINTS)


def _listing_from_segment(
    source: str,
    page_url: str,
    segment: Segment,
    allowed_areas: Optional[Iterable[str]],
) -> Optional[Listing]:
    text = normalize_space(segment.text)
    if not text:
        return None

    price_pcm = parse_price_pcm(text)
    bedrooms = parse_bedrooms(text)
    postcode_area = parse_postcode_area(text, allowed_areas)

    if price_pcm is None and bedrooms is None and postcode_area is None:
        return None

    main_link = _choose_main_link(segment.links)
    title = _choose_title(text, main_link)

    area_inferred = False
    if postcode_area is None:
        postcode_area = _infer_area_from_url(page_url, allowed_areas)
        area_inferred = postcode_area is not None

    resolved = urljoin(page_url, main_link.href) if main_link else page_url
    if main_link and not _same_page(resolved, page_url):
        url = resolved
        external_id = stable_listing_id(url, title)
        search_page = False
    else:
        url = page_url
        external_id = stable_listing_id(
            url, title, fallback_parts=[_normalize_title(title), price_pcm, bedrooms, postcode_area]
        )
        search_page = True

    listing = Listing(
        source=source,
        external_id=external_id,
        url=url,
        title=title,
        raw_text=text[:4000],
        price_pcm=price_pcm,
        bedrooms=bedrooms,
        postcode_area=postcode_area,
        address=title,
    )
    if search_page:
        listing.metadata["search_page"] = True
    if area_inferred:
        listing.metadata["area_inferred"] = True
    return listing


def _choose_main_link(links: Sequence[Link]) -> Optional[Link]:
    property_links = [link for link in links if looks_like_property_url(link.href)]
    if property_links:
        return _best_text_link(property_links)
    return None


def _best_text_link(links: Sequence[Link]) -> Optional[Link]:
    usable = []
    for link in links:
        text = normalize_space(link.text)
        if text.lower() not in NOISE_LINK_TEXT and len(text) > 2:
            usable.append(Link(href=link.href, text=text))
    if usable:
        return max(usable, key=lambda item: len(item.text))
    return links[0] if links else None


def _choose_title(text: str, link: Optional[Link]) -> str:
    if link and normalize_space(link.text).lower() not in NOISE_LINK_TEXT:
        return normalize_space(link.text)[:180]

    for line in text_lines(text):
        lowered = line.lower()
        if lowered.startswith("image") or lowered.startswith("fees") or lowered.startswith("tenant info"):
            continue
        if parse_price_pcm(line) is not None:
            continue
        if len(line) > 3:
            return line[:180]

    return text[:180]


def stable_listing_id(url: str, title: str, fallback_parts: Optional[Sequence[Any]] = None) -> str:
    if fallback_parts is not None:
        joined = "|".join(
            normalize_space(str(part)).lower()
            for part in fallback_parts
            if part is not None and str(part).strip()
        )
        if joined:
            return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]
    parsed = urlparse(url)
    identity = parsed.path.rstrip("/") or normalize_space(title).lower()
    if parsed.netloc:
        identity = parsed.netloc.lower() + identity
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _normalize_title(title: str) -> str:
    return normalize_space(TITLE_NOISE_RE.sub(" ", normalize_space(title or "").lower()))


def _same_page(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.netloc.lower(), pa.path.rstrip("/").lower()) == (pb.netloc.lower(), pb.path.rstrip("/").lower())


def _infer_area_from_url(page_url: str, allowed_areas: Optional[Iterable[str]]) -> Optional[str]:
    if not allowed_areas:
        return None
    path = urlparse(page_url).path.upper()
    tokens = set(re.findall(r"[A-Z]{1,2}\d{1,2}[A-Z]?", path))
    found = [area.upper() for area in allowed_areas if area.upper() in tokens]
    return found[0] if len(found) == 1 else None


def _dedupe_listings(listings: Sequence[Listing]) -> List[Listing]:
    deduped: Dict[str, Listing] = {}
    for listing in listings:
        key = listing.external_id
        existing = deduped.get(key)
        if not existing:
            deduped[key] = listing
            continue
        deduped[key] = _prefer_richer_listing(existing, listing)

    result = list(deduped.values())
    real_keys = {_content_key(item) for item in result if not item.metadata.get("search_page")}
    return [
        item
        for item in result
        if not (item.metadata.get("search_page") and _content_key(item) in real_keys)
    ]


def _content_key(listing: Listing) -> Tuple[str, Optional[int], Optional[int]]:
    return (_normalize_title(listing.title), listing.price_pcm, listing.bedrooms)


def _prefer_richer_listing(left: Listing, right: Listing) -> Listing:
    left_score = sum(value is not None for value in [left.price_pcm, left.bedrooms, left.postcode_area]) + len(left.raw_text)
    right_score = sum(value is not None for value in [right.price_pcm, right.bedrooms, right.postcode_area]) + len(right.raw_text)
    return right if right_score > left_score else left


def _window_segments_from_text(full_text: str, links: Sequence[Link]) -> List[Segment]:
    lines = text_lines(full_text)
    segments: List[Segment] = []
    for index, line in enumerate(lines):
        if parse_price_pcm(line) is None:
            continue
        start = max(0, index - 4)
        end = min(len(lines), index + 9)
        window = "\n".join(lines[start:end])
        window_links = _links_for_window(window, links)
        segments.append(Segment(text=window, links=window_links))
    return segments


def _links_for_window(window: str, links: Sequence[Link]) -> List[Link]:
    lowered = window.lower()
    matched = []
    for link in links:
        text = normalize_space(link.text)
        if text and text.lower() in lowered:
            matched.append(link)
    return matched


def _extract_from_json_scripts(
    source: str,
    page_url: str,
    scripts: Sequence[str],
    allowed_areas: Optional[Iterable[str]],
) -> List[Listing]:
    listings: List[Listing] = []
    for script in scripts:
        try:
            payload = json.loads(script)
        except json.JSONDecodeError:
            continue
        for item in _walk_json_dicts(payload):
            listing = _listing_from_json_dict(source, page_url, item, allowed_areas)
            if listing:
                listings.append(listing)
    return listings


def _walk_json_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_dicts(child)


def _listing_from_json_dict(
    source: str,
    page_url: str,
    item: Dict[str, Any],
    allowed_areas: Optional[Iterable[str]],
) -> Optional[Listing]:
    url_value = _first_string(item, ["url", "@id", "canonicalUrl", "propertyUrl"])
    name = _first_string(item, ["name", "title", "displayAddress", "address"])
    price = _first_number_or_string(item, ["price", "pricePCM", "rent", "amount"])
    bedrooms = _first_number_or_string(item, ["bedrooms", "bedroomCount", "numberOfBedrooms"])

    flattened = _flatten_json_text(item)
    if not url_value and not name:
        return None
    if price is None and parse_price_pcm(flattened) is None:
        return None

    text = normalize_space(" ".join(part for part in [name or "", str(price or ""), flattened] if part))
    title = (name or _choose_title(text, None))[:180]

    parsed_price = int(price) if isinstance(price, (int, float)) and 1000 <= int(price) <= 10000 else parse_price_pcm(text)
    parsed_beds = int(bedrooms) if isinstance(bedrooms, (int, float)) else parse_bedrooms(text)

    postcode_area = parse_postcode_area(text, allowed_areas)
    area_inferred = False
    if postcode_area is None:
        postcode_area = _infer_area_from_url(page_url, allowed_areas)
        area_inferred = postcode_area is not None

    resolved = urljoin(page_url, str(url_value)) if url_value else page_url
    if url_value and not _same_page(resolved, page_url):
        url = resolved
        external_id = stable_listing_id(url, title)
        search_page = False
    else:
        url = page_url
        external_id = stable_listing_id(
            url, title, fallback_parts=[_normalize_title(title), parsed_price, parsed_beds, postcode_area]
        )
        search_page = True

    listing = Listing(
        source=source,
        external_id=external_id,
        url=url,
        title=title,
        raw_text=text[:4000],
        price_pcm=parsed_price,
        bedrooms=parsed_beds,
        postcode_area=postcode_area,
        address=title,
    )
    if search_page:
        listing.metadata["search_page"] = True
    if area_inferred:
        listing.metadata["area_inferred"] = True
    return listing


def _first_string(item: Dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = _first_string(value, ["name", "streetAddress", "addressLocality"])
            if nested:
                return nested
    return None


def _first_number_or_string(item: Dict[str, Any], keys: Sequence[str]) -> Optional[Any]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, (int, float, str)) and str(value).strip():
            return value
        if isinstance(value, dict):
            nested = _first_number_or_string(value, keys)
            if nested is not None:
                return nested
    return None


def _flatten_json_text(item: Any) -> str:
    parts: List[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, (str, int, float)):
            text = str(value)
            if len(text) <= 300:
                parts.append(text)

    visit(item)
    return normalize_space(" ".join(parts))
