"""Command-line entry point for the Step 1 runtime foundation."""

import argparse
import logging
import sys
from pathlib import Path

from app.browser.manager import BrowserManager
from app.core.config import get_settings
from app.core.database import Database
from app.core.health import print_health_report, run_health_checks
from app.core.logger import configure_logging
from app.core.runtime import RuntimeLock, ShutdownCoordinator, ensure_runtime_directories


def command_health() -> int:
    settings = get_settings()
    results = run_health_checks(settings)
    print_health_report(results)
    return 0 if all(result.ok for result in results) else 1


def command_db_test() -> int:
    settings = get_settings()
    database = Database(settings.database_path)
    database.initialize_database()
    source_url = "internal://db-test"
    job = database.find_job("SELF_TEST", source_url)
    if job is None:
        job_id = database.create_job("SELF_TEST", "Database test job", source_url)
    else:
        job_id = int(job["id"])
    database.update_job_status(job_id, "COMPLETED", last_action="db-test passed")
    print(database.get_job(job_id))
    return 0


def command_browser_test() -> int:
    settings = get_settings()
    screenshot = Path("runtime/screenshots/idn-training-test.png")
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    manager = BrowserManager(settings.browser_profile_path, settings.headless)
    try:
        manager.start()
        page = manager.new_page()
        page.goto(settings.idn_training_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_load_state("networkidle", timeout=30_000)
        print(f"Page title: {page.title()}")
        print(f"Final URL: {page.url}")
        page.screenshot(path=str(screenshot), full_page=True)
        print(f"Screenshot: {screenshot}")
        return 0
    except Exception:
        logging.getLogger("browser-test").exception("Browser test failed")
        return 1
    finally:
        manager.stop()


def command_run() -> int:
    settings = get_settings()
    lock = RuntimeLock()
    shutdown = ShutdownCoordinator()
    shutdown.add(lock.release)
    shutdown.install()
    try:
        lock.acquire()
        Database(settings.database_path).initialize_database()
        results = run_health_checks(settings, include_browser=False)
        print_health_report(results)
        print("\nAutonomous pipeline is not enabled in Step 1.")
        return 0 if all(result.ok for result in results) else 1
    finally:
        shutdown.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="IDN KB Agent runtime foundation")
    parser.add_argument("command", choices=("health", "db-test", "browser-test", "run"))
    args = parser.parse_args()
    ensure_runtime_directories()
    configure_logging(get_settings().log_level)
    try:
        return {"health": command_health, "db-test": command_db_test,
                "browser-test": command_browser_test, "run": command_run}[args.command]()
    except KeyboardInterrupt:
        logging.getLogger("main").info("Interrupted by user")
        return 130
    except Exception:
        logging.getLogger("main").exception("Command failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

