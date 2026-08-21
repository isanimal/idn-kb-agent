"""Orchestration for Gate 0 IDN reconnaissance."""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.browser.manager import BrowserManager
from app.core.config import Settings
from app.core.database import Database
from app.crawler.http_client import PoliteHttpClient
from app.crawler.page_fetcher import PageFetcher
from app.discovery.idn_discovery import (
    choose_diverse_samples, discover_supporting_pages, parse_training_directory, source_hash,
)
from app.site_model.builder import analyze_page, build_heading_inventory, save_json
from app.site_model.models import PageAnalysis, ReconnaissanceReport


AUTHORITY = {
    "TRAINING_PRODUCT": {"authority": "PRIMARY_PRODUCT_SOURCE"},
    "TRAINING_DIRECTORY": {"authority": "PRIMARY_DISCOVERY_SOURCE"},
    "FAQ": {"authority": "IDN_GLOBAL_INFORMATION"},
    "TRAINER_DIRECTORY": {"authority": "IDN_TRAINER_SOURCE"},
    "SCHEDULE": {"authority": "IDN_SCHEDULE_SOURCE"},
    "LOCATION": {"authority": "IDN_GLOBAL_INFORMATION"},
    "POLICY": {"authority": "IDN_GLOBAL_INFORMATION"},
    "GENERAL": {"authority": "IDN_GENERAL_SOURCE"},
    "UNKNOWN": {"authority": "UNCLASSIFIED_IDN_SOURCE"},
}


