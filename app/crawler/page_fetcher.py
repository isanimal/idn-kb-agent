"""HTTP-first page acquisition with a persistent-browser fallback."""

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.browser.manager import BrowserManager
from app.crawler.http_client import PoliteHttpClient


@dataclass
class FetchResult:
    url: str
    final_url: str
    html: str
    fetch_method: str
    snapshot_path: str


def safe_snapshot_name(url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[-80:] or "page"
    return f"{slug}-{hashlib.sha256(url.encode()).hexdigest()[:10]}.html"


class PageFetcher:
    def __init__(self, http: PoliteHttpClient, browser: BrowserManager) -> None:
        self.http = http
        self.browser = browser
        self.logger = logging.getLogger("idn.crawler")

    def fetch(self, url: str, snapshot_dir: Path,
              usable: Callable[[str], bool] = lambda html: len(html) > 500) -> FetchResult:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        try:
            response = self.http.get(url)
            if not usable(response.text):
                raise ValueError("HTTP DOM did not contain required content")
            result = FetchResult(url, str(response.url), response.text, "HTTP", "")
        except Exception as exc:
            self.logger.warning("HTTP fetch failed/unusable for %s, using browser fallback: %s", url, exc)
            self.browser.start()
            page = self.browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    self.logger.info("Browser network remained active for %s; validating current DOM", url)
                result = FetchResult(url, page.url, page.content(), "PLAYWRIGHT", "")
            finally:
                page.close()
            if not usable(result.html):
                raise RuntimeError(f"Rendered DOM is unusable for {url}")
        path = snapshot_dir / safe_snapshot_name(result.final_url)
        path.write_text(result.html, encoding="utf-8")
        result.snapshot_path = str(path)
        return result
