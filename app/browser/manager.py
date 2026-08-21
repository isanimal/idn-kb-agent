"""Persistent Playwright Chromium context manager."""

import logging
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright


class BrowserManager:
    def __init__(self, profile_path: Path, headless: bool = False) -> None:
        self.profile_path = profile_path
        self.headless = headless
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self.logger = logging.getLogger("browser")

    def start(self) -> None:
        if self.is_alive():
            return
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_path.resolve()), headless=self.headless
            )
            self.logger.info("Chromium started with persistent profile")
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise

    def stop(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self.logger.info("Browser stopped")

    def restart(self) -> None:
        self.stop()
        self.start()

    def new_page(self) -> Page:
        if not self.is_alive() or self._context is None:
            raise RuntimeError("Browser is not running")
        return self._context.new_page()

    def is_alive(self) -> bool:
        return self._context is not None and bool(self._context.pages or self._context.browser)

    def __enter__(self) -> "BrowserManager":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

