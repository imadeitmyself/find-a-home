"""Parsing helpers for magnet links."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

_HEX_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_BTIH_RE = re.compile(r"urn:btih:([0-9a-zA-Z]+)", re.IGNORECASE)


@dataclass
class Magnet:
    infohash: str  # normalized lowercase 40-char hex
    display_name: str  # best-effort human name; falls back to the infohash
    uri: str  # the original magnet URI


def parse_magnet(uri: str) -> Magnet:
    """Parse a magnet URI into its infohash and (optional) display name.

    Accepts both 40-char hex and 32-char base32 BitTorrent infohashes and
    normalizes them to lowercase hex. Raises ``ValueError`` for anything that is
    not a usable BitTorrent magnet link.
    """
    if not uri:
        raise ValueError("Empty magnet link")
    uri = uri.strip()
    if not uri.lower().startswith("magnet:"):
        raise ValueError("Not a magnet link (must start with 'magnet:')")

    # urlsplit treats 'magnet' as the scheme and everything after '?' as the query.
    query = urlsplit(uri).query
    params = parse_qs(query, keep_blank_values=True)

    infohash = _extract_infohash(params, uri)
    if infohash is None:
        raise ValueError("Magnet link has no BitTorrent infohash (urn:btih:)")

    display = ""
    for name in params.get("dn", []):
        if name.strip():
            display = name.strip()
            break

    return Magnet(infohash=infohash, display_name=display or infohash, uri=uri)


def _extract_infohash(params: dict, uri: str):
    candidates = list(params.get("xt", []))
    # parse_qs can miss xt values when there are several; also scan the raw URI.
    candidates.extend(match.group(0) for match in _BTIH_RE.finditer(uri))
    for candidate in candidates:
        match = _BTIH_RE.search(candidate)
        if not match:
            continue
        try:
            return _normalize_infohash(match.group(1))
        except ValueError:
            continue
    return None


def _normalize_infohash(raw: str) -> str:
    raw = raw.strip()
    if _HEX_RE.match(raw):
        return raw.lower()
    if len(raw) == 32:
        try:
            data = base64.b32decode(raw.upper())
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid base32 infohash") from exc
        return binascii.hexlify(data).decode("ascii").lower()
    raise ValueError("Unrecognized infohash format: %r" % raw)
