"""Deterministic parser for IDN BetterDocs training landing pages."""

import hashlib
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from app.extractor.models import Evidence, FactField, FieldStatus, RawSection, TrainingFacts
from app.extractor.section_aliases import semantic_section


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u200b", "").replace("\ufeff", "")).strip()


def _without_heading(text: str, heading: str) -> str:
    """Remove repeated heading prefixes while preserving descendant evidence."""
    text, heading = clean_text(text), clean_text(heading)
    while heading and text.lower().startswith(heading.lower()):
        text = clean_text(text[len(heading):])
    return text


def _day_curriculum(container: Tag | None) -> list[dict]:
    if not container: return []
    days: list[dict] = []; current_day = None; current_topic = None
    for node in container.find_all(["p", "ul", "ol"], recursive=True):
        if node.find_parent(["ul", "ol"]) and node.name in {"p", "ul", "ol"}: continue
        text = clean_text(node.get_text(" ", strip=True))
        day = re.match(r"(?i)^DAY\s+(\d+)\b", text)
        if node.name == "p" and day:
            current_day = {"title": f"DAY {day.group(1)}", "objective": None, "items": [],
                           "raw": "", "structure_type": "DAY_BASED"}
            days.append(current_day)
            remainder = clean_text(text[day.end():])
            if remainder: current_topic = {"title": remainder, "items": []}; current_day["items"].append(current_topic)
        elif node.name == "p" and re.match(r"^\d{1,2}\.\s+", text) and current_day:
            current_topic = {"title": text, "items": []}; current_day["items"].append(current_topic)
        elif node.name in {"ul", "ol"} and current_topic:
            current_topic["items"].extend(clean_text(li.get_text(" ", strip=True)) for li in node.find_all("li", recursive=False))
    return days


def _context_after_heading(container: Tag, heading: Tag, title: str) -> str:
    full = clean_text(container.get_text(" ", strip=True))
    position = full.lower().find(title.lower())
    text = clean_text(full[position + len(title):]) if position >= 0 else full
    following = []
    passed = False
    for candidate in container.find_all(["h1", "h2", "h3", "h4"]):
        if candidate is heading: passed = True; continue
        if passed:
            value = clean_text(candidate.get_text(" ", strip=True))
            index = text.lower().find(value.lower())
            if value and index >= 0: following.append(index)
    if following: text = clean_text(text[:min(following)])
    return text


