from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class AgentDirectoryEntry:
    agent_name: str
    owned_website_url: str
    status: str = ""
    evidence_or_note: str = ""


def load_agent_directory(path: str) -> List[AgentDirectoryEntry]:
    csv_path = Path(path)
    if not csv_path.exists():
        return []

    entries: List[AgentDirectoryEntry] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            agent_name = (row.get("agent_name") or row.get("name") or "").strip()
            owned_website_url = (row.get("owned_website_url") or row.get("website_url") or row.get("url") or "").strip()
            if not agent_name or not owned_website_url:
                continue
            entries.append(
                AgentDirectoryEntry(
                    agent_name=agent_name,
                    owned_website_url=owned_website_url,
                    status=(row.get("status") or "").strip(),
                    evidence_or_note=(row.get("evidence_or_note") or row.get("notes") or "").strip(),
                )
            )
    return entries
