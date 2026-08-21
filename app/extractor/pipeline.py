"""Restart-safe Step 3 extraction orchestration and offline reporting."""

import json
import logging
import random
import shutil
from types import SimpleNamespace
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.browser.manager import BrowserManager
from app.core.config import Settings
from app.core.database import Database
from app.crawler.http_client import PoliteHttpClient
from app.crawler.page_fetcher import PageFetcher
from app.extractor.parser import evidence_document, parse_training_page, relevant_content_hash
from app.site_model.builder import save_json


FIELDS = ("description", "duration", "price", "training_format", "curriculum", "benefits", "facilities",
          "prerequisites", "target_audiences", "certifications", "trainers", "tools", "practice")


def product_slug(url: str) -> str:
    return urlsplit(url).path.rstrip("/").split("/")[-1]


def _usable(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    betterdocs = soup.select_one(".betterdocs-content") and soup.select_one(
        "h1.betterdocs-entry-title, .docs-single-title h1, h1.docs-single-title")
    elementor = soup.select_one(".elementor") and len(soup.select("h2, h3")) >= 3
    return bool(betterdocs or elementor)


def _write_product(source: dict, fetched, facts, root: Path) -> dict:
    folder = root / product_slug(source["canonical_url"])
    folder.mkdir(parents=True, exist_ok=True)
    raw_path = folder / "raw.html"
    raw_path.write_text(fetched.html, encoding="utf-8")
    facts_path, evidence_path, extraction_path = folder / "facts.json", folder / "evidence.json", folder / "extraction.json"
    save_json(facts_path, facts)
    save_json(evidence_path, evidence_document(facts))
    status = "COMPLETED" if facts.identity["full_name"].status.value == "FOUND" else "PARTIAL"
    metadata = {"training_source_id": source["id"], "canonical_url": source["canonical_url"], "status": status,
                "fetch_method": fetched.fetch_method, "http_status": None, "content_hash": facts.metadata["content_hash"],
                "template_type": facts.metadata["template_type"], "extracted_at": datetime.now(timezone.utc).isoformat(),
                "facts_path": str(facts_path), "evidence_path": str(evidence_path), "raw_snapshot_path": str(raw_path),
                "last_error": None}
    save_json(extraction_path, metadata)
    return metadata


def generate_summary(database: Database, root: Path = Path("data/products")) -> dict:
    sources = database.list_training_sources(); records = database.list_training_extractions()
    status = Counter(r["status"] for r in records); methods = Counter(r["fetch_method"] for r in records if r["fetch_method"])
    coverage = {field: Counter() for field in FIELDS}; templates = Counter(); flags = Counter(); headings = Counter()
    coverage_values = []
    for record in records:
        path = Path(record["facts_path"] or "")
        if not path.is_file(): continue
        facts = json.loads(path.read_text(encoding="utf-8")); templates[record["template_type"] or "UNKNOWN"] += 1
        coverage_values.append(facts.get("completeness", {}).get("coverage", 0))
        for field in FIELDS: coverage[field][facts.get(field, {}).get("status", "NOT_FOUND")] += 1
        flags.update(facts.get("quality_flags", []))
        headings.update(s["heading"] for s in facts.get("unknown_sections", []))
    failed = [r["canonical_url"] for r in records if r["status"] == "FAILED"]
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "total_products": len(sources),
        "completed": status["COMPLETED"], "partial": status["PARTIAL"], "failed": status["FAILED"],
        "pending": len(sources) - len(records), "fetch_methods": dict(methods),
        "field_coverage": {k: dict(v) for k, v in coverage.items()}, "template_types": dict(templates),
        "unknown_headings": dict(headings), "quality_flags": dict(flags), "failed_urls": failed,
        "average_coverage": round(sum(coverage_values) / len(coverage_values), 4) if coverage_values else 0}
    save_json(Path("data/site_models/training_extraction_summary.json"), summary)
    return summary


def extract_global_facts() -> dict:
    model_path = Path("data/site_models/idn_site_model.json")
    model = json.loads(model_path.read_text(encoding="utf-8")) if model_path.exists() else {}
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "faq": [], "trainers": [],
              "global_training_information": [], "source_urls": []}
    for page in model.get("supporting_pages", []):
        path = Path(page.get("snapshot_path", ""))
        if not path.is_file(): continue
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser"); url = page["url"]
        result["source_urls"].append(url)
        if page.get("page_type") == "FAQ":
            for question_node in soup.select(".elementor-tab-title"):
                question = " ".join(question_node.get_text(" ", strip=True).split())
                content = question_node.find_next_sibling()
                answer = " ".join(content.get_text(" ", strip=True).split()) if content else ""
                if question and answer: result["faq"].append({"question": question, "answer": answer, "source_url": url})
            for heading in soup.find_all(["h2", "h3", "h4"]):
                question = " ".join(heading.get_text(" ", strip=True).split())
                sibling = heading.find_next_sibling()
                answer = " ".join(sibling.get_text(" ", strip=True).split()) if sibling else ""
                if question and answer: result["faq"].append({"question": question, "answer": answer, "source_url": url})
        elif page.get("page_type") == "TRAINER_DIRECTORY":
            for heading in soup.select("h2, h3"):
                name = " ".join(heading.get_text(" ", strip=True).split())
                if name and name.lower() not in {"our trainer", "trainer"}:
                    result["trainers"].append({"name": name, "source_url": url, "source_text": name})
        else:
            text = " ".join((soup.select_one("main") or soup.body or soup).get_text(" ", strip=True).split())
            if text: result["global_training_information"].append({"source_url": url, "source_text": text})
    save_json(Path("data/site_models/idn_global_facts.json"), result)
    return result


