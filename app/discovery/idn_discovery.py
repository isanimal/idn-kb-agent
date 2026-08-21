"""Deterministic DOM discovery for the IDN training ecosystem."""

import hashlib
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from app.discovery.models import TrainingCatalog, TrainingCategory, TrainingProduct


def canonicalize_url(url: str, base_url: str = "https://www.idn.id/") -> str:
    absolute = urljoin(base_url, url.strip())
    parts = urlsplit(absolute)
    scheme = "https" if parts.hostname and parts.hostname.endswith("idn.id") else parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        path += "/"
    return urlunsplit((scheme, host + port, path, parts.query, ""))


def parse_training_directory(html: str, source_url: str) -> TrainingCatalog:
    soup = BeautifulSoup(html, "html.parser")
    categories: list[TrainingCategory] = []
    seen_urls: set[str] = set()
    now = datetime.now(timezone.utc)
    logger = logging.getLogger("idn.discovery")
    containers = soup.select("article.betterdocs-single-category-wrapper")
    for container in containers:
        heading = container.find(["h2", "h3"], class_=lambda value: value and "category-title" in value)
        if not heading:
            continue
        category_name = heading.get_text(" ", strip=True)
        products: list[TrainingProduct] = []
        for anchor in container.select(".betterdocs-articles-list a[href]"):
            name = anchor.get_text(" ", strip=True)
            original = anchor.get("href", "")
            if not name or not original:
                continue
            canonical = canonicalize_url(original, source_url)
            duplicate = canonical in seen_urls
            if duplicate:
                continue
            potential = any(
                SequenceMatcher(None, name.casefold(), item.name.casefold()).ratio() > 0.92
                for category in categories for item in category.products
            )
            products.append(TrainingProduct(
                name=name, category=category_name, source_url=canonical,
                canonical_url=canonical, original_url=original, source_page=source_url,
                discovered_at=now, potential_duplicate=potential,
            ))
            seen_urls.add(canonical)
        if products:
            categories.append(TrainingCategory(name=category_name, products=products))
            logger.info("Category discovered: %s (%d products)", category_name, len(products))
    total = sum(len(category.products) for category in categories)
    return TrainingCatalog(source=source_url, generated_at=now,
                           statistics={"categories": len(categories), "products": total,
                                       "unique_urls": len(seen_urls)}, categories=categories)


def choose_diverse_samples(catalog: TrainingCatalog, limit: int) -> list[TrainingProduct]:
    """Round-robin across categories instead of taking one vendor block."""
    buckets = [list(category.products) for category in catalog.categories]
    selected: list[TrainingProduct] = []
    offset = 0
    while len(selected) < limit:
        added = False
        for bucket in buckets:
            if offset < len(bucket) and len(selected) < limit:
                selected.append(bucket[offset])
                added = True
        if not added:
            break
        offset += 1
    return selected


def discover_supporting_pages(html: str, source_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        text = anchor.get_text(" ", strip=True)
        url = canonicalize_url(anchor.get("href", ""), source_url)
        host = urlsplit(url).hostname or ""
        if not host.endswith("idn.id") or url == canonicalize_url(source_url):
            continue
        haystack = f"{text} {url}".casefold()
        page_type = "UNKNOWN"
        for needle, kind in (("trainer", "TRAINER_DIRECTORY"), ("faq", "FAQ"),
                             ("jadwal", "SCHEDULE"), ("schedule", "SCHEDULE"),
                             ("contact", "LOCATION"), ("lokasi", "LOCATION"),
                             ("privacy", "POLICY"), ("training", "TRAINING_PRODUCT")):
            if needle in haystack:
                page_type = kind
                break
        if page_type not in {"UNKNOWN", "TRAINING_PRODUCT"}:
            found[url] = {"title": text or url, "url": url, "page_type": page_type}
    return list(found.values())


def source_hash(product: TrainingProduct) -> str:
    value = f"{product.name}\0{product.category}\0{product.canonical_url}"
    return hashlib.sha256(value.encode()).hexdigest()
