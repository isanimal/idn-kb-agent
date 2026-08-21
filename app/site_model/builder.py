"""Build deterministic patterns and inventory from saved DOM pages."""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.site_model.models import HeadingInventoryItem, PageAnalysis


def normalize_heading(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.casefold())).strip()


def analyze_page(html: str, url: str, fetch_method: str, page_type: str) -> PageAnalysis:
    soup = BeautifulSoup(html, "html.parser")
    headings = {tag: [x.get_text(" ", strip=True) for x in soup.find_all(tag)
                      if x.get_text(" ", strip=True)] for tag in ("h1", "h2", "h3")}
    text = soup.get_text(" ", strip=True).casefold()
    json_types: list[str] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "{}")
            items = data.get("@graph", []) if isinstance(data, dict) else data
            if isinstance(items, dict): items = [items]
            if isinstance(data, dict) and data.get("@type"): items = [data, *items]
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get("@type"):
                    kind = item["@type"]
                    json_types.extend(kind if isinstance(kind, list) else [str(kind)])
        except (json.JSONDecodeError, TypeError):
            pass
    links = []
    for anchor in soup.select("a[href]"):
        absolute = urljoin(url, anchor["href"])
        if (urlsplit(absolute).hostname or "").endswith("idn.id"):
            links.append(absolute.split("#", 1)[0])
    keywords = {
        "pricing": ("rp", "harga", "biaya"), "duration": ("durasi", "hari", "jam"),
        "curriculum": ("kurikulum", "materi", "curriculum"), "trainer": ("trainer", "instruktur"),
        "facility": ("fasilitas",), "benefit": ("benefit", "keuntungan"),
        "prerequisite": ("prasyarat", "prerequisite"), "certification": ("sertifikasi", "certificate"),
        "cta": ("daftar", "register", "hubungi"),
    }
    return PageAnalysis(url=url, fetch_method=fetch_method, page_type=page_type,
                        title=soup.title.get_text(" ", strip=True) if soup.title else "",
                        h1=headings["h1"], h2=headings["h2"], h3=headings["h3"],
                        section_headings=headings["h1"] + headings["h2"] + headings["h3"],
                        json_ld_types=sorted(set(json_types)), list_count=len(soup.find_all(["ul", "ol"])),
                        table_count=len(soup.find_all("table")),
                        patterns={name: any(word in text for word in words) for name, words in keywords.items()},
                        internal_links=sorted(set(links)))


def build_heading_inventory(pages: list[PageAnalysis]) -> list[HeadingInventoryItem]:
    inventory: dict[tuple[str, str], set[str]] = defaultdict(set)
    for page in pages:
        for raw in page.section_headings:
            inventory[(raw, normalize_heading(raw))].add(page.url)
    return [HeadingInventoryItem(raw_heading=raw, normalized_text=normalized, seen_on=sorted(urls))
            for (raw, normalized), urls in sorted(inventory.items(), key=lambda item: item[0][1])]


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
