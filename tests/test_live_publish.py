import json
from datetime import datetime,timezone,timedelta

import pytest

from app.candidate.service import candidate_hash
from app.live_publish import service


def _write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding="utf-8")


@pytest.fixture
def frozen(tmp_path,monkeypatch):
    monkeypatch.setattr(service,"ROOT",tmp_path)
    slug="p";folder=tmp_path/slug;payload={"full_name":"Product"};diff={"fields":[]};route={"kb_product_id":"id-1","target_url":"https://kb.idn.id/kb/training/edit?id=id-1","identity_decision":"UPDATE_EXISTING"};base={"baseline_hash":"base","inventory_hash":"inventory"}
    digest=candidate_hash(slug,route,payload,diff,"base","inventory")
    report={"candidate_hash":digest,"generated_at":datetime.now(timezone.utc).isoformat(),"live_candidate_readiness":"READY","effective_conflicts":0,"round_trip":"PASS","content_regression":False,"unexpected_removal":False,"deferred_relations_count":0}
    for name,value in (("candidate_report.json",report),("candidate_payload.json",payload),("candidate_diff.json",diff),("candidate_route.json",route),("baseline_hash.json",base)):_write(folder/name,value)
    return slug,digest,folder


def test_exact_candidate_hash_is_required(frozen):
    slug,_,_=frozen
    with pytest.raises(service.CandidateHashMismatch):service.validate_frozen(slug,"wrong")


def test_candidate_hash_is_recomputed(frozen):
    slug,digest,folder=frozen
    value=json.loads((folder/"candidate_payload.json").read_text());value["full_name"]="Changed";_write(folder/"candidate_payload.json",value)
    with pytest.raises(service.CandidateChanged):service.validate_frozen(slug,digest)


def test_stale_candidate_is_blocked(frozen):
    slug,digest,_=frozen
    with pytest.raises(service.CandidateStale):service.validate_frozen(slug,digest,datetime.now(timezone.utc)+timedelta(minutes=61))


def test_unresolved_conflict_is_blocked(frozen):
    slug,digest,folder=frozen
    report=json.loads((folder/"candidate_report.json").read_text());report["effective_conflicts"]=1;_write(folder/"candidate_report.json",report)
    with pytest.raises(service.LivePublishError):service.validate_frozen(slug,digest)


def test_baseline_hash_changes_when_server_state_changes():
    assert service._sha({"field":"old"})!=service._sha({"field":"new"})


def test_write_guard_only_arms_from_final_verification():
    guard=service.PublisherWriteGuard()
    with pytest.raises(service.LivePublishError):guard.arm()
    guard.state=service.PublishState.FINAL_VERIFICATION;guard.arm();assert guard.state==service.PublishState.ARMED


def test_write_guard_allows_exactly_one_mutation_and_never_retries():
    handlers={}
    class Context:
        def route(self,pattern,handler):handlers["handler"]=handler
        def on(self,*args):pass
    class Request:
        method="POST";url="https://kb.idn.id/kb/training/edit"
    class Route:
        def __init__(self):self.result=None
        def continue_(self):self.result="continued"
        def abort(self,*args):self.result="blocked"
    guard=service.PublisherWriteGuard(state=service.PublishState.ARMED);guard.install(Context());first=Route();handlers["handler"](first,Request());second=Route();handlers["handler"](second,Request())
    assert first.result=="continued" and second.result=="blocked" and guard.save_requests==1


def test_network_audit_is_sanitized():
    guard=service.PublisherWriteGuard(state=service.PublishState.ARMED);handlers={}
    class Context:
        def route(self,p,h):handlers["h"]=h
        def on(self,*args):pass
    class Request:method="PATCH";url="https://kb.idn.id/kb/training/edit?id=secret"
    class Route:
        def continue_(self):pass
        def abort(self,*args):pass
    guard.install(Context());handlers["h"](Route(),Request())
    assert guard.network_audit==[{"method":"PATCH","host":"kb.idn.id","path":"/kb/training/edit"}]


def test_update_identity_must_keep_same_target_id():
    route={"identity_decision":"UPDATE_EXISTING","kb_product_id":"expected"}
    inventory={"products":[{"kb_product_id":"other","name":"Product","normalized_name":"product","canonical_seo_url":None}]}
    with pytest.raises(service.IdentityChanged):service._identity("p",{"full_name":"Product","seo_url":"https://idn.id/missing"},inventory,route)