def run_extraction(settings: Settings, database: Database, limit: int | None = None, force: bool = False) -> dict:
    logger = logging.getLogger("idn.extractor"); sources = database.list_training_sources()
    # Resume unfinished work first; completed records are still fetched afterward so
    # their relevant-content hash can detect official page changes.
    sources.sort(key=lambda s: ((database.get_training_extraction(s["id"]) or {}).get("status") in {"COMPLETED", "PARTIAL"}, s["id"]))
    http = PoliteHttpClient(user_agent=settings.crawler_user_agent, timeout=settings.crawl_timeout_seconds,
                            delay=settings.crawl_delay_seconds, max_retries=settings.crawl_max_retries)
    browser = BrowserManager(settings.browser_profile_path, settings.headless); fetcher = PageFetcher(http, browser)
    processed = 0; consecutive_parser_failures = 0
    try:
        for source in sources:
            if limit is not None and processed >= limit: break
            existing = database.get_training_extraction(source["id"])
            processed += 1
            database.upsert_training_extraction(source["id"], source["canonical_url"], "FETCHING")
            try:
                fetched = fetcher.fetch(source["canonical_url"], Path("data/snapshots/idn/extraction"), usable=_usable)
                new_hash = relevant_content_hash(fetched.html)
                if existing and existing.get("content_hash") == new_hash and not force and Path(existing.get("facts_path") or "").exists():
                    database.upsert_training_extraction(source["id"], source["canonical_url"], existing["status"],
                                                        fetch_method=fetched.fetch_method, content_hash=new_hash)
                    continue
                database.upsert_training_extraction(source["id"], source["canonical_url"], "EXTRACTING",
                                                    fetch_method=fetched.fetch_method, content_hash=new_hash)
                facts = parse_training_page(fetched.html, source["canonical_url"], source["category"], source["name"])
                metadata = _write_product(source, fetched, facts, Path("data/products"))
                database.upsert_training_extraction(source["id"], source["canonical_url"], metadata.pop("status"), **{
                    k: v for k, v in metadata.items() if k not in {"training_source_id", "canonical_url"}})
                consecutive_parser_failures = 0
                logger.info("Extracted %s", source["canonical_url"])
            except Exception as exc:
                consecutive_parser_failures += 1; retry = (existing or {}).get("retry_count", 0) + 1
                database.upsert_training_extraction(source["id"], source["canonical_url"], "FAILED",
                                                    retry_count=retry, last_error=str(exc))
                logger.exception("Extraction failed for %s", source["canonical_url"])
                if consecutive_parser_failures >= 10: raise RuntimeError("SYSTEMIC_PARSER_FAILURE") from exc
    finally:
        browser.stop(); http.close()
    extract_global_facts()
    return generate_summary(database)


def reparse_saved_products(database: Database, limit: int | None = None) -> dict:
    """Regenerate facts/evidence from saved official HTML without network access."""
    processed = 0
    for source in database.list_training_sources():
        if limit is not None and processed >= limit: break
        raw_path = Path("data/products") / product_slug(source["canonical_url"]) / "raw.html"
        if not raw_path.is_file(): continue
        html = raw_path.read_text(encoding="utf-8")
        facts = parse_training_page(html, source["canonical_url"], source["category"], source["name"])
        metadata = _write_product(source, SimpleNamespace(html=html, fetch_method="HTTP"), facts, Path("data/products"))
        database.upsert_training_extraction(source["id"], source["canonical_url"], metadata.pop("status"), **{
            k: v for k, v in metadata.items() if k not in {"training_source_id", "canonical_url"}})
        processed += 1
    extract_global_facts()
    return generate_summary(database)


def manual_validation_samples(database: Database, count: int = 5) -> list[dict]:
    candidates = []
    for source in database.list_training_sources():
        record = database.get_training_extraction(source["id"])
        if record and record["status"] in {"COMPLETED", "PARTIAL"} and Path(record["facts_path"]).exists():
            candidates.append((source, record))
    by_category = defaultdict(list)
    for item in candidates: by_category[item[0]["category"]].append(item)
    rng = random.Random(42); chosen = [rng.choice(by_category[k]) for k in sorted(by_category)]
    rng.shuffle(chosen); chosen = chosen[:count]
    output = []
    for source, record in chosen:
        facts = json.loads(Path(record["facts_path"]).read_text(encoding="utf-8"))
        output.append({"product": facts["identity"]["full_name"].get("value"), "category": source["category"],
                       "url": source["canonical_url"], "status": "REVIEW_REQUIRED", "facts": facts})
    return output
