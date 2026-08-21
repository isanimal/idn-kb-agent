import tempfile, unittest
from pathlib import Path
from app.core.database import Database
from app.kb.auth import classify_auth_page
from app.kb.discovery import content_hash, parse_faq, parse_form_schema, parse_navigation, parse_resource_cards, parse_trainer_cards
from app.kb.guard import ReadOnlyGuard, ReadOnlyViolation, should_block_request
from app.kb.models import AuthState

class KBReconTests(unittest.TestCase):
    def fixture(self,name): return Path("tests/fixtures",name).read_text(encoding="utf-8")
    def test_auth_classification(self):
        self.assertEqual(classify_auth_page(self.fixture("kb_login.html"),"https://kb.idn.id/login"),AuthState.AUTH_REQUIRED)
        self.assertEqual(classify_auth_page(self.fixture("kb_authenticated.html"),"https://kb.idn.id/kb/training"),AuthState.AUTHENTICATED)
    def test_navigation_and_products(self):
        html=self.fixture("kb_authenticated.html")
        self.assertEqual(len(parse_navigation(html,"https://kb.idn.id")),2)
        products=parse_resource_cards(html,"https://kb.idn.id","/kb/training/detail")
        self.assertEqual((products[0]["name"],products[0]["short_name"]),("CCNA","CCNA"))
    def test_form_and_categories(self):
        fields=parse_form_schema(self.fixture("kb_form.html"));self.assertTrue(fields[0].required)
        self.assertEqual([x["label"] for x in fields[1].options],["Networking","AI"])
    def test_trainer_and_faq_semantic_parsing(self):
        trainer='<a class="dir-card" href="/kb/trainer/detail?id=1"><span class="dir-name">A Trainer</span><span class="dir-title">Networking</span><span class="dir-cert-row"><span class="dir-cert-label">active cert:</span><span class="dir-cert-count">2</span></span></a>'
        parsed=parse_trainer_cards(trainer,"https://kb.idn.id")[0]
        self.assertEqual((parsed["name"],parsed["expertise"],parsed["certification_counts"]["active cert"]),("A Trainer","Networking",2))
        faq='<article class="faq-item"><span class="q-text">Pertanyaan?</span><span class="faq-meta">metadata</span><div class="faq-body"><div class="prose"><p>Jawaban.</p></div><p class="faq-approval">metadata</p></div></article>'
        self.assertEqual(parse_faq(faq,"https://kb.idn.id/kb/faq")[0]["answer"],"Jawaban.")
    def test_read_only_guard_rules(self):
        for method in ("POST","PUT","PATCH","DELETE"):self.assertTrue(should_block_request(method,"https://kb.idn.id/api/x"))
        self.assertFalse(should_block_request("GET","https://kb.idn.id/kb/training"));self.assertFalse(should_block_request("POST","https://example.com/x"))
        with self.assertRaises(ReadOnlyViolation):ReadOnlyGuard().assert_safe_ui_action("Simpan")
    def test_hash_and_database_idempotency(self):
        self.assertEqual(content_hash(" a  b "),content_hash("a b"))
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/"x.db");db.initialize_database();kw=dict(resource_type="FAQ",name="Q",url="https://kb.idn.id/kb/faq",canonical_key="faq:q",content_hash="x")
            self.assertEqual(db.upsert_kb_resource(**kw),"new");self.assertEqual(db.upsert_kb_resource(**kw),"unchanged");kw["content_hash"]="y";self.assertEqual(db.upsert_kb_resource(**kw),"updated")

if __name__=="__main__":unittest.main()
