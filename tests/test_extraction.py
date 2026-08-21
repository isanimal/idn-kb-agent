import json
import tempfile
import unittest
import sqlite3
from pathlib import Path

from app.core.database import Database
from app.extractor.models import FactField, FieldStatus
from app.extractor.parser import evidence_document, parse_duration, parse_prices, parse_training_page, relevant_content_hash
from app.site_model.builder import save_json


HTML = Path("tests/fixtures/training_product.html").read_text(encoding="utf-8")
URL = "https://www.idn.id/training/network-pro/"


class ExtractionTests(unittest.TestCase):
    def test_section_mapping_curriculum_and_evidence(self):
        facts = parse_training_page(HTML, URL, "Cisco", "Network Pro")
        self.assertEqual(facts.description.status, FieldStatus.FOUND)
        self.assertEqual(facts.curriculum.value[0], {"title": "Module 1", "items": ["Topic A", "Topic B"]})
        self.assertEqual(evidence_document(facts)["duration"][0]["source_url"], URL)
        self.assertEqual(facts.unknown_sections[0].heading, "Bagian Unik")

    def test_price_normalization_and_ambiguity_flag(self):
        values = parse_prices("Mulai dari Rp2.000.000 dan Rp 2.500.000")
        self.assertEqual([v["amount"] for v in values], [2000000, 2500000])
        self.assertEqual(values[0]["qualifier"], "STARTING_FROM")
        self.assertEqual(parse_prices("Rp. 8,1 JT")[0]["amount"], 8100000)
        self.assertEqual(parse_prices("2 juta")[0]["amount"], 2000000)
        self.assertIn("MULTIPLE_PRICE_VALUES", parse_training_page(HTML, URL, "Cisco", "x").quality_flags)

    def test_duration(self):
        value = parse_duration("2 hari / 16 jam, 09.00-17.00")
        self.assertEqual((value["days"], value["hours"]), (2.0, 16.0))
        self.assertEqual(value["daily_schedule"], {"start": "09:00", "end": "17:00", "timezone": None})

    def test_field_status_and_json_serialization(self):
        self.assertEqual(FactField().status, FieldStatus.NOT_FOUND)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "facts.json"; save_json(path, parse_training_page(HTML, URL, "Cisco", "x"))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["price"]["status"], "FOUND")

    def test_hash_ignores_irrelevant_shell(self):
        self.assertEqual(relevant_content_hash(HTML), relevant_content_hash(HTML.replace("<body>", "<body><nav>changed</nav>")))

    def test_database_upsert_and_resume_state(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "x.db"); db.initialize_database()
            source_id, _ = db.upsert_training_source(name="x", category="c", source_url=URL, canonical_url=URL,
                                                      discovered_at="2026-01-01T00:00:00Z", source_hash="x")
            db.upsert_training_extraction(source_id, URL, "COMPLETED", content_hash="abc", facts_path="facts.json")
            db.upsert_training_extraction(source_id, URL, "COMPLETED", content_hash="abc", facts_path="facts.json")
            self.assertEqual(len(db.list_training_extractions()), 1)
            self.assertEqual(db.get_training_extraction(source_id)["content_hash"], "abc")

    def _saved(self, slug):
        conn = sqlite3.connect("data/idn_kb.db"); conn.row_factory = sqlite3.Row
        source = dict(conn.execute("SELECT * FROM training_sources WHERE canonical_url LIKE ?", (f"%/{slug}/",)).fetchone())
        conn.close()
        html = (Path("data/products") / slug / "raw.html").read_text(encoding="utf-8")
        return parse_training_page(html, source["canonical_url"], source["category"], source["name"])

    def test_ai_automation_regression(self):
        facts = self._saved("ai-automation-tools")
        self.assertEqual([p["amount"] for p in facts.price.values], [2500000, 1700000])
        self.assertEqual(facts.trainers.status, FieldStatus.NOT_FOUND)
        self.assertIn("Sertifikat", facts.facilities.value)
        self.assertEqual(facts.curriculum.value[0]["title"], "Modul 1: Pengenalan & Persiapan (The Basics)")
        self.assertEqual(facts.curriculum.value[0]["objective"], "Memahami apa itu n8n dan cara mengaksesnya.")
        self.assertEqual(facts.repeat_policy.status, FieldStatus.FOUND)
        self.assertEqual(facts.support_information.status, FieldStatus.FOUND)
        self.assertNotIn("Free Mengulang Free Mengulang", facts.repeat_policy.evidence[0].source_text)

    def test_content_creator_day_hierarchy(self):
        facts = self._saved("content-creator-social-media-mastery")
        self.assertEqual(facts.price.value["amount"], 1500000)
        self.assertEqual([x["title"] for x in facts.curriculum.value], ["DAY 1", "DAY 2"])

    def test_robotik_repeat_policy_excludes_facility_siblings(self):
        facts=self._saved("robotik-iot")
        self.assertEqual(facts.repeat_policy.value,"Gratis Mengulang training sebanyak 2 kali")
        self.assertNotIn("Lunch",facts.repeat_policy.value)
        self.assertNotIn("Sertifikat",facts.repeat_policy.value)

    def test_rcna_multiple_prices_trainers_and_curriculum(self):
        facts = self._saved("rcna-wlan-exam")
        self.assertEqual(len(facts.price.values), 2); self.assertGreaterEqual(len(facts.trainers.value), 2)
        self.assertGreaterEqual(len(facts.curriculum.value), 2)

    def test_google_huawei_ubwa_regressions(self):
        google = self._saved("google-cloud-administrator"); duration = google.duration.value
        self.assertEqual((duration["days"], duration["daily_schedule"]["start"], duration["daily_schedule"]["end"]), (4.0, "09:00", "17:00"))
        self.assertEqual(google.price.value["amount"], 2500000); self.assertEqual(google.trainers.status, FieldStatus.FOUND)
        huawei = self._saved("huawei-hcia-datacom")
        self.assertEqual(huawei.price.value["amount"], 2800000); self.assertEqual(huawei.trainers.status, FieldStatus.FOUND)
        ubwa = self._saved("ubiquiti-broadband-wireless-admin")
        self.assertTrue(ubwa.curriculum.value); self.assertEqual(ubwa.trainers.status, FieldStatus.NOT_FOUND)


if __name__ == "__main__": unittest.main()
