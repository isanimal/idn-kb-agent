import hashlib
import json
from pathlib import Path

from app.core.config import get_settings
from app.quality.models import PublishReadiness
from app.quality.service import run_one
from app.quality.validator import clean_repeat_policy, deduplicate, evaluate, safe_truncate, sanitize_payload, similarity
from app.resolver.models import KBProductPayload, ProductStatus, ResolvedProduct, ResolvedValue, ResolutionMethod, ResolutionStatus, TrainingFormat


def load_product(slug):
    result=ResolvedProduct.model_validate_json(Path(f"data/resolved_products/{slug}/resolved.json").read_text(encoding="utf-8"))
    facts=json.loads(Path(f"data/products/{slug}/facts.json").read_text(encoding="utf-8"))
    return result,facts


def test_repeat_policy_contamination_removed():
    value="Gratis Mengulang training sebanyak 2 kali Lunch & Coffebreak Penginapan Gratis Sertifikat Kaos"
    cleaned=clean_repeat_policy(value)
    assert cleaned=="Gratis Mengulang training sebanyak 2 kali."
    assert not any(x.lower() in cleaned.lower() for x in ("lunch","coffee","penginapan","sertifikat","kaos"))


def test_safe_truncation_never_breaks_word():
    result=safe_truncate("Training khusus " + "kemampuan "*80,100)
    assert len(result)<=101 and result.endswith(".") and not result.endswith("kemamp.")


def test_duplicate_and_similarity_detection():
    kept,removed=deduplicate(["Gratis Mengulang Training 2 Kali", "gratis mengulang training 2 kali!", "Praktik lab"])
    assert kept==["Gratis Mengulang Training 2 Kali","Praktik lab"] and len(removed)==1
    assert similarity("Mengonfigurasi VLAN pada switch", "Melakukan konfigurasi VLAN pada switch")>=.5


def test_robotik_semantic_regressions_and_source_immutable():
    result,facts=load_product("robotik-iot");source=Path("data/resolved_products/robotik-iot/resolved.json");before=hashlib.sha256(source.read_bytes()).hexdigest()
    payload,changes=sanitize_payload(result,facts);report=evaluate(result,payload,facts,changes)
    assert "Lunch" not in payload.repeat_policy
    assert all("curriculum" not in x.lower() for x in payload.learning_outcomes)
    assert all(x.lower().startswith(("melakukan","membuat","mengonfigurasi","menghubungkan","menguji","menganalisis","menggunakan","menambahkan","menyusun","mengendalikan","mengirim","menerapkan")) for x in payload.practice_examples)
    assert not any(similarity(a,b)>=.72 for a in payload.learning_outcomes for b in payload.practice_examples)
    assert payload.short_description.endswith((".","!","?")) and len(payload.short_description)<=350
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert report.publish_readiness in set(PublishReadiness)


def test_pentest_career_claims_removed_and_practice_is_authorized():
    result,facts=load_product("pentest");payload,_=sanitize_payload(result,facts)
    text=" ".join(payload.learning_outcomes).lower()
    assert not any(x in text for x in ("peluang penghasilan","marketability","peluang kerja lebih baik","langsung kerja"))
    assert payload.short_description.lower().startswith("training basic penetration testing")
    assert payload.practice_examples and all(any(x in item.lower() for x in ("lab","diotorisasi","legal")) for item in payload.practice_examples)


def test_commercial_values_are_immutable_and_rcna_ambiguity_retained():
    result,facts=load_product("rcna-wlan-exam");before=result.payload.training_formats[0].model_dump();payload,_=sanitize_payload(result,facts)
    assert payload.training_formats[0].model_dump()==before
    assert "labels must be reviewed" in payload.training_formats[0].price_note
    report=evaluate(result,payload,facts)
    assert report.publish_readiness!=PublishReadiness.READY


def test_blocking_error_for_invalid_url():
    result,facts=load_product("pentest");result.warnings.append("INVALID_URL");payload,changes=sanitize_payload(result,facts);report=evaluate(result,payload,facts,changes)
    assert report.publish_readiness==PublishReadiness.BLOCKED
    assert any(x.code=="INVALID_URL" and x.blocking for x in report.errors)


def test_quality_artifacts_created_separately(tmp_path,monkeypatch):
    import app.quality.service as service
    source_root=tmp_path/"resolved";product_root=tmp_path/"data"/"products";output_root=tmp_path/"ready"
    (source_root/"pentest").mkdir(parents=True);(product_root/"pentest").mkdir(parents=True)
    source=Path("data/resolved_products/pentest/resolved.json");facts=Path("data/products/pentest/facts.json")
    (source_root/"pentest"/"resolved.json").write_bytes(source.read_bytes());(product_root/"pentest"/"facts.json").write_bytes(facts.read_bytes())
    monkeypatch.setattr(service,"RESOLVED_ROOT",source_root);monkeypatch.setattr(service,"OUTPUT_ROOT",output_root)
    monkeypatch.chdir(tmp_path)
    # Service's factual path is workspace-relative; mirror it under the isolated cwd.
    report,trace=run_one("pentest",get_settings(),False)
    assert (output_root/"pentest"/"publish_payload.json").exists()
    assert (output_root/"pentest"/"quality_report.json").exists()
    assert (output_root/"pentest"/"quality_trace.json").exists()
    assert trace["repair"]["calls"]==0 and report.slug=="pentest"


def test_quality_repair_is_single_call_and_cached(tmp_path,monkeypatch):
    import app.quality.service as service
    from app.resolver.inference import SemanticEnrichment
    class Response:
        def __init__(self,data):self.data=data
        def raise_for_status(self):return None
        def json(self):return self.data
    class Client:
        def __init__(self):self.posts=0
        def get(self,*args,**kwargs):return Response({"models":[{"name":get_settings().ollama_model}]})
        def post(self,*args,**kwargs):
            self.posts+=1
            output=SemanticEnrichment(short_description="Training uji membahas praktik sistem.",learning_outcomes=["Mampu memahami sistem."],practice_examples=["Menguji sistem pada lab."],selling_points=["Pembelajaran berbasis praktik."],target_audiences=[{"audience":"Profesional IT","problem_solved":"Mempelajari dasar sistem."}])
            return Response({"message":{"content":output.model_dump_json()}})
    monkeypatch.setattr(service,"CACHE_ROOT",tmp_path);client=Client();input_data={"product":{"name":"Uji"},"factual_evidence":{"curriculum":"sistem lab"},"current_semantic_fields":{},"quality_violations":[{"code":"GENERIC_LOW_INFORMATION"}]}
    first,meta1=service._ollama_repair(get_settings(),input_data,client);second,meta2=service._ollama_repair(get_settings(),input_data,client)
    assert first==second and client.posts==1 and meta1["calls"]==1 and meta2["calls"]==0 and meta2["cache_hit"]
