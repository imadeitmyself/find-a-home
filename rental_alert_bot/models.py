from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Criteria:
    postcode_areas: List[str]
    bedrooms: int
    min_price_pcm: int
    max_price_pcm: int
    allow_unknown_area: bool = False
    exclude_keywords: List[str] = field(default_factory=list)


@dataclass
class SourceConfig:
    name: str
    urls: List[str]
    enabled: bool = True
    excluded_keywords: List[str] = field(default_factory=list)


@dataclass
class AppConfig:
    poll_interval_seconds: int
    request_timeout_seconds: int
    respect_robots_txt: bool
    database_path: str
    user_agent: str
    criteria: Criteria
    sources: List[SourceConfig]
    source_files: List[str] = field(default_factory=list)


@dataclass
class Listing:
    source: str
    external_id: str
    url: str
    title: str
    raw_text: str
    price_pcm: Optional[int] = None
    bedrooms: Optional[int] = None
    postcode_area: Optional[str] = None
    address: str = ""
    available_date: Optional[str] = None
    first_seen_at: str = field(default_factory=utcnow_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MatchResult:
    accepted: bool
    reasons: List[str]
