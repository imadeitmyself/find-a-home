from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from .health import HealthTracker
from .models import SourceConfig


FAST_AGENTS = ("Foxtons", "Savills")
DEFAULT_STALE_AFTER_DAYS = 3
SCHEDULE_GROUPS = ("fast", "standard", "stale")


def select_sources_for_schedule(
    sources: Iterable[SourceConfig],
    health: HealthTracker,
    group: str,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    now: Optional[datetime] = None,
) -> List[SourceConfig]:
    if group not in SCHEDULE_GROUPS:
        raise ValueError("Unknown schedule group: %s" % group)
    if stale_after_days < 1:
        raise ValueError("stale_after_days must be at least 1")

    now = now or datetime.now(timezone.utc)
    last_ok = health.last_ok_index()
    first_checked = health.first_checked_index()

    return [
        source
        for source in sources
        if source.enabled
        and schedule_group_for_source(
            source,
            health,
            last_ok,
            first_checked,
            now,
            stale_after_days,
        )
        == group
    ]


def schedule_group_for_source(
    source: SourceConfig,
    health: HealthTracker,
    last_ok: Dict[str, str],
    first_checked: Dict[Tuple[str, str], str],
    now: datetime,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> str:
    if _is_fast_source(source.name):
        return "fast"

    cutoff = now - timedelta(days=stale_after_days)
    last_success = _latest_timestamp(
        last_ok.get(health.make_key(source.name, url)) for url in source.urls
    )
    if last_success is not None:
        return "stale" if last_success <= cutoff else "standard"

    first_attempt = _earliest_timestamp(
        first_checked.get((source.name, url)) for url in source.urls
    )
    if first_attempt is not None and first_attempt <= cutoff:
        return "stale"
    return "standard"


def _is_fast_source(source_name: str) -> bool:
    name = source_name.strip()
    return any(
        name == agent or name.startswith(agent + " ") or name.startswith(agent + " (")
        for agent in FAST_AGENTS
    )


def _latest_timestamp(values: Iterable[Optional[str]]) -> Optional[datetime]:
    timestamps = [_parse_iso(value) for value in values if value]
    return max(timestamps) if timestamps else None


def _earliest_timestamp(values: Iterable[Optional[str]]) -> Optional[datetime]:
    timestamps = [_parse_iso(value) for value in values if value]
    return min(timestamps) if timestamps else None


def _parse_iso(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)
