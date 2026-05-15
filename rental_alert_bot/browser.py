from __future__ import annotations

import os
import random
import time
from types import TracebackType
from typing import Optional, Type


_JITTER_MIN = 2.0
_JITTER_MAX = 8.0


def _proxy_config() -> Optional[dict]:
    """
    Build a Playwright proxy dict from environment variables.
    Reads BROWSER_PROXY (full URL) or WEBSHARE_* credentials.
    Returns None if no proxy is configured.
    """
    direct = os.environ.get("BROWSER_PROXY", "").strip()
    if direct:
        return {"server": direct}

    host = os.environ.get("WEBSHARE_PROXY_HOST", "").strip()
    port = os.environ.get("WEBSHARE_PROXY_PORT", "80").strip()
    user = os.environ.get("WEBSHARE_PROXY_USER", "").strip()
    password = os.environ.get("WEBSHARE_PROXY_PASS", "").strip()
    if host and user and password:
        return {"server": "http://%s:%s" % (host, port), "username": user, "password": password}

    return None


class BrowserFetcher:
    """
    Context manager that owns one Camoufox browser instance for the lifetime of a run.
    All URLs are fetched sequentially with random jitter to avoid bot fingerprinting.

    use_proxy=True: routes through Webshare (Tier 1 agents — major chains with strong bot detection).
    use_proxy=False: headless only, no proxy (Tier 2 agents).
    """

    def __init__(self, timeout_seconds: int = 30, use_proxy: bool = False) -> None:
        self.timeout_seconds = timeout_seconds
        self._proxy = _proxy_config() if use_proxy else None
        self._browser = None
        self._cm = None

    def __enter__(self) -> "BrowserFetcher":
        try:
            from camoufox.sync_api import Camoufox  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "camoufox is not installed. On the VPS run: "
                "pip install camoufox && python -m camoufox fetch"
            ) from exc

        kwargs: dict = {"headless": True, "geoip": True}
        if self._proxy:
            kwargs["proxy"] = self._proxy

        self._cm = Camoufox(**kwargs)
        self._browser = self._cm.__enter__()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self._cm is not None:
            self._cm.__exit__(exc_type, exc_val, exc_tb)
            self._cm = None
            self._browser = None

    def fetch(self, url: str, jitter: bool = True) -> str:
        if self._browser is None:
            raise RuntimeError("BrowserFetcher must be used as a context manager.")

        if jitter:
            time.sleep(random.uniform(_JITTER_MIN, _JITTER_MAX))

        ms = self.timeout_seconds * 1000
        page = self._browser.new_page()
        try:
            page.goto(url, timeout=ms, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass  # networkidle timeout is acceptable — content is already loaded
            return page.content()
        finally:
            page.close()
