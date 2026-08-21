import json
from datetime import datetime,timezone,timedelta

from app.identity.models import IdentityDecision,MatchMethod
from app.identity.service import audit_duplicates,build_diff,canonical_url,inventory_hash,normalize_name,publisher_preflight,resolve_identity,route_is_fresh

def product(kid,name,seo=None,short=None):
    return {"kb_product_id":kid,"name":name,"short_name":short,"detail_url":f"https://kb.idn.id/kb/training/detail?id={kid}","edit_url":f"https://kb.idn.id/kb/training/edit?id={kid}","seo_url":seo,"canonical_seo_url":canonical_url(seo),"normalized_name":normalize_name(name),"snapshot":{"sections":[]}}
def inventory(*products):
    rows=list(products);return {"generated_at":datetime.now(timezone.utc).isoformat(),"count":len(rows),"products":rows,"inventory_hash":inventory_hash(rows)}
def payload(name,url,short=None):return {"full_name":name,"short_name":short,"seo_url":url}

def test_canonical_url_normalization_preserves_meaningful_query():
    assert canonical_url("HTTPS://www.IDN.id/training/x/?course=1&utm_source=a#top")=="https://idn.id/training/x?course=1"
def test_name_case_dash_ampersand_and_version_normalization():
    assert normalize_name("Cisco CCNA 200 – 301 v1.1")==normalize_name("cisco ccna 200-301 V1.1")
    assert normalize_name("AI & Automation")==normalize_name("AI and Automation")
def test_exact_url_ai_automation_regression():
    p=product("ai","AI & Automation Tools (n8n)","https://www.idn.id/training/ai-automation-tools/")
    r=resolve_identity("ai-automation-tools",payload("AI & Automation Tools","https://idn.id/training/ai-automation-tools"),inventory(p))
    assert r.decision==IdentityDecision.UPDATE_EXISTING and r.match_method==MatchMethod.EXACT_CANONICAL_URL
def test_exact_name_pentest_regression():
    p=product("9196","Basic Penetration Testing")
    r=resolve_identity("pentest",payload("Basic Penetration Testing","https://www.idn.id/training/pentest/"),inventory(p))
    assert r.decision==IdentityDecision.UPDATE_EXISTING and r.match_method==MatchMethod.EXACT_NORMALIZED_NAME
def test_ambiguous_exact_and_alias_never_auto_update():
    inv=inventory(product("1","Cisco CCNA 200-301",short="CCNA"),product("2","Cisco CCNA Bootcamp",short="CCNA"))
    r=resolve_identity("ccna",payload("CCNA","https://idn.id/training/other","CCNA"),inv)
    assert r.decision==IdentityDecision.REVIEW_REQUIRED
def test_fuzzy_candidate_never_auto_updates():
    r=resolve_identity("x",payload("Huawei HCIA Data Communication","https://idn.id/training/x"),inventory(product("1","Huawei HCIA Datacom")))
    assert r.decision==IdentityDecision.REVIEW_REQUIRED and r.match_method==MatchMethod.FUZZY_CANDIDATE
def test_new_product_creates_route_decision():
    r=resolve_identity("new",payload("Entirely New Quantum Course","https://idn.id/training/quantum"),inventory(product("1","Cisco CCNA")))
    assert r.decision==IdentityDecision.CREATE_NEW
def test_duplicate_audit_detects_url_and_name(tmp_path,monkeypatch):
    import app.identity.service as service
    monkeypatch.setattr(service,"DUPLICATES",tmp_path/"audit.json");inv=inventory(product("1","Same","https://idn.id/a"),product("2","Same","https://www.idn.id/a/"))
    result=audit_duplicates(inv);assert {x["reason"] for x in result["duplicate_groups"]}=={"SAME_CANONICAL_URL","SAME_NORMALIZED_NAME"}
def test_payload_empty_preserves_existing_and_high_risk_conflict():
    existing=product("1","X");existing["snapshot"]={"sections":[{"heading":"Kebijakan mengulang training","content":"Dua kali"},{"heading":"Deskripsi singkat","content":"Lama"}]}
    diff=build_diff({"short_description":"","repeat_policy":"Tiga kali"},existing);by={x["field"]:x for x in diff["fields"]}
    assert by["short_description"]["classification"]=="PRESERVE_EXISTING" and by["repeat_policy"]["classification"]=="CONFLICT" and diff["high_risk_conflict"]
def test_stale_route_detection():
    inv=inventory(product("1","X"));fresh={"checked_against_live_kb_at":datetime.now(timezone.utc).isoformat(),"inventory_hash":inv["inventory_hash"]}
    assert route_is_fresh(fresh,inv);fresh["checked_against_live_kb_at"]=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat();assert not route_is_fresh(fresh,inv)

def test_publisher_preflight_requires_route_and_rejects_stale(tmp_path,monkeypatch):
    import app.identity.service as service
    monkeypatch.setattr(service,"ROUTES",tmp_path);inv=inventory(product("1","X"))
    assert publisher_preflight("missing",inv)["reason"]=="ROUTE_MISSING"
    folder=tmp_path/"x";folder.mkdir();route={"checked_against_live_kb_at":(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat(),"inventory_hash":inv["inventory_hash"],"route_ttl_seconds":3600,"publish_readiness":"READY","identity_decision":"CREATE_NEW","target_url":"https://kb.idn.id/kb/training/edit?new=1","publisher_allowed":True}
    (folder/"route.json").write_text(json.dumps(route),encoding="utf-8");assert publisher_preflight("x",inv)["reason"]=="ROUTE_STALE"
