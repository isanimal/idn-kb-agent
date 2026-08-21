"""Polite HTTP client with bounded retries and backoff."""

import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx


class PoliteHttpClient:
    def __init__(self, *, user_agent: str, timeout: float = 30, delay: float = 0.75,
                 max_retries: int = 3) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.delay = delay
        self.max_retries = max_retries
        self.client = httpx.Client(headers={"User-Agent": user_agent}, follow_redirects=True, timeout=timeout)
        self.logger = logging.getLogger("idn.crawler")
        self._last_request = 0.0
        self._robots: dict[str, RobotFileParser] = {}

    def _wait(self) -> None:
        remaining = self.delay - (time.monotonic() - self._last_request)
        if remaining > 0:
            time.sleep(remaining)

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser(f"{origin}/robots.txt")
            try:
                self._wait()
                response = self.client.get(parser.url)
                self._last_request = time.monotonic()
                parser.parse(response.text.splitlines() if response.status_code < 400 else [])
            except httpx.HTTPError:
                parser.parse([])
            self._robots[origin] = parser
        return self._robots[origin].can_fetch(self.user_agent, url)

    def get(self, url: str) -> httpx.Response:
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                self._wait()
                response = self.client.get(url)
                self._last_request = time.monotonic()
                if response.status_code in {403, 429} or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code}", request=response.request, response=response
                    )
                response.raise_for_status()
                self.logger.info("HTTP fetched %s", response.url)
                return response
            except (httpx.HTTPError, PermissionError) as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"HTTP fetch failed after {self.max_retries} attempts: {last_error}")

    def close(self) -> None:
        self.client.close()