def run_reconnaissance(settings: Settings, database: Database, sample_limit: int = 10) -> ReconnaissanceReport:
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    report = ReconnaissanceReport(generated_at=now)
    logger = logging.getLogger("idn.site_model")
    http = PoliteHttpClient(user_agent=settings.crawler_user_agent,
                            timeout=settings.crawl_timeout_seconds,
                            delay=settings.crawl_delay_seconds,
                            max_retries=settings.crawl_max_retries)
    browser = BrowserManager(settings.browser_profile_path, headless=settings.headless)
    fetcher = PageFetcher(http, browser)
    model_dir = Path("data/site_models")

    def record_fetch(method: str) -> None:
        report.pages_attempted += 1
        report.pages_successful += 1
        if method == "HTTP": report.http_fetched += 1
        else: report.browser_fetched += 1

    try:
        try:
            index = fetcher.fetch(
                settings.idn_training_url, Path("data/snapshots/idn/training-index"),
                usable=lambda html: bool(BeautifulSoup(html, "html.parser").select(
                    "article.betterdocs-single-category-wrapper .betterdocs-articles-list a[href]"
                )),
            )
            record_fetch(index.fetch_method)
            catalog = parse_training_directory(index.html, settings.idn_training_url)
            if not catalog.categories or not catalog.statistics["products"]:
                raise RuntimeError("Training index yielded no categories/products")
        except Exception as exc:
            report.pages_attempted += 1
            report.pages_failed += 1
            report.errors.append({"url": settings.idn_training_url, "reason": str(exc)})
            report.duration_seconds = round(time.monotonic() - started, 3)
            save_json(model_dir / "reconnaissance_report.json", report)
            raise RuntimeError(f"Primary training index failed: {exc}") from exc

        report.categories_found = catalog.statistics["categories"]
        report.training_products_found = catalog.statistics["products"]
        report.duplicate_urls_found = catalog.statistics["products"] - catalog.statistics["unique_urls"]
        save_json(model_dir / "training_catalog.json", catalog)

        inserted = 0
        for category in catalog.categories:
            for product in category.products:
                _, created = database.upsert_training_source(
                    name=product.name, category=product.category, source_url=product.source_url,
                    canonical_url=product.canonical_url,
                    discovered_at=product.discovered_at.isoformat(), source_hash=source_hash(product),
                )
                inserted += int(created)
        logger.info("Training registry refreshed: %d products, %d new", catalog.statistics["products"], inserted)

        pages: list[PageAnalysis] = []
        for product in choose_diverse_samples(catalog, max(0, sample_limit)):
            try:
                fetched = fetcher.fetch(product.canonical_url, Path("data/snapshots/idn/samples"))
                record_fetch(fetched.fetch_method)
                pages.append(analyze_page(fetched.html, fetched.final_url, fetched.fetch_method, "TRAINING_PRODUCT"))
            except Exception as exc:
                report.pages_attempted += 1
                report.pages_failed += 1
                report.errors.append({"url": product.canonical_url, "reason": str(exc)})
                logger.warning("Sample failed: %s: %s", product.canonical_url, exc)
        report.sample_landing_pages_analyzed = len(pages)

        supporting = discover_supporting_pages(index.html, settings.idn_training_url)
        report.supporting_pages_found = len(supporting)
        # Snapshot a small authoritative subset; related subdomains are recorded but not crawled.
        fetched_supporting = 0
        for item in supporting:
            if fetched_supporting >= 3 or urlsplit(item["url"]).hostname != "www.idn.id":
                continue
            try:
                fetched = fetcher.fetch(item["url"], Path("data/snapshots/idn/supporting"))
                record_fetch(fetched.fetch_method)
                pages.append(analyze_page(fetched.html, fetched.final_url, fetched.fetch_method, item["page_type"]))
                item["fetch_method"] = fetched.fetch_method
                item["snapshot_path"] = fetched.snapshot_path
                fetched_supporting += 1
            except Exception as exc:
                report.pages_attempted += 1
                report.pages_failed += 1
                report.errors.append({"url": item["url"], "reason": str(exc)})

        heading_inventory = build_heading_inventory(pages)
        page_types: dict[str, int] = {}
        for page in pages:
            page_types[page.page_type] = page_types.get(page.page_type, 0) + 1
        warnings = list(report.warnings)
        if report.pages_failed:
            warnings.append(f"{report.pages_failed} page(s) failed; model is partial")
        site_model = {
            "domain": "www.idn.id", "generated_at": now.isoformat(),
            "entry_points": {"home": settings.idn_base_url, "training": settings.idn_training_url},
            "page_types": page_types,
            "training_directory": {
                "url": settings.idn_training_url, "fetch_method": index.fetch_method,
                "category_container": "article.betterdocs-single-category-wrapper",
                "category_heading": ".betterdocs-category-title",
                "product_links": ".betterdocs-articles-list a[href]",
                "statistics": catalog.statistics,
            },
            "training_landing_page_patterns": {
                "samples": [page.model_dump(mode="json") for page in pages if page.page_type == "TRAINING_PRODUCT"],
                "feature_frequency": {
                    key: sum(page.patterns.get(key, False) for page in pages if page.page_type == "TRAINING_PRODUCT")
                    for key in ("pricing", "duration", "curriculum", "trainer", "facility", "benefit",
                                "prerequisite", "certification", "cta")
                },
            },
            "heading_inventory": [item.model_dump(mode="json") for item in heading_inventory],
            "supporting_pages": supporting, "source_authority": AUTHORITY,
            "crawl_policy": {
                "strategy": "HTTP_FIRST_BROWSER_FALLBACK", "robots_respected": True,
                "timeout_seconds": settings.crawl_timeout_seconds,
                "max_retries": settings.crawl_max_retries, "delay_seconds": settings.crawl_delay_seconds,
                "max_concurrency": settings.crawl_concurrency,
                "scope": ["*.idn.id"], "external_links_followed": False,
            },
            "statistics": {
                **catalog.statistics, "samples_analyzed": report.sample_landing_pages_analyzed,
                "supporting_pages": len(supporting), "headings": len(heading_inventory),
            },
            "warnings": warnings,
        }
        save_json(model_dir / "idn_site_model.json", site_model)
        report.warnings = warnings
        report.duration_seconds = round(time.monotonic() - started, 3)
        save_json(model_dir / "reconnaissance_report.json", report)
        return report
    finally:
        browser.stop()
        http.close()

