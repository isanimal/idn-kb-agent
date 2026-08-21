import json
import tempfile
import unittest
from pathlib import Path

from app.core.database import Database
from app.discovery.idn_discovery import canonicalize_url, parse_training_directory, source_hash
from app.discovery.models import TrainingProduct
from app.site_model.builder import save_json


class ReconnaissanceTests(unittest.TestCase):
    def test_canonicalization(self) -> None:
        self.assertEqual(canonicalize_url("http://WWW.IDN.ID/training/foo#x"),
                         "https://www.idn.id/training/foo/")
        self.assertEqual(canonicalize_url("/training/foo?a=1#x"),
                         "https://www.idn.id/training/foo/?a=1")

    def test_parser_relationship_and_duplicate_prevention(self) -> None:
        html = Path("tests/fixtures/training_index.html").read_text(encoding="utf-8")
        catalog = parse_training_directory(html, "https://www.idn.id/training/")
        self.assertEqual([category.name for category in catalog.categories], ["Cisco", "Mikrotik"])
        self.assertEqual(catalog.statistics, {"categories": 2, "products": 2, "unique_urls": 2})
        self.assertEqual(catalog.categories[0].products[0].category, "Cisco")

    def test_models_json_serialization(self) -> None:
        html = Path("tests/fixtures/training_index.html").read_text(encoding="utf-8")
        catalog = parse_training_directory(html, "https://www.idn.id/training/")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "catalog.json"
            save_json(path, catalog)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["statistics"]["products"], 2)

    def test_database_upsert(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.db")
            db.initialize_database()
            kwargs = dict(name="CCNA", category="Cisco", source_url="https://www.idn.id/training/ccna/",
                          canonical_url="https://www.idn.id/training/ccna/", discovered_at="2026-01-01T00:00:00Z",
                          source_hash="abc")
            self.assertTrue(db.upsert_training_source(**kwargs)[1])
            kwargs["name"] = "CCNA Updated"
            self.assertFalse(db.upsert_training_source(**kwargs)[1])
            self.assertEqual(db.count_training_sources(), 1)


if __name__ == "__main__":
    unittest.main()