def relevant_content_hash(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".betterdocs-content") or soup.select_one(".entry-content") or soup.select_one("main") or soup.body
    text = clean_text(root.get_text(" ", strip=True) if root else "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_prices(text: str) -> list[dict]:
    results = []
    pattern = re.compile(r"(?i)(mulai(?:\s+dari)?\s*)?(?:rp\.?\s*)?(\d[\d.\s]*(?:,\d+)?)\s*(juta|jt)?")
    for match in pattern.finditer(text):
        raw = clean_text(match.group(0))
        if not ("rp" in raw.lower() or match.group(3)):
            continue
        try:
            if match.group(3):
                amount = int(float(match.group(2).replace(".", "").replace(" ", "").replace(",", ".")) * 1_000_000)
            else:
                amount = int(match.group(2).replace(".", "").replace(" ", "").split(",")[0])
        except ValueError:
            continue
        results.append({"raw": raw, "amount": amount, "currency": "IDR",
                        "qualifier": "STARTING_FROM" if match.group(1) else None})
    return results


def parse_duration(text: str) -> dict:
    result = {"raw": clean_text(text), "days": None, "hours": None, "daily_schedule": None}
    day = re.search(r"(?i)\b(\d+(?:[.,]\d+)?)\s*(?:hari|days?)\b", text)
    hour = re.search(r"(?i)\b(\d+(?:[.,]\d+)?)\s*(?:jam|hours?)\b", text)
    schedule = re.search(r"\b((?:[01]?\d|2[0-3])[.:]\d{2})\s*(?:[-–]|s\.?\s*d\.?)\s*((?:[01]?\d|2[0-3])[.:]\d{2})\b", text, re.I)
    if day: result["days"] = float(day.group(1).replace(",", "."))
    if hour: result["hours"] = float(hour.group(1).replace(",", "."))
    if schedule:
        result["daily_schedule"] = {"start": schedule.group(1).replace(".", ":"),
                                    "end": schedule.group(2).replace(".", ":"),
                                    "timezone": "WIB" if re.search(r"\bWIB\b", text, re.I) else None}
    return result


def extract_raw_sections(html: str) -> tuple[str, list[RawSection]]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".betterdocs-content")
    if not root:
        root = next((x for x in soup.select(".elementor") if "elementor-1024" not in (x.get("class") or [])), None) or soup.select_one(".entry-content")
        if not root: return "", []
        sections = []
        for heading in root.find_all(["h1", "h2", "h3", "h4"]):
            if heading.find_parent(class_=lambda c: c and "elementor-1024" in c): continue
            title = clean_text(heading.get_text(" ", strip=True))
            widget = heading.find_parent(attrs={"data-element_type": "widget"})
            context_section = heading.find_parent("section", class_=lambda c: c and "elementor-section" in c)
            widget_text = clean_text(widget.get_text(" ", strip=True)) if widget else title
            content = _without_heading(widget_text, title)
            # Heading widgets commonly have their factual paragraph/list in sibling widgets.
            if not content and context_section:
                candidate = _context_after_heading(context_section, heading, title)
                if len(candidate) <= 5000: content = candidate
            kind = semantic_section(title)
            item_container = widget if kind == "CURRICULUM_UNIT" else (context_section or widget)
            items = [clean_text(li.get_text(" ", strip=True)) for li in item_container.find_all("li")] if item_container else []
            if kind == "CURRICULUM":
                structured_days = _day_curriculum(context_section)
                if structured_days: items = structured_days
            sections.append(RawSection(heading=title, semantic_type=semantic_section(title), content=content, items=items))
        return "", sections
    h1 = soup.select_one("h1.betterdocs-entry-title") or soup.select_one(".docs-single-title h1") or root.find("h1")
    name = clean_text(h1.get_text(" ", strip=True)) if h1 else ""
    sections = []
    for heading in root.find_all("h2", recursive=True):
        title = clean_text(heading.get_text(" ", strip=True))
        nodes = []
        for sibling in heading.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "h2": break
            if isinstance(sibling, Tag): nodes.append(sibling)
        content = clean_text(" ".join(n.get_text(" ", strip=True) for n in nodes))
        items = []
        has_modules = any(node.name == "h4" for node in nodes)
        for node in nodes:
            if node.name == "h4":
                subitems = []
                nxt = node.find_next_sibling()
                while nxt and nxt.name not in {"h2", "h4"}:
                    if nxt.name in {"ul", "ol"}: subitems.extend(clean_text(li.get_text(" ", strip=True)) for li in nxt.find_all("li"))
                    nxt = nxt.find_next_sibling()
                items.append({"title": clean_text(node.get_text(" ", strip=True)), "items": subitems})
            elif not has_modules and node.name in {"ul", "ol"} and not node.find_parent(["ul", "ol"]):
                items.extend(clean_text(li.get_text(" ", strip=True)) for li in node.find_all("li", recursive=False))
        sections.append(RawSection(heading=title, semantic_type=semantic_section(title), content=content, items=items))
    return name, sections


def _field(sections: list[RawSection], kind: str, url: str, transform=None) -> FactField:
    matches = [s for s in sections if s.semantic_type == kind]
    if not matches: return FactField()
    evidence = [Evidence(source_url=url, source_section=s.heading,
                         source_text=clean_text(f"{s.heading} {s.content}")) for s in matches]
    raw_values = [s.items or s.content or s.heading for s in matches]
    try:
        values = transform(matches) if transform else raw_values
    except Exception:
        return FactField(status=FieldStatus.PARSE_ERROR, evidence=evidence)
    if not isinstance(values, list): values = [values]
    values = [v for v in values if v not in (None, "", [])]
    if not values: return FactField(status=FieldStatus.PARSE_ERROR, evidence=evidence)
    return FactField(status=FieldStatus.FOUND, value=values[0] if len(values) == 1 else None,
                     values=values if len(values) > 1 else [], evidence=evidence)


