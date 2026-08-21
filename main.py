"""Command-line entry point for the Step 1 runtime foundation."""

import argparse
import logging
import sys
import json
from pathlib import Path

from app.browser.manager import BrowserManager
from app.core.config import get_settings
from app.core.database import Database
from app.core.health import print_health_report, run_health_checks
from app.core.logger import configure_logging
from app.core.runtime import RuntimeLock, ShutdownCoordinator, ensure_runtime_directories
from app.site_model.reconnaissance import run_reconnaissance
from app.extractor.pipeline import generate_summary, manual_validation_samples, run_extraction
from app.extractor.audit import print_parser_audit, run_parser_audit


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


def command_idn_learn(limit: int) -> int:
    settings = get_settings()
    database = Database(settings.database_path)
    database.initialize_database()
    report = run_reconnaissance(settings, database, sample_limit=limit)
    print(f"IDN reconnaissance complete: {report.training_products_found} products, "
          f"{report.sample_landing_pages_analyzed} samples, {report.pages_failed} failures")
    return 0 if report.pages_failed == 0 else 2


def command_idn_report() -> int:
    model_path = Path("data/site_models/idn_site_model.json")
    catalog_path = Path("data/site_models/training_catalog.json")
    if not model_path.exists() or not catalog_path.exists():
        print("No saved IDN site model. Run: python main.py idn-learn", file=sys.stderr)
        return 1
    model = json.loads(model_path.read_text(encoding="utf-8"))
    stats = model.get("statistics", {})
    print("IDN SITE MODEL\n")
    print(f"Categories.............. {stats.get('categories', 0)}")
    print(f"Training Products....... {stats.get('products', 0)}")
    print(f"Unique URLs............. {stats.get('unique_urls', 0)}")
    print(f"Samples Analyzed........ {stats.get('samples_analyzed', 0)}")
    print(f"Supporting Pages........ {stats.get('supporting_pages', 0)}")
    print(f"Warnings................ {len(model.get('warnings', []))}")
    print(f"\nModel:\n{model_path}\n\nCatalog:\n{catalog_path}")
    return 0


def command_idn_extract(limit: int | None, force: bool) -> int:
    settings = get_settings(); database = Database(settings.database_path); database.initialize_database()
    summary = run_extraction(settings, database, limit=limit, force=force)
    print(f"IDN extraction complete: {summary['completed']} completed, {summary['partial']} partial, "
          f"{summary['failed']} failed, {summary['pending']} pending")
    return 2 if summary["failed"] else 0


def command_extraction_report() -> int:
    settings = get_settings(); database = Database(settings.database_path); database.initialize_database()
    summary = generate_summary(database)
    coverage = summary["field_coverage"]
    print("IDN TRAINING EXTRACTION REPORT\n")
    print(f"Products registered...... {summary['total_products']}")
    print(f"Extracted................ {summary['completed']}")
    print(f"Partial.................. {summary['partial']}")
    print(f"Failed................... {summary['failed']}")
    for label, key in (("Description", "description"), ("Curriculum", "curriculum"), ("Price", "price"),
                       ("Duration", "duration"), ("Prerequisite", "prerequisites"), ("Trainer", "trainers")):
        print(f"{label + ' found':27} {coverage.get(key, {}).get('FOUND', 0)}")
    print(f"\nAverage coverage......... {summary['average_coverage']:.1%}")
    print(f"Unknown headings......... {sum(summary['unknown_headings'].values())}")
    print(f"Quality warnings......... {sum(summary['quality_flags'].values())}")
    print("\nDataset:\ndata/products/\n\nSummary:\ndata/site_models/training_extraction_summary.json")
    samples = manual_validation_samples(database)
    if samples:
        print("\nMANUAL EXTRACTION VALIDATION")
        for item in samples:
            facts = item["facts"]
            def shown(key):
                field = facts[key]; return field.get("value") if field.get("value") is not None else field.get("values")
            print(f"\nProduct: {item['product']}\nCategory: {item['category']}\nURL: {item['url']}\n"
                  f"Description: {shown('description')}\nDuration: {shown('duration')}\nPrice: {shown('price')}\n"
                  f"Curriculum: {shown('curriculum')}\nTrainer: {shown('trainers')}\nStatus: REVIEW_REQUIRED")
    return 0


def command_parser_audit() -> int:
    audit = run_parser_audit()
    print_parser_audit(audit)
    return 0 if all(audit["regression"].values()) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="IDN KB Agent runtime foundation")
    parser.add_argument("command", choices=("health", "db-test", "browser-test", "run", "idn-learn", "idn-report",
                                            "idn-extract", "extraction-report", "parser-audit"))
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximum landing pages sampled by idn-learn (catalog remains complete)")
    parser.add_argument("--force", action="store_true", help="Re-extract completed products")
    args = parser.parse_args()
    ensure_runtime_directories()
    configure_logging(get_settings().log_level)
    try:
        commands = {"health": command_health, "db-test": command_db_test,
                    "browser-test": command_browser_test, "run": command_run,
                    "idn-report": command_idn_report, "extraction-report": command_extraction_report,
                    "parser-audit": command_parser_audit}
        if args.command == "idn-learn":
            if args.limit is not None and args.limit < 0:
                parser.error("--limit must be zero or greater")
            return command_idn_learn(10 if args.limit is None else args.limit)
        if args.command == "idn-extract":
            if args.limit is not None and args.limit < 0: parser.error("--limit must be zero or greater")
            return command_idn_extract(args.limit, args.force)
        return commands[args.command]()
    except KeyboardInterrupt:
        logging.getLogger("main").info("Interrupted by user")
        return 130
    except Exception:
        logging.getLogger("main").exception("Command failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
