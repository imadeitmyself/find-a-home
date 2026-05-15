from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Dict, List, Set, Tuple
from urllib.parse import urlsplit, urlunsplit

from .models import AppConfig, Criteria, SourceConfig
from .tiers import tier_for_agent


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)

    criteria_raw = raw["criteria"]
    criteria = Criteria(
        postcode_areas=[area.upper() for area in criteria_raw["postcode_areas"]],
        bedrooms=int(criteria_raw["bedrooms"]),
        min_price_pcm=int(criteria_raw["min_price_pcm"]),
        max_price_pcm=int(criteria_raw["max_price_pcm"]),
        allow_unknown_area=bool(criteria_raw.get("allow_unknown_area", False)),
        exclude_keywords=list(criteria_raw.get("exclude_keywords", [])),
    )

    sources = [
        SourceConfig(
            name=str(item["name"]),
            urls=list(item["urls"]),
            enabled=bool(item.get("enabled", True)),
            excluded_keywords=list(item.get("excluded_keywords", [])),
            tier=int(item.get("tier", tier_for_agent(str(item["name"])))),
        )
        for item in raw.get("sources", [])
    ]

    return AppConfig(
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 90)),
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 15)),
        respect_robots_txt=bool(raw.get("respect_robots_txt", True)),
        database_path=str(raw.get("database_path", "data/listings.sqlite3")),
        user_agent=str(raw.get("user_agent", "find-a-home/0.1")),
        criteria=criteria,
        sources=sources,
        source_files=list(raw.get("source_files", [])),
    )


def load_source_file(path: str) -> List[SourceConfig]:
    csv_path = Path(path)
    if not csv_path.exists():
        return []

    grouped: Dict[str, Dict[str, object]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for line_number, row in enumerate(reader, start=2):
            if not row:
                continue
            enabled = _csv_bool(row.get("enabled", "true"))
            name = (row.get("name") or row.get("agent") or "").strip()
            postcode_area = (row.get("postcode_area") or "").strip().upper()
            listing_url = (row.get("listing_url") or row.get("url") or "").strip()
            excluded_keywords = _csv_list(row.get("excluded_keywords", ""))
            raw_tier = row.get("tier", "").strip()
            tier = int(raw_tier) if raw_tier.isdigit() else tier_for_agent(name)

            if not name and not listing_url:
                continue
            if not name:
                name = "Agent source line %s" % line_number
            if postcode_area:
                name = "%s %s" % (name, postcode_area)
            if not listing_url:
                print("SKIP %s line %s: missing listing_url" % (path, line_number), flush=True)
                continue

            key = normalize_source_url(listing_url)
            item = grouped.setdefault(
                key,
                {
                    "url": listing_url,
                    "enabled": False,
                    "names": set(),
                    "areas": set(),
                    "excluded_keywords": set(),
                    "tier": tier,
                },
            )
            item["enabled"] = bool(item["enabled"] or enabled)
            # Keep the lowest (most aggressive) tier seen for this URL
            if tier < int(item["tier"]):
                item["tier"] = tier
            cast_set(item["names"]).add((row.get("name") or row.get("agent") or name).strip() or name)
            if postcode_area:
                cast_set(item["areas"]).add(postcode_area)
            cast_set(item["excluded_keywords"]).update(excluded_keywords)

    sources: List[SourceConfig] = []
    for item in grouped.values():
        names = sorted(cast_set(item["names"]))
        areas = sorted(cast_set(item["areas"]))
        label = ", ".join(names[:2])
        if len(names) > 2:
            label += " +%s" % (len(names) - 2)
        if areas:
            label += " (%s)" % "/".join(areas)
        sources.append(
            SourceConfig(
                name=label,
                urls=[str(item["url"])],
                enabled=bool(item["enabled"]),
                excluded_keywords=sorted(cast_set(item["excluded_keywords"])),
                tier=int(item["tier"]),
            )
        )
    return sources


def load_live_sources(config: AppConfig) -> List[SourceConfig]:
    sources = list(config.sources)
    for path in config.source_files:
        sources.extend(load_source_file(path))
    return _dedupe_sources(sources)


def env_bool(env: Dict[str, str], key: str, default: bool = False) -> bool:
    value = env.get(key)
    if value is None or value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _csv_bool(value: str) -> bool:
    return (value or "").strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _csv_list(value: str) -> List[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def _dedupe_sources(sources: List[SourceConfig]) -> List[SourceConfig]:
    deduped: Dict[tuple, SourceConfig] = {}
    for source in sources:
        for url in source.urls:
            key = (normalize_source_url(url),)
            if key not in deduped:
                deduped[key] = SourceConfig(
                    name=source.name,
                    urls=[url],
                    enabled=source.enabled,
                    excluded_keywords=list(source.excluded_keywords),
                    tier=source.tier,
                )
    return list(deduped.values())


def normalize_source_url(url: str) -> str:
    parts = urlsplit((url or "").strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def cast_set(value: object) -> Set[str]:
    return value if isinstance(value, set) else set()
