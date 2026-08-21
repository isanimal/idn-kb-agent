"""Offline semantic audit for saved Step 3 extraction artifacts."""

import json
from pathlib import Path
from typing import Any


CASES = {
    "AI Automation": "ai-automation-tools",
    "Content Creator": "content-creator-social-media-mastery",
    "RCNA WLAN": "rcna-wlan-exam",
    "Google Cloud": "google-cloud-administrator",
    "Huawei HCIA": "huawei-hcia-datacom",
    "UBWA": "ubiquiti-broadband-wireless-admin",
}


def _load(slug: str, root: Path) -> dict[str, Any]:
    return json.loads((root / slug / "facts.json").read_text(encoding="utf-8"))


def _values(field: dict) -> list:
    return field.get("values") or ([field.get("value")] if field.get("value") is not None else [])


def run_parser_audit(root: Path = Path("data/products")) -> dict[str, Any]:
    products = []
    for path in root.glob("*/facts.json"):
        products.append(json.loads(path.read_text(encoding="utf-8")))
    unknown: dict[str, int] = {}; flattened = generic = heading_only = price_errors = duration_errors = 0
    for facts in products:
        for section in facts.get("unknown_sections", []): unknown[section["heading"]] = unknown.get(section["heading"], 0) + 1
        flags = facts.get("quality_flags", [])
        flattened += "FLATTENED_CURRICULUM" in flags; generic += "GENERIC_TRAINER_TEXT" in flags
        heading_only += "HEADING_ONLY_VALUE" in flags
        price_errors += facts["price"]["status"] == "PARSE_ERROR"
        duration_errors += facts["duration"]["status"] == "PARSE_ERROR"
    regression = {}
    for label, slug in CASES.items():
        try: f = _load(slug, root)
        except FileNotFoundError: regression[label] = False; continue
        if label == "AI Automation":
            regression[label] = (f["description"]["status"] == "FOUND" and len(_values(f["price"])) >= 2 and
                len(f["curriculum"].get("value") or []) >= 6 and len(f["facilities"].get("value") or []) >= 5 and
                f["repeat_policy"]["status"] == "FOUND" and f["support_information"]["status"] == "FOUND" and
                f["trainers"]["status"] == "NOT_FOUND")
        elif label == "Content Creator":
            curriculum = f["curriculum"].get("value") or []
            regression[label] = (_values(f["price"])[0]["amount"] == 1_500_000 and
                                 [x.get("title") for x in curriculum[:2]] == ["DAY 1", "DAY 2"])
        elif label == "RCNA WLAN":
            regression[label] = len(_values(f["price"])) == 2 and len(f["trainers"].get("value") or []) >= 2 and bool(f["curriculum"].get("value"))
        elif label == "Google Cloud":
            duration = _values(f["duration"])[0]; schedule = duration.get("daily_schedule") or {}
            regression[label] = duration.get("days") == 4 and schedule.get("start") == "09:00" and schedule.get("end") == "17:00" and _values(f["price"])[0]["amount"] == 2_500_000 and f["trainers"]["status"] == "FOUND"
        elif label == "Huawei HCIA":
            regression[label] = _values(f["price"])[0]["amount"] == 2_800_000 and f["trainers"]["status"] == "FOUND"
        else:
            regression[label] = bool(f["curriculum"].get("value")) and f["trainers"]["status"] == "NOT_FOUND"
    return {"products": len(products), "unknown_sections": sum(unknown.values()),
            "top_unknown_headings": sorted(unknown.items(), key=lambda x: (-x[1], x[0]))[:10],
            "flattened_curriculum": flattened, "generic_trainer": generic, "heading_only_values": heading_only,
            "price_parse_errors": price_errors, "duration_parse_errors": duration_errors, "regression": regression}


def print_parser_audit(audit: dict[str, Any]) -> None:
    print("PARSER AUDIT\n")
    print(f"Products................ {audit['products']}")
    print(f"Unknown sections........ {audit['unknown_sections']}\n\nTop unknown headings:")
    for index, (heading, count) in enumerate(audit["top_unknown_headings"], 1): print(f"{index}. {heading} ({count})")
    print(f"\nPrice parse errors....... {audit['price_parse_errors']}")
    print(f"Duration parse errors.... {audit['duration_parse_errors']}")
    print(f"Flat giant curriculums... {audit['flattened_curriculum']}")
    print(f"Generic trainer text..... {audit['generic_trainer']}")
    print(f"Heading-only values...... {audit['heading_only_values']}\n\nRegression cases:")
    for label, passed in audit["regression"].items(): print(f"{label + '':26} {'PASS' if passed else 'FAIL'}")
