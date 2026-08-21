"""Defense-in-depth network and UI guards for read-only KB inspection."""

import logging
from urllib.parse import urlsplit

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MUTATING_LABELS = {"simpan", "save", "submit", "update", "delete", "hapus", "publish", "create", "buat"}


class ReadOnlyViolation(RuntimeError):
    pass


def should_block_request(method: str, url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == "kb.idn.id" and method.upper() in MUTATING_METHODS


class ReadOnlyGuard:
    def __init__(self) -> None:
        self.blocked: list[dict[str, str]] = []
        self.logger = logging.getLogger("kb.read_only")

    def install(self, context) -> None:
        def handler(route, request) -> None:
            if should_block_request(request.method, request.url):
                item = {"method": request.method, "url": request.url}
                self.blocked.append(item)
                self.logger.warning("READ_ONLY_GUARD blocked %s %s", request.method, request.url)
                route.abort("blockedbyclient")
            else:
                route.continue_()
        context.route("**/*", handler)

    def assert_safe_ui_action(self, label: str) -> None:
        normalized = " ".join(label.lower().split())
        if normalized in MUTATING_LABELS or any(normalized.startswith(x + " ") for x in MUTATING_LABELS):
            raise ReadOnlyViolation(f"Blocked mutation-looking UI action: {label}")
