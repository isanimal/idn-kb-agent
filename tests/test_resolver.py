import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app.core.database import Database
from app.research.provider import OpenAIResearchProvider,ResearchCache
from app.resolver.engine import ResolverContext,canonical_url,norm,resolve_product,rv,na
from app.resolver.models import KBProductPayload,ResearchResult,ResolutionStatus,TrainingFormat

class ResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.ctx=ResolverContext()
    def test_exact_product_url_matching(self):
        status,product,method=self.ctx.match("https://www.idn.id/training/ai-automation-tools/","wrong")
        self.assertEqual((status,method),("MATCHED","EXACT_URL"));self.assertIn("Automation",product["name"])
    def test_category_is_restricted_to_kb_taxonomy(self):
        category,_,_=self.ctx.category("IOT & Robotik","Robotik + IoT",None);self.assertIn(category,self.ctx.categories);self.assertEqual(category,"IoT & Robotik")
    def test_trainer_never_inferred_from_expertise(self):
        with tempfile.TemporaryDirectory() as d:
            db=Database(Path(d)/"x.db");db.initialize_database()
            with patch("app.resolver.engine.OUT",Path(d)/"out"):result=resolve_product("robotik-iot",db,self.ctx)
            self.assertEqual(result.fields["trainer_references"].status,ResolutionStatus.NOT_APPLICABLE);self.assertEqual(result.payload.trainer_references,[])
    def test_current_idn_commercial_fact_wins_recorded_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            db=Database(Path(d)/"x.db");db.initialize_database()
            with patch("app.resolver.engine.OUT",Path(d)/"out"):result=resolve_product("ai-automation-tools",db,self.ctx)
            for conflict in result.source_conflicts:self.assertEqual(conflict["selected"],"IDN_PRIMARY")
            self.assertTrue(all(x["status"]=="SOURCE_CONFLICT" for x in result.source_conflicts))
    def test_not_applicable_counts_complete(self):self.assertEqual(na([]).status,ResolutionStatus.NOT_APPLICABLE)
    def test_publish_payload_serialization(self):
        p=KBProductPayload(full_name="X",short_name="X",category=self.ctx.categories[0],seo_url="https://idn.id/x",training_formats=[TrainingFormat(format="Offline")],short_description="X",repeat_policy="Confirm",post_training_support="Confirm")
        self.assertEqual(KBProductPayload.model_validate_json(p.model_dump_json()).full_name,"X")
    def test_database_checkpoint_idempotency(self):
        with tempfile.TemporaryDirectory() as d:
            db=Database(Path(d)/"x.db");db.initialize_database();kw=dict(slug="x",status="RESOLVED",resolution_hash="h",resolved_path="a",publish_payload_path="b",completion=100,needs_review=False,source_url="https://idn.id/x")
            self.assertEqual(db.upsert_resolved_product(**kw),"new");self.assertEqual(db.upsert_resolved_product(**kw),"unchanged")
    def test_research_cache(self):
        with tempfile.TemporaryDirectory() as d:
            c=ResearchCache(Path(d));key=c.key("p","f","q","h");v=ResearchResult(query="q",field="f",answer="a",source_authority="OFFICIAL",retrieved_at="now",confidence=.9);c.put(key,v)
            self.assertEqual(c.get(key).answer,"a");self.assertEqual(c.hits,1)
    def test_research_retry_and_structured_output(self):
        result=ResearchResult(query="q",field="f",answer="a",source_authority="OFFICIAL",retrieved_at="now",confidence=.9)
        class Responses:
            def __init__(self):self.n=0
            def parse(self,**kw):
                self.n+=1
                if self.n<3:raise TimeoutError("temporary")
                return SimpleNamespace(output_parsed=result)
        responses=Responses();settings=SimpleNamespace(openai_api_key="test",openai_model="gpt-5.4",openai_web_search_enabled=True,research_max_searches_per_product=3,research_max_retries=3,research_cache_days=30)
        with tempfile.TemporaryDirectory() as d:
            provider=OpenAIResearchProvider(settings,SimpleNamespace(responses=responses),ResearchCache(Path(d)));self.assertEqual(provider.research(product="p",field="f",query="q",input_hash="h").answer,"a");self.assertEqual(responses.n,3)

if __name__=="__main__":unittest.main()
