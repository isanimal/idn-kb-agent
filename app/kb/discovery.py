"""Deterministic DOM parsers for the authenticated KB interface."""

import hashlib
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.kb.models import FormField, NavigationItem


def clean(value: str) -> str: return re.sub(r"\s+", " ", value).strip()
def content_hash(value: str) -> str: return hashlib.sha256(clean(value).encode()).hexdigest()


def parse_navigation(html: str, base_url: str) -> list[NavigationItem]:
    soup = BeautifulSoup(html, "html.parser"); seen = set(); items = []
    for anchor in soup.select('a[href^="/kb/"]'):
        href = anchor.get("href", "").split("?")[0]; label = clean(anchor.get_text(" ", strip=True))
        if href.count("/") != 2 or not label or href in seen: continue
        seen.add(href); items.append(NavigationItem(label=label, url=urljoin(base_url, href), parent="Knowledge Based"))
    return items


def _heading_name(anchor: Tag) -> tuple[str, str | None]:
    heading = anchor.find(["h2", "h3", "h4"])
    if not heading: return clean(anchor.get_text(" ", strip=True)), None
    parts = [clean(x) for x in heading.stripped_strings]
    return (parts[0] if parts else clean(heading.get_text(" ", strip=True)), parts[1] if len(parts) > 1 else None)


def parse_resource_cards(html: str, base_url: str, href_fragment: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser"); output = []
    for anchor in soup.select(f'a[href*="{href_fragment}"]'):
        name, short = _heading_name(anchor); url = urljoin(base_url, anchor["href"])
        if not name or any(x["url"] == url for x in output): continue
        output.append({"name": name, "short_name": short, "url": url,
                       "preview": clean(anchor.get_text(" ", strip=True)), "source": "KB_INTERNAL"})
    return output


def parse_trainer_cards(html: str, base_url: str) -> list[dict]:
    """Parse trainer directory cards without folding expertise/cert counts into names."""
    soup = BeautifulSoup(html, "html.parser"); output = []
    for anchor in soup.select('a.dir-card[href*="/kb/trainer/detail"]'):
        url = urljoin(base_url, anchor.get("href", "")); name_node = anchor.select_one(".dir-name")
        if not name_node or any(x["url"] == url for x in output): continue
        counts = {}
        for row in anchor.select(".dir-cert-row"):
            label = clean((row.select_one(".dir-cert-label") or row).get_text(" ", strip=True)).rstrip(":")
            count = row.select_one(".dir-cert-count")
            if label and count: counts[label] = int(clean(count.get_text(strip=True)) or 0)
        output.append({"name": clean(name_node.get_text(" ", strip=True)),
                       "expertise": clean((anchor.select_one(".dir-title") or Tag()).get_text(" ", strip=True)),
                       "certification_counts": counts, "url": url,
                       "status": "OBSERVED", "source": "KB_INTERNAL"})
    return output


def parse_form_schema(html: str) -> list[FormField]:
    soup = BeautifulSoup(html, "html.parser"); fields = []
    for index, label in enumerate(soup.find_all("label")):
        text = clean(label.get_text(" ", strip=True)); label_text = text.rstrip(" *")
        if not label_text: continue
        control = soup.find(id=label.get("for")) if label.get("for") else label.find(["input", "select", "textarea"])
        if not control and "Aktif" in label_text: control = label.find("input")
        if not control: continue
        ctype = "checkbox" if control.get("type") == "checkbox" else control.name
        options = [{"value": o.get("value", ""), "label": clean(o.get_text(" ", strip=True))} for o in control.find_all("option")]
        key = re.sub(r"[^a-z0-9]+", "_", label_text.lower()).strip("_")
        help_node = label.find_next_sibling()
        help_text = clean(help_node.get_text(" ", strip=True)) if help_node and help_node.name not in {"input", "select", "textarea"} else None
        fallbacks = []
        if control.get("name"): fallbacks.append({"strategy": "name_attribute", "value": control["name"]})
        if control.get("placeholder"): fallbacks.append({"strategy": "placeholder", "value": control["placeholder"]})
        fields.append(FormField(field_key=key, label=label_text, control_type=ctype,
            required="*" in text or control.has_attr("required"), multiple=control.has_attr("multiple"),
            placeholder=control.get("placeholder"), help_text=help_text, options=options,
            preferred_locator={"strategy": "label", "value": label_text}, fallbacks=fallbacks))
    return fields


def parse_detail(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser"); main = soup.select_one("main") or soup.body
    headings = main.find_all(["h1", "h2", "h3"]) if main else []
    name = clean(headings[0].get_text(" ", strip=True)) if headings else ""
    sections = []
    for heading in headings[1:]:
        title = clean(heading.get_text(" ", strip=True)); parent = heading.parent
        text = clean(parent.get_text(" ", strip=True)) if parent else title
        sections.append({"heading": title, "content": text[len(title):].strip() if text.startswith(title) else text})
    links = [a.get("href") for a in (main or soup).find_all("a", href=True) if a.get("href", "").startswith("http")]
    return {"name": name, "url": url, "sections": sections, "external_links": links,
            "raw_text": clean(main.get_text(" ", strip=True)) if main else ""}


def parse_faq(html: str, url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser"); output = []
    for item in soup.select("article.faq-item"):
        header = item.select_one(".q-text, .faq-question, .faq-title, h2, h3, h4") or item.find(["div", "p"])
        question = clean(header.get_text(" ", strip=True)) if header else ""
        body = item.select_one(".faq-body .prose")
        answer = clean(body.get_text(" ", strip=True)) if body else ""
        if question: output.append({"question": question, "answer": answer or None, "source_url": url})
    return output
