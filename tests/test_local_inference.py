import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pydantic import ValidationError
from app.core.database import Database
from app.research.browser import BrowserResearchProvider,SourceAuthority
from app.resolver.engine import ResolverContext,resolve_product
from app.resolver.inference import OllamaInferenceProvider,RuleBasedInferenceProvider,SemanticEnrichment,extract_json_object,indonesian_language_ok,ollama_status,select_inference_provider,semantic_field_errors

def enrichment(**extra):
    value={"short_description":"Pelatihan praktis untuk memahami IoT.","learning_outcomes":["Mampu menggunakan Arduino dan ESP8266."],"target_audiences":[{"audience":"Pemula","problem_solved":"Memahami dasar IoT."}],"prerequisites":["Mampu menggunakan komputer."],"practice_examples":["Membuat kontrol perangkat melalui web."],"selling_points":["Berbasis praktik."],"claims_to_avoid":["Jangan menjanjikan hasil tanpa bukti."]};value.update(extra);return value
class Response:
    def __init__(self,data,status=200,text=None,url="https://www.idn.id/training/x/"):self.data=data;self.status_code=status;self.text=text if text is not None else json.dumps(data);self.url=url
    def json(self):return self.data
    def raise_for_status(self):
        if self.status_code>=400:raise RuntimeError(str(self.status_code))
class Client:
    def __init__(self,posts=None,get_response=None):self.posts=list(posts or []);self.get_response=get_response;self.post_calls=0;self.get_calls=0
    def post(self,*a,**kw):
        self.post_calls+=1;value=self.posts.pop(0)
        if isinstance(value,Exception):raise value
        return value
    def get(self,*a,**kw):self.get_calls+=1;return self.get_response
def settings():return SimpleNamespace(ollama_model="qwen3:4b",ollama_max_retries=3,ollama_base_url="http://127.0.0.1:11434",ollama_timeout_seconds=1,ollama_context_size=4096,ollama_temperature=.2,ollama_enabled=True,crawl_timeout_seconds=2,crawler_user_agent="test")
def input_data():return {"current_payload":{"short_description":"Pelatihan IoT.","learning_outcomes":[],"target_audiences":[],"prerequisites":[],"practice_examples":[],"selling_points":[],"claims_to_avoid":[]},"facts":{}}

class LocalInferenceTests(unittest.TestCase):
    def test_thinking_wrapper_json_extraction(self):self.assertEqual(extract_json_object("Thinking... done\n"+json.dumps(enrichment()))["learning_outcomes"][0],"Mampu menggunakan Arduino dan ESP8266.")
    def test_valid_json_and_cache(self):
        with tempfile.TemporaryDirectory() as d:
            c=Client([Response({"message":{"content":json.dumps(enrichment())}})]);p=OllamaInferenceProvider(settings(),c,Path(d));value,meta=p.enrich(input_data());self.assertTrue(indonesian_language_ok(value));self.assertFalse(meta["cache_hit"]);p.enrich(input_data());self.assertEqual((c.post_calls,p.cache_hits),(1,1))
    def test_malformed_json_retries(self):
        bad=Response({"message":{"content":"not json"}});good=Response({"message":{"content":json.dumps(enrichment())}})
        with tempfile.TemporaryDirectory() as d:
            c=Client([bad,bad,good]);p=OllamaInferenceProvider(settings(),c,Path(d));p.enrich(input_data());self.assertEqual(c.post_calls,3)
    def test_timeout_then_failure_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            c=Client([TimeoutError(),TimeoutError(),TimeoutError()]);p=OllamaInferenceProvider(settings(),c,Path(d))
            with self.assertRaises(RuntimeError):p.enrich(input_data())
            self.assertEqual(c.post_calls,3)
    def test_health_and_model_discovery(self):
        c=Client(get_response=Response({"models":[{"name":"qwen3:4b"}]}));r=ollama_status(settings(),c);self.assertTrue(r["runtime"] and r["model_installed"])
    def test_unavailable_runtime_selects_rule_fallback(self):
        with patch("app.resolver.inference.ollama_status",return_value={"runtime":False,"model_installed":False}):provider,_=select_inference_provider(settings());self.assertIsInstance(provider,RuleBasedInferenceProvider)
    def test_indonesian_and_technical_terms_allowed(self):self.assertTrue(indonesian_language_ok(SemanticEnrichment.model_validate(enrichment())))
    def test_marketing_copy_rejected_as_learning_outcome(self):
        value=SemanticEnrichment.model_validate(enrichment(learning_outcomes=["Kenapa training ini istimewa?","Training ini sangat menarik."]));self.assertIn("learning_outcomes",semantic_field_errors(value))
    def test_banned_hallucination_schema(self):
        with self.assertRaises(ValidationError):SemanticEnrichment.model_validate(enrichment(price=1000,trainer="Random",certification="Fake"))
    def test_source_authority(self):self.assertEqual(SourceAuthority().classify("https://www.cisco.com/x"),"OFFICIAL_VENDOR");self.assertEqual(SourceAuthority().classify("https://random.example/x"),"TRUST_UNKNOWN")
    def test_browser_research_and_cache(self):
        html="<html><title>Training</title><body>Format training Hybrid selama 2 hari.</body></html>"
        with tempfile.TemporaryDirectory() as d,patch("app.research.browser.CACHE",Path(d)):
            c=Client(get_response=Response({},text=html));p=BrowserResearchProvider(settings(),c);r=p.research_field("x","X","training_formats",["https://www.idn.id/training/x/"]);self.assertTrue(r["evidence"]);p.research_field("x","X","training_formats",["https://www.idn.id/training/x/"]);self.assertEqual((c.get_calls,p.cache_hits),(1,1))
    def test_real_ambiguity_is_preserved(self):
        with tempfile.TemporaryDirectory() as d,patch("app.resolver.engine.OUT",Path(d)/"out"):
            db=Database(Path(d)/"x.db");db.initialize_database();r=resolve_product("rcna-wlan-exam",db,ResolverContext());self.assertTrue(r.fields["training_formats"].needs_review);self.assertIn("labels must be reviewed",r.payload.training_formats[0].price_note)

if __name__=="__main__":unittest.main()
