"""Component health checks."""

import platform
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.browser.manager import BrowserManager
from app.core.config import Settings
from app.core.database import Database
from app.core.runtime import ensure_runtime_directories


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def run_health_checks(settings: Settings, include_browser: bool = True) -> list[CheckResult]:
    results = [CheckResult("Python", sys.version_info >= (3, 11), platform.python_version())]
    try:
        Database(settings.database_path).initialize_database()
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("SELECT 1").fetchone()
        results.append(CheckResult("Database", True, str(settings.database_path)))
    except Exception as exc:
        results.append(CheckResult("Database", False, str(exc)))

    try:
        ensure_runtime_directories()
        required = [Path("data"), Path("runtime"), Path("logs")]
        results.append(CheckResult("Directories", all(path.is_dir() for path in required)))
    except Exception as exc:
        results.append(CheckResult("Directories", False, str(exc)))

    try:
        import playwright  # noqa: F401
        results.append(CheckResult("Playwright", True))
    except Exception as exc:
        results.append(CheckResult("Playwright", False, str(exc)))

    if include_browser:
        manager = BrowserManager(settings.browser_profile_path, headless=True)
        try:
            manager.start()
            results.append(CheckResult("Browser", manager.is_alive()))
        except Exception as exc:
            results.append(CheckResult("Browser", False, str(exc)))
        finally:
            manager.stop()

    try:
        response = httpx.get(settings.idn_base_url, timeout=15, follow_redirects=True)
        results.append(CheckResult("idn.id", response.status_code < 500, f"HTTP {response.status_code}"))
    except Exception as exc:
        results.append(CheckResult("idn.id", False, str(exc)))
    return results


def print_health_report(results: list[CheckResult]) -> None:
    print("IDN KB AGENT HEALTH CHECK\n")
    for result in results:
        status = "OK" if result.ok else "FAIL"
        suffix = f" ({result.detail})" if result.detail else ""
        print(f"{result.name:.<20} {status}{suffix}")
    print(f"\nRESULT: {'HEALTHY' if all(item.ok for item in results) else 'DEGRADED'}")