def _curriculum_field(sections: list[RawSection], url: str) -> FactField:
    matches = [s for s in sections if s.semantic_type == "CURRICULUM"]
    if not matches: return FactField()
    units = [s for s in sections if s.semantic_type == "CURRICULUM_UNIT"]
    items = []
    if units:
        for unit in units:
            objective = None
            objective_match = re.search(r"(?i)\bTujuan\s*:\s*(.*?\.)", unit.content)
            if objective_match: objective = clean_text(objective_match.group(1))
            topics = unit.items[:]
            if not topics:
                topics = [clean_text(x) for x in re.findall(r"(?:^|\s)([A-Z][^:]{1,45}):\s*", unit.content) if x.lower() != "tujuan"]
            items.append({"title": unit.heading, "objective": objective, "items": topics,
                          "raw": unit.content, "structure_type": "MODULED"})
    else:
        for section in matches:
            text = section.content
            if section.items and isinstance(section.items[0], dict) and section.items[0].get("structure_type") == "DAY_BASED":
                items.extend(section.items); continue
            day_parts = re.split(r"(?i)\b(DAY\s+\d+)\b", text)
            if len(day_parts) >= 3:
                for index in range(1, len(day_parts), 2):
                    body = day_parts[index + 1] if index + 1 < len(day_parts) else ""
                    topic_matches = list(re.finditer(r"(?<!\d)(\d{1,2})\.\s+([^\d]+?)(?=\s+\d{1,2}\.\s+|$)", body))
                    topics = [clean_text(f"{m.group(1)}. {m.group(2)}") for m in topic_matches]
                    items.append({"title": day_parts[index].upper(), "objective": None,
                                  "items": topics or [clean_text(body)], "raw": clean_text(body),
                                  "structure_type": "DAY_BASED"})
            elif section.items:
                items.extend(section.items)
            elif text:
                module_matches = list(re.finditer(r"(?i)\b(?:module|modul|chapter|topic)\s*\d+\b", text))
                if len(module_matches) >= 2:
                    for index, match in enumerate(module_matches):
                        end = module_matches[index + 1].start() if index + 1 < len(module_matches) else len(text)
                        block = clean_text(text[match.start():end]); parts = [clean_text(x) for x in block.split("•") if clean_text(x)]
                        items.append({"title": parts[0], "objective": None, "items": parts[1:], "raw": block,
                                      "structure_type": "MODULED"})
                else:
                    items.append({"title": None, "objective": None, "items": [text], "raw": text,
                                  "structure_type": "RAW"})
    evidence = [Evidence(source_url=url, source_section=s.heading,
                         source_text=clean_text(f"{s.heading} {s.content}")) for s in matches]
    return FactField(status=FieldStatus.FOUND if items else FieldStatus.PARSE_ERROR,
                     value=items or None, evidence=evidence)


def _facilities_field(sections: list[RawSection], url: str) -> FactField:
    matches = [s for s in sections if s.semantic_type == "FACILITIES"]
    evidence = [Evidence(source_url=url, source_section=s.heading,
                         source_text=clean_text(f"{s.heading} {s.content}")) for s in matches]
    values = []
    for section in matches:
        values.extend(section.items)
        if not section.items and section.content:
            content = re.split(r"(?i)\b(?:after sales|sesi tanya jawab|trainer (?:ter)?sertifikasi)\b", section.content)[0]
            values.extend(clean_text(x.strip(" .")) for x in re.split(r",|\s+dan\s+", content, flags=re.I) if clean_text(x.strip(" .")))
    values = list(dict.fromkeys(v for v in values if v.lower() != "fasilitas lengkap"))
    return FactField(status=FieldStatus.FOUND if values else (FieldStatus.PARSE_ERROR if matches else FieldStatus.NOT_FOUND),
                     value=values or None, evidence=evidence)


def _trainer_field(sections: list[RawSection], url: str) -> tuple[FactField, bool]:
    matches = [s for s in sections if s.semantic_type == "TRAINERS"]
    evidence = [Evidence(source_url=url, source_section=s.heading,
                         source_text=clean_text(f"{s.heading} {s.content}")) for s in matches]
    generic = re.compile(r"(?i)^(?:trainer|instruktur)?\s*(?:bersertifikasi|tersertifikasi|profesional|international|internasional|berpengalaman)(?:\b.*)?$")
    candidates = []
    for section in matches:
        raw = section.items or ([section.content] if section.content else [])
        candidates.extend(v for v in raw if v and not generic.match(clean_text(v)) and
                          not re.search(r"(?i)\b(?:mengajar|pengalaman|praktisi)\b", v))
    candidates = list(dict.fromkeys(candidates))
    had_generic = bool(matches) and not candidates
    return FactField(status=FieldStatus.FOUND if candidates else FieldStatus.NOT_FOUND,
                     value=candidates or None, evidence=evidence), had_generic


