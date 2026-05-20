from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from .health import HealthTracker
from .models import SourceConfig


EMPTY_RUN_THRESHOLD = 3  # consecutive empty polls in window to flag as "empty but historically OK"


@dataclass
class _UrlStats:
    source_name: str
    url: str
    tier: int
    ok: int = 0
    empty: int = 0
    fail: int = 0
    last_error: str = ""


def _classify(stats: _UrlStats, historic_max: int) -> str:
    """Returns 'working', 'failing', or 'empty_historic'."""
    if stats.fail > 0 and stats.fail >= stats.ok + stats.empty:
        return "failing"
    if stats.ok > 0:
        return "working"
    if stats.empty >= EMPTY_RUN_THRESHOLD and historic_max > 0:
        return "empty_historic"
    if stats.fail > 0:
        return "failing"
    return "working"


def _age_phrase(now: datetime, iso: Optional[str]) -> str:
    if not iso:
        return "never"
    try:
        ts = datetime.fromisoformat(iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return iso[:19]
    delta = now - ts
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return "%dm ago" % (seconds // 60)
    if seconds < 86400:
        return "%dh ago" % (seconds // 3600)
    return "%dd ago" % (seconds // 86400)


def _gather(
    health: HealthTracker, start_iso: str, end_iso: str, tier_by_url: Dict[str, int]
) -> Dict[Tuple[str, str], _UrlStats]:
    counts, errors = health.get_window_summary(start_iso, end_iso)
    stats: Dict[Tuple[str, str], _UrlStats] = {}
    for row in counts:
        key = (row["source_name"], row["url"])
        s = stats.setdefault(
            key,
            _UrlStats(
                source_name=row["source_name"],
                url=row["url"],
                tier=tier_by_url.get(row["url"], 0),
            ),
        )
        outcome = row["outcome"]
        count = int(row["count"])
        if outcome == "ok":
            s.ok += count
        elif outcome == "empty":
            s.empty += count
        elif outcome == "skip":
            continue
        else:
            s.fail += count
    for (name, url), msg in errors.items():
        if (name, url) in stats:
            stats[(name, url)].last_error = msg
    return stats


def _agency_label(source_name: str) -> str:
    """Strip the trailing "(E9)" / "(E9/E2)" tag to get the agency name."""
    idx = source_name.rfind(" (")
    return source_name[:idx] if idx > 0 and source_name.endswith(")") else source_name


def _area_tag(source_name: str) -> str:
    idx = source_name.rfind(" (")
    if idx > 0 and source_name.endswith(")"):
        return source_name[idx + 2 : -1]
    return ""


def build_daily_report(
    health: HealthTracker,
    sources: List[SourceConfig],
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    now = now or datetime.now(timezone.utc)
    end_today = now.isoformat()
    start_today = (now - timedelta(hours=24)).isoformat()
    start_prev = (now - timedelta(hours=48)).isoformat()

    tier_by_url: Dict[str, int] = {}
    for src in sources:
        for url in src.urls:
            tier_by_url[url] = src.tier

    today = _gather(health, start_today, end_today, tier_by_url)
    prev = _gather(health, start_prev, start_today, tier_by_url)
    historic_max = health.historic_max_index()
    last_ok = health.last_ok_index()
    earliest_log = health.log_earliest()

    classified_today: Dict[str, List[_UrlStats]] = {"working": [], "failing": [], "empty_historic": []}
    for key, stats in today.items():
        hm = historic_max.get(health.make_key(*key), 0)
        classified_today[_classify(stats, hm)].append(stats)

    classified_prev: Dict[Tuple[str, str], str] = {}
    for key, stats in prev.items():
        hm = historic_max.get(health.make_key(*key), 0)
        classified_prev[key] = _classify(stats, hm)

    regressed: List[_UrlStats] = []
    recovered: List[_UrlStats] = []
    for stats in classified_today["failing"]:
        key = (stats.source_name, stats.url)
        if classified_prev.get(key, "working") == "working" and key in prev:
            regressed.append(stats)
    for key, prev_cls in classified_prev.items():
        if prev_cls == "failing" and key in today:
            today_stats = today[key]
            hm = historic_max.get(health.make_key(*key), 0)
            if _classify(today_stats, hm) == "working":
                recovered.append(today_stats)

    lines: List[str] = []
    title_time = now.strftime("%Y-%m-%d %H:%M UTC")
    lines.append("find-a-home daily report — %s" % title_time)
    lines.append(
        "Window: last 24h (%s → %s)" % (start_today[:16].replace("T", " "), end_today[:16].replace("T", " "))
    )
    if earliest_log and earliest_log > start_today:
        lines.append("Note: log only goes back to %s — report covers a partial window." % earliest_log[:16].replace("T", " "))
    lines.append("")

    n_working = len(classified_today["working"])
    n_failing = len(classified_today["failing"])
    n_empty = len(classified_today["empty_historic"])
    subject = "find-a-home: %d OK · %d failing · %d new · %d recovered" % (
        n_working, n_failing, len(regressed), len(recovered),
    )
    if n_empty:
        subject += " · %d empty" % n_empty

    if regressed or recovered:
        lines.append("CHANGED SINCE PRIOR 24H")
        for s in regressed:
            lines.append(
                "  + %s [T%d]: regressed — %d fails%s"
                % (s.source_name, s.tier, s.fail, (" — %s" % s.last_error[:80]) if s.last_error else "")
            )
        for s in recovered:
            lines.append("  - %s [T%d]: recovered" % (s.source_name, s.tier))
        lines.append("")

    if classified_today["failing"]:
        lines.append("FAILING (%d urls)" % n_failing)
        by_agency: Dict[str, List[_UrlStats]] = defaultdict(list)
        for s in sorted(classified_today["failing"], key=lambda x: (-x.fail, x.source_name)):
            by_agency[_agency_label(s.source_name)].append(s)
        for agency in sorted(by_agency.keys()):
            lines.append("  %s" % agency)
            for s in by_agency[agency]:
                area = _area_tag(s.source_name) or "—"
                key = health.make_key(s.source_name, s.url)
                last_ok_phrase = _age_phrase(now, last_ok.get(key))
                err = s.last_error[:80] if s.last_error else ""
                lines.append(
                    "    %-8s [T%d]  %3d fails  last OK %-9s  %s"
                    % (area, s.tier, s.fail, last_ok_phrase, err)
                )
                lines.append("        %s" % s.url)
        lines.append("")

    if classified_today["empty_historic"]:
        lines.append("EMPTY BUT HISTORICALLY OK (%d urls)" % n_empty)
        for s in sorted(classified_today["empty_historic"], key=lambda x: x.source_name):
            key = health.make_key(s.source_name, s.url)
            last_ok_phrase = _age_phrase(now, last_ok.get(key))
            lines.append(
                "  %s [T%d]: %d empty polls in 24h, last OK %s"
                % (s.source_name, s.tier, s.empty, last_ok_phrase)
            )
            lines.append("    %s" % s.url)
        lines.append("")

    lines.append("WORKING (%d urls)" % n_working)
    if classified_today["working"]:
        by_agency = defaultdict(list)
        for s in classified_today["working"]:
            by_agency[_agency_label(s.source_name)].append(s)
        for agency in sorted(by_agency.keys()):
            urls_for_agency = by_agency[agency]
            urls_for_agency.sort(key=lambda x: _area_tag(x.source_name))
            for s in urls_for_agency:
                area = _area_tag(s.source_name) or "—"
                lines.append(
                    "  %-30s %-8s [T%d]  %3d ok, %d empty"
                    % (agency[:30], area, s.tier, s.ok, s.empty)
                )

    lines.append("")
    return subject, "\n".join(lines)
