from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .agent_directory import AgentDirectoryEntry
from .models import SourceConfig


def write_agent_seed(sources: Iterable[SourceConfig], output_path: str) -> int:
    path = Path(output_path)
    parent = path.parent
    if parent:
        parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for source in sources:
        for url in source.urls:
            rows.append(
                {
                    "name": source.name,
                    "listing_url": url,
                    "enabled": source.enabled,
                    "excluded_keywords": list(source.excluded_keywords),
                }
            )

    lines = [
        "-- Generated from an agent source CSV by find-a-home.",
        "-- Applies an upsert by listing_url; it does not delete rows missing from the CSV.",
        "insert into public.agent_sources (name, listing_url, enabled, excluded_keywords)",
        "values",
    ]

    value_lines = []
    for row in rows:
        value_lines.append(
            "  (%s, %s, %s, %s)"
            % (
                sql_string(row["name"]),
                sql_string(row["listing_url"]),
                "true" if row["enabled"] else "false",
                sql_text_array(row["excluded_keywords"]),
            )
        )

    if value_lines:
        lines.append(",\n".join(value_lines))
        lines.extend(
            [
                "on conflict (listing_url) do update set",
                "  name = excluded.name,",
                "  enabled = excluded.enabled,",
                "  excluded_keywords = excluded.excluded_keywords,",
                "  updated_at = now();",
            ]
        )
    else:
        lines = ["-- No agent sources found in CSV."]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def write_agent_directory_seed(entries: Iterable[AgentDirectoryEntry], output_path: str) -> int:
    path = Path(output_path)
    parent = path.parent
    if parent:
        parent.mkdir(parents=True, exist_ok=True)

    rows = list(entries)
    lines = [
        "-- Generated from the root agent directory CSV by find-a-home.",
        "-- Applies an upsert by owned_website_url; it does not delete rows missing from the CSV.",
        "insert into public.agent_directory (agent_name, owned_website_url, status, evidence_or_note)",
        "values",
    ]

    value_lines = []
    for row in rows:
        value_lines.append(
            "  (%s, %s, %s, %s)"
            % (
                sql_string(row.agent_name),
                sql_string(row.owned_website_url),
                sql_string(row.status),
                sql_string(row.evidence_or_note),
            )
        )

    if value_lines:
        lines.append(",\n".join(value_lines))
        lines.extend(
            [
                "on conflict (owned_website_url) do update set",
                "  agent_name = excluded.agent_name,",
                "  status = excluded.status,",
                "  evidence_or_note = excluded.evidence_or_note,",
                "  updated_at = now();",
            ]
        )
    else:
        lines = ["-- No agent directory rows found in CSV."]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def sql_string(value: str) -> str:
    return "'" + (value or "").replace("'", "''") + "'"


def sql_text_array(values: Iterable[str]) -> str:
    items = [sql_string(value) for value in values if value]
    if not items:
        return "array[]::text[]"
    return "array[%s]::text[]" % ", ".join(items)