def _description_field(sections: list[RawSection], url: str) -> FactField:
    direct = _field(sections, "DESCRIPTION", url)
    if direct.status == FieldStatus.FOUND: return direct
    for section in sections[:8]:
        normalized = section.heading.lower()
        if ("training" in normalized or "pelatihan" in normalized) and len(section.content) >= 40:
            text = re.split(r"(?i)\b(?:daftar sekarang|booking sekarang|cek jadwal)\b", section.content)[0].strip()
            if text:
                evidence = Evidence(source_url=url, source_section=section.heading,
                                    source_text=clean_text(f"{section.heading} {text}"))
                return FactField(status=FieldStatus.FOUND, value=text, evidence=[evidence])
    for section in sections[:5]:
        if len(section.content) >= 60:
            text = re.split(r"(?i)\b(?:daftar sekarang|booking sekarang|cek jadwal)\b", section.content)[0].strip()
            if text:
                return FactField(status=FieldStatus.FOUND, value=text,
                                 evidence=[Evidence(source_url=url, source_section=section.heading,
                                                    source_text=clean_text(f"{section.heading} {text}"))])
    return direct


def _repeat_field(sections: list[RawSection], url: str) -> FactField:
    direct = _field(sections, "REPEAT_POLICY", url)
    def isolate(value: str) -> str:
        value=clean_text(value)
        boundary=re.search(r"(?i)\b(?:lunch|makan siang|coffee\s*break|coffe\s*break|coffebreak|penginapan|sertifikat|kaos|t-?shirt|akses internet|goodie bag|ruangan ber\s*ac)\b",value)
        if boundary:value=value[:boundary.start()].strip(" ,;:-")
        repeats=list(re.finditer(r"(?i)\b(?:gratis|free)\s+(?:mengikuti\s+)?(?:mengulang|ulang)\b",value))
        if len(repeats)>1:value=value[:repeats[1].start()].strip()
        return value.rstrip(".")
    if direct.status == FieldStatus.FOUND:
        cleaned=isolate(str(direct.value or ""))
        if cleaned:
            direct.value=cleaned
            direct.evidence=[Evidence(source_url=url,source_section=e.source_section,source_text=clean_text(f"{e.source_section} {cleaned}"),confidence=e.confidence) for e in direct.evidence]
        return direct
    pattern = re.compile(r"(?i)(?:gratis|free)\s+.*?mengulang[^.,;]*(?:[.,;]|$)")
    for section in sections:
        haystack = " ".join([section.content, *[x for x in section.items if isinstance(x, str)]])
        match = pattern.search(haystack)
        if match:
            value = isolate(match.group(0).strip(" ,;."))
            return FactField(status=FieldStatus.FOUND, value=value,
                             evidence=[Evidence(source_url=url, source_section=section.heading,
                                                source_text=clean_text(f"{section.heading} {value}"))])
    return direct


