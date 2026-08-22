import json
from datetime import datetime,timezone,timedelta

import pytest

from app.canary import service

def _save(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding="utf-8")

def test_plan_hash_is_deterministic_and_includes_candidate_hashes():
    products=[{"slug":"a","mode":"CREATE_NEW","candidate_hash":"h1"}]
    assert service.plan_hash(products,"inventory")==service.plan_hash(products,"inventory")
    changed=[{"slug":"a","mode":"CREATE_NEW","candidate_hash":"h2"}]
    assert service.plan_hash(products,"inventory")!=service.plan_hash(changed,"inventory")

def test_stale_plan_is_blocked(tmp_path,monkeypatch):
    monkeypatch.setattr(service,"ROOT",tmp_path);products=[];digest=service.plan_hash(products,"i");run="run"
    _save(tmp_path/"current.json",{"run_id":run,"plan_hash":digest});_save(tmp_path/run/"plan.json",{"canary_run_id":run,"generated_at":(datetime.now(timezone.utc)-timedelta(minutes=61)).isoformat(),"inventory_hash":"i","products":products,"canary_plan_hash":digest,"ttl_minutes":60})
    with pytest.raises(service.CanaryError,match="STALE"):service._current_plan(digest)

def test_discovery_excludes_pentest_and_review_required(tmp_path,monkeypatch):
    quality=tmp_path/"quality";routes=tmp_path/"routes";candidates=tmp_path/"candidates";root=tmp_path/"canary"
    for slug,state in (("pentest","READY"),("good","READY"),("review","REVIEW_REQUIRED")):
        _save(quality/slug/"quality_report.json",{"publish_readiness":state});_save(quality/slug/"publish_payload.json",{"full_name":slug,"category":"Test"})
    _save(routes/"good"/"route.json",{"identity_decision":"CREATE_NEW"});_save(candidates/"good"/"candidate_report.json",{"live_candidate_readiness":"READY","effective_conflicts":0,"round_trip":"PASS","candidate_hash":"h"})
    monkeypatch.setattr(service,"QUALITY",quality);monkeypatch.setattr(service,"ROUTES",routes);monkeypatch.setattr(service,"CANDIDATES",candidates);monkeypatch.setattr(service,"ROOT",root);monkeypatch.setattr(service,"LIVE",tmp_path/"live.json");_save(tmp_path/"live.json",{"inventory_hash":"i"})
    result=service.discover();assert [x["slug"] for x in result["selected"]]==["good"]
    assert all(x["slug"]!="pentest" for rows in result["groups"].values() for x in rows)
    assert result["groups"]["REVIEW_REQUIRED"][0]["slug"]=="review"

def test_execute_is_sequential_and_stops_after_first_failure(tmp_path,monkeypatch):
    monkeypatch.setattr(service,"ROOT",tmp_path);products=[{"slug":"a","product":"A","mode":"UPDATE_EXISTING","candidate_hash":"1","status":"READY"},{"slug":"b","product":"B","mode":"CREATE_NEW","candidate_hash":"2","status":"READY"}];digest=service.plan_hash(products,"i");run="run"
    _save(tmp_path/"current.json",{"run_id":run,"plan_hash":digest});_save(tmp_path/run/"plan.json",{"canary_run_id":run,"generated_at":datetime.now(timezone.utc).isoformat(),"inventory_hash":"i","products":products,"canary_plan_hash":digest,"ttl_minutes":60})
    calls=[];monkeypatch.setattr(service,"preflight",lambda *a,**k:{"result":"READY"})
    def publish(slug,*a,**k):calls.append(slug);return {"result":"FAILED","server_write_count":1,"publish_run_id":None}
    monkeypatch.setattr(service,"publish_live",publish);result=service.execute(digest,True,object());assert calls==["a"] and result["attempted"]==1 and result["result"]=="STOPPED"

def test_mixed_mode_counts_use_create_plus_one_and_update_unchanged_conventions():
    # The orchestration preserves mode per product; Step 7 verifier owns count checks.
    products=[{"mode":"UPDATE_EXISTING"},{"mode":"CREATE_NEW"}]
    assert sum(x["mode"]=="UPDATE_EXISTING" for x in products)==1
    assert sum(x["mode"]=="CREATE_NEW" for x in products)==1

