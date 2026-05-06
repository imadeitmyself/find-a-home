from __future__ import annotations

import urllib.error
import urllib.request
import urllib.robotparser
from typing import Dict
from urllib.parse import urljoin, urlparse


class RobotsCache:
    def __init__(self, user_agent: str, timeout_seconds: int = 10) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self._cache: Dict[str, urllib.robotparser.RobotFileParser] = {}

    def can_fetch(self, url: str) -> bool:
        parsed = urlparse(url)
        base = "%s://%s" % (parsed.scheme, parsed.netloc)
        robots = self._cache.get(base)
        if robots is None:
            robots = urllib.robotparser.RobotFileParser()
            robots.set_url(urljoin(base, "/robots.txt"))
            try:
                robots.read()
            except Exception:
                return True
            self._cache[base] = robots
        return robots.can_fetch(self.user_agent, url)


def fetch_text(url: str, user_agent: str, timeout_seconds: int) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("HTTP %s fetching %s" % (exc.code, url)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Network error fetching %s: %s" % (url, exc.reason)) from exc