def parse_training_page(html: str, source_url: str, category: str, registry_name: str) -> TrainingFacts:
    name, sections = extract_raw_sections(html)
    if not name: name = registry_name
    ev = lambda section, text: [Evidence(source_url=source_url, source_section=section, source_text=text)]
    identity = {
        "full_name": FactField(status=FieldStatus.FOUND if name else FieldStatus.NOT_FOUND, value=name or None,
                               evidence=ev("Page title", name) if name else []),
        "short_name": FactField(),
        "source_category": FactField(status=FieldStatus.FOUND, value=category,
                                     evidence=ev("Training registry", category)),
        "source_url": FactField(status=FieldStatus.FOUND, value=source_url,
                                evidence=ev("Training registry", source_url)),
    }
    price_sections = [s for s in sections if s.semantic_type in {"PRICE", "PRICE_VALUE"}]
    # Include money headings/cards sharing the same Elementor price block.
    prices = _field([RawSection(heading=s.heading, semantic_type="PRICE", content=s.content, items=s.items)
                     for s in price_sections], "PRICE", source_url,
                    lambda ss: [p for s in ss for p in parse_prices(f"{s.heading} {s.content}")])
    price_values = prices.values or ([prices.value] if prices.value else [])
    if price_values:
        unique_prices = list({(p["amount"], p.get("raw")): p for p in price_values}.values())
        prices = FactField(status=FieldStatus.FOUND, value=unique_prices[0] if len(unique_prices) == 1 else None,
                           values=unique_prices if len(unique_prices) > 1 else [], evidence=prices.evidence)
    duration_sections = [s for s in sections if s.semantic_type == "DURATION"]
    if not duration_sections:
        duration_sections = [s for s in sections if re.search(r"(?i)\b\d+\s*(?:hari|days?)\b", s.content)]
    durations = _field([RawSection(heading=s.heading, semantic_type="DURATION", content=s.content, items=s.items)
                        for s in duration_sections], "DURATION", source_url,
                       lambda ss: [parse_duration(f"{s.heading} {s.content}") for s in ss])
    curriculum = _curriculum_field(sections, source_url)
    trainer, generic_trainer = _trainer_field(sections, source_url)
    facts = TrainingFacts(identity=identity, description=_description_field(sections, source_url),
        duration=durations, price=prices, training_format=_field(sections, "TRAINING_FORMAT", source_url), curriculum=curriculum,
        benefits=_field(sections, "BENEFITS", source_url), facilities=_facilities_field(sections, source_url),
        prerequisites=_field(sections, "PREREQUISITES", source_url), target_audiences=_field(sections, "TARGET_AUDIENCE", source_url),
        certifications=_field(sections, "CERTIFICATIONS", source_url), trainers=trainer,
        tools=_field(sections, "TOOLS", source_url), practice=_field(sections, "PRACTICE", source_url),
        support_information=_field(sections, "SUPPORT", source_url), repeat_policy=_repeat_field(sections, source_url), raw_sections=sections,
        unknown_sections=[s for s in sections if s.semantic_type == "UNKNOWN_SECTION"])
    checked = [facts.identity["full_name"], facts.identity["short_name"], facts.identity["source_category"], facts.description,
               facts.duration, facts.price, facts.training_format, facts.curriculum, facts.benefits, facts.facilities,
               facts.prerequisites, facts.target_audiences, facts.certifications, facts.trainers, facts.tools, facts.practice]
    found = sum(f.status == FieldStatus.FOUND for f in checked)
    facts.completeness = {"fields_checked": len(checked), "fields_found": found,
                          "fields_not_found": sum(f.status == FieldStatus.NOT_FOUND for f in checked),
                          "coverage": round(found / len(checked), 4)}
    flags = []
    if facts.price.status != FieldStatus.FOUND: flags.append("MISSING_PRICE")
    if facts.duration.status != FieldStatus.FOUND: flags.append("MISSING_DURATION")
    if facts.curriculum.status != FieldStatus.FOUND: flags.append("NO_CURRICULUM")
    if len(facts.price.values) > 1: flags.append("MULTIPLE_PRICE_VALUES")
    if facts.unknown_sections: flags.append("UNKNOWN_SECTIONS")
    if any(f.status == FieldStatus.PARSE_ERROR for f in checked): flags.append("PARSER_WARNING")
    if generic_trainer: flags.append("GENERIC_TRAINER_TEXT")
    curriculum_value = facts.curriculum.value or []
    if len(curriculum_value) == 1 and len(str(curriculum_value[0])) > 1000 and re.search(r"(?i)(?:modul|module|day)\s*1.*(?:modul|module|day)\s*2", str(curriculum_value[0])):
        flags.append("FLATTENED_CURRICULUM")
    substantive = ("facilities", "benefits", "support_information", "practice")
    for field_name in substantive:
        field = getattr(facts, field_name)
        if field.status == FieldStatus.FOUND and field.evidence:
            values_text = clean_text(str(field.value or field.values))
            if values_text and any(values_text.lower() == e.source_section.lower() for e in field.evidence):
                flags.append("HEADING_ONLY_VALUE"); break
    facts.quality_flags = flags
    soup = BeautifulSoup(html, "html.parser")
    template = "BETTERDOCS" if soup.select_one(".betterdocs-content") else "ELEMENTOR" if soup.select_one(".elementor") else "UNKNOWN"
    facts.metadata = {"registry_name": registry_name, "content_hash": relevant_content_hash(html), "template_type": template}
    return facts


def evidence_document(facts: TrainingFacts) -> dict:
    output = {}
    for key in ("description", "duration", "price", "training_format", "curriculum", "benefits", "facilities",
                "prerequisites", "target_audiences", "certifications", "trainers", "tools", "practice",
                "support_information", "repeat_policy", "related_training", "contact_information"):
        output[key] = [e.model_dump(mode="json") for e in getattr(facts, key).evidence]
    output["identity"] = {k: [e.model_dump(mode="json") for e in v.evidence] for k, v in facts.identity.items()}
    return output
