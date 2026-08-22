import hashlib,json
from dataclasses import dataclass,field
from datetime import datetime,timezone,timedelta
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from app.browser.manager import BrowserManager
from app.candidate.service import PARITY,SECTIONS,_add_row,_ensure,_fill_row,_has_removal,_section,candidate_hash,discover_schema,parse_full,save
from app.identity.service import LIVE,audit_duplicates,canonical_url,load,normalize_name,refresh_live_index,resolve_identity
from app.kb.guard import MUTATING_METHODS,ReadOnlyGuard

ROOT=Path("data/live_candidates");RUNS=Path("data/live_publish")
MAX_AGE_MINUTES=60

class LivePublishError(RuntimeError):pass
class CandidateHashMismatch(LivePublishError):pass
class CandidateChanged(LivePublishError):pass
class CandidateStale(LivePublishError):pass
class IdentityChanged(LivePublishError):pass
class BaselineChanged(LivePublishError):pass
class FinalDOMMismatch(LivePublishError):pass
class SaveControlMissing(LivePublishError):pass
class SaveControlAmbiguous(LivePublishError):pass

class PublishState(str,Enum):
    PREPARING="PREPARING";VALIDATING="VALIDATING";FORM_FILLED="FORM_FILLED";FINAL_VERIFICATION="FINAL_VERIFICATION";ARMED="ARMED";SUBMITTING="SUBMITTING";SUBMITTED="SUBMITTED";VERIFYING="VERIFYING";VERIFIED="VERIFIED"

@dataclass
class PublisherWriteGuard:
    state:PublishState=PublishState.PREPARING
    allowed_host:str="kb.idn.id"
    save_requests:int=0
    blocked:list[dict]=field(default_factory=list)
    network_audit:list[dict]=field(default_factory=list)
    def install(self,context):
        def handler(route,request):
            host=(urlsplit(request.url).hostname or "").lower();method=request.method.upper()
            if host==self.allowed_host and method in MUTATING_METHODS:
                item={"method":method,"host":host,"path":urlsplit(request.url).path}
                if self.state==PublishState.ARMED and self.save_requests==0:
                    self.save_requests=1;self.state=PublishState.SUBMITTING;self.network_audit.append(item);route.continue_();return
                self.blocked.append(item);route.abort("blockedbyclient");return
            route.continue_()
        context.route("**/*",handler)
        context.on("response",lambda response:self._response(response))
    def _response(self,response):
        url=urlsplit(response.url)
        for item in reversed(self.network_audit):
            if item["host"]==(url.hostname or "").lower() and item["path"]==url.path and "status" not in item:item["status"]=response.status;break
    def arm(self):
        if self.state!=PublishState.FINAL_VERIFICATION:raise LivePublishError("WRITE_GUARD_NOT_FINAL")
        self.state=PublishState.ARMED

def _sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def _artifacts(slug):
    folder=ROOT/slug
    return {name:load(folder/name) for name in ("candidate_report.json","candidate_payload.json","candidate_diff.json","candidate_route.json","baseline_hash.json")}
def validate_frozen(slug,supplied,now=None):
    a=_artifacts(slug);report=a["candidate_report.json"];stored=report["candidate_hash"]
    if supplied!=stored:raise CandidateHashMismatch("CandidateHashMismatch")
    recomputed=candidate_hash(slug,a["candidate_route.json"],a["candidate_payload.json"],a["candidate_diff.json"],a["baseline_hash.json"]["baseline_hash"],a["baseline_hash.json"]["inventory_hash"])
    if stored!=recomputed:raise CandidateChanged("CANDIDATE_CHANGED")
    generated=datetime.fromisoformat(report["generated_at"]);now=now or datetime.now(timezone.utc);age=now-generated
    if age>timedelta(minutes=MAX_AGE_MINUTES):raise CandidateStale("CANDIDATE_STALE")
    if report["live_candidate_readiness"]!="READY" or report["effective_conflicts"] or report["round_trip"]!="PASS" or report["content_regression"] or report["unexpected_removal"]:raise LivePublishError("CANDIDATE_NOT_READY")
    return a,age
def _identity(slug,payload,inventory,route):
    result=resolve_identity(slug,payload,inventory)
    if result.decision.value!=route["identity_decision"]:raise IdentityChanged("IDENTITY_CHANGED")
    if route["identity_decision"]=="UPDATE_EXISTING" and (not result.existing_product or result.existing_product["kb_product_id"]!=route["kb_product_id"]):raise IdentityChanged("IDENTITY_CHANGED")
    return result
def _save_control(page):
    # The current React form exposes two equivalent Save buttons. The footer
    # control is semantically disambiguated by its unique sibling, Batal.
    footer=page.get_by_role("button",name="Batal",exact=True)
    if footer.count()>1:raise SaveControlAmbiguous("SAVE_CONTROL_AMBIGUOUS")
    controls=footer.first.locator("xpath=..").get_by_role("button",name="Simpan",exact=True) if footer.count()==1 else page.get_by_role("button",name="Simpan",exact=True)
    if controls.count()==0:raise SaveControlMissing("SAVE_CONTROL_MISSING")
    if controls.count()>1:raise SaveControlAmbiguous("SAVE_CONTROL_AMBIGUOUS")
    return controls.first
def _schema_compatible(old,new):
    return all(old["sections"].get(k,{}).get("labels")==v.get("labels") and not v.get("schema_ambiguous") for k,v in new["sections"].items())
def live_preflight(slug,supplied_hash,settings,refresh=True):
    a,age=validate_frozen(slug,supplied_hash);payload=a["candidate_payload.json"];route=a["candidate_route.json"]
    inventory=refresh_live_index(settings) if refresh else load(LIVE);identity=_identity(slug,payload,inventory,route);manager=BrowserManager(settings.browser_profile_path,False);guard=ReadOnlyGuard()
    try:
        manager.start();guard.install(manager.context);page=manager.new_page();_ensure(page,route["target_url"]);schema=discover_schema(page)
        if any(x["client_side_probe_added"] for x in schema["sections"].values()):_ensure(page,route["target_url"])
        baseline=parse_full(page,schema,load("data/kb_site_models/kb_training_form_schema.json").get("trainer_options",[]));current_hash=_sha(baseline);stored_hash=a["baseline_hash.json"]["baseline_hash"]
        if current_hash!=stored_hash:raise BaselineChanged("BASELINE_CHANGED")
        if not _schema_compatible(load("data/publisher_models/live_form_schema.json"),schema):raise LivePublishError("SCHEMA_INCOMPATIBLE")
        _save_control(page)
        if guard.blocked:raise LivePublishError("UNEXPECTED_SERVER_MUTATION")
        return {"product":payload["full_name"],"slug":slug,"mode":route["identity_decision"],"candidate_hash":supplied_hash,"candidate_age_seconds":int(age.total_seconds()),"hash_supplied":"MATCH","hash_recomputed":"MATCH","kb_product_id":route.get("kb_product_id"),"identity":"MATCH","duplicate_check":"PASS","stored_baseline_hash":stored_hash,"current_baseline_hash":current_hash,"baseline_match":True,"schema":"COMPATIBLE","round_trip":"PASS","regression":"NONE","effective_conflicts":0,"deferred_relations":a["candidate_report.json"]["deferred_relations_count"],"old_inventory_hash":a["baseline_hash.json"]["inventory_hash"],"new_inventory_hash":inventory["inventory_hash"],"result":"ARMED_CANDIDATE_READY","server_writes":0}
    finally:manager.stop()

def _apply_candidate(page,baseline,candidate,diff,trainer_options):
    deferred={x["field"] for x in diff["fields"] if x["action"]=="DEFERRED_RELATION"}
    for field,(label,_) in PARITY.items():
        if field in SECTIONS or field in {"trainer_references","active"}:continue
        if baseline[field]==candidate[field]:continue
        control=page.get_by_label(label,exact=False).first;value="\n".join(candidate[field]) if isinstance(candidate[field],list) else candidate[field]
        control.select_option(label=str(value)) if field=="category" else control.fill(str(value))
    for field in SECTIONS:
        if field in deferred:continue
        section=_section(page,SECTIONS[field][0]);rows=section.locator(".entry-row")
        for i,item in enumerate(candidate[field]):
            row=rows.nth(i) if i<rows.count() else _add_row(page,field)
            _fill_row(row,[item.get(k) for k in SECTIONS[field][1]])
    by_id={x["value"]:x["display_name"] for x in trainer_options};selected={x["kb_trainer_id"] for x in baseline["trainer_references"]};trigger=page.get_by_label("Trainer referensi",exact=False).first
    for item in candidate["trainer_references"]:
        if item["kb_trainer_id"] in selected:continue
        trigger.click();page.get_by_text(by_id[item["kb_trainer_id"]],exact=True).click();selected.add(item["kb_trainer_id"])
    if page.get_by_label(PARITY["active"][0],exact=False).first.is_checked()!=candidate["active"]:page.get_by_label(PARITY["active"][0],exact=False).first.click()

def publish_live(slug,supplied_hash,confirm_write,settings):
    if not confirm_write:raise LivePublishError("EXPLICIT_WRITE_CONFIRMATION_REQUIRED")
    pre=live_preflight(slug,supplied_hash,settings,refresh=True);a=_artifacts(slug);route=a["candidate_route.json"];run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+f"-{slug}-{supplied_hash[:8]}";folder=RUNS/slug/run_id
    inventory=load(LIVE);manager=BrowserManager(settings.browser_profile_path,False);guard=PublisherWriteGuard();save_clicks=0
    try:
        manager.start();guard.install(manager.context);page=manager.new_page();_ensure(page,route["target_url"]);schema=discover_schema(page)
        if any(x["client_side_probe_added"] for x in schema["sections"].values()):_ensure(page,route["target_url"])
        trainers=load("data/kb_site_models/kb_training_form_schema.json").get("trainer_options",[]);baseline=parse_full(page,schema,trainers)
        if _sha(baseline)!=a["baseline_hash.json"]["baseline_hash"]:raise BaselineChanged("BASELINE_CHANGED")
        for name,value in (("candidate_report.json",a["candidate_report.json"]),("candidate_payload.json",a["candidate_payload.json"]),("candidate_diff.json",a["candidate_diff.json"]),("pre_write_form.json",baseline),("pre_write_target_snapshot.json",next(x for x in inventory["products"] if x["kb_product_id"]==route["kb_product_id"])),("pre_write_inventory.json",inventory),("pre_write_hashes.json",a["baseline_hash.json"])):save(folder/name,{"publish_run_id":run_id,"data":value})
        guard.state=PublishState.VALIDATING;_apply_candidate(page,baseline,a["candidate_payload.json"],a["candidate_diff.json"],trainers);guard.state=PublishState.FORM_FILLED
        final=parse_full(page,schema,trainers)
        for d in a["candidate_diff.json"]["fields"]:
            if d["field"] in {x["field"] for x in a["candidate_diff.json"]["fields"] if x["action"]=="DEFERRED_RELATION"}:continue
            if final[d["field"]]!=a["candidate_payload.json"][d["field"]]:raise FinalDOMMismatch("FINAL_DOM_MISMATCH")
            if _has_removal(d["field"],baseline[d["field"]],final[d["field"]]):raise LivePublishError("UNEXPECTED_REMOVAL")
        guard.state=PublishState.FINAL_VERIFICATION;button=_save_control(page);guard.arm()
        print(f"LIVE WRITE ARMED\n\nProduct: {a['candidate_payload.json']['full_name']}\nMode: {route['identity_decision']}\nKB Product ID: {route.get('kb_product_id')}\nCandidate Hash: {supplied_hash}\nDeferred relations: {a['candidate_report.json']['deferred_relations_count']}\nCONFIRMATION FLAG: VALID",flush=True)
        uncertain=False
        try:button.click();save_clicks=1
        except Exception:
            # A timeout after the request may still mean persistence succeeded.
            # Never click again; verification below is the only recovery path.
            save_clicks=1;uncertain=True
        page.wait_for_timeout(1500);guard.state=PublishState.SUBMITTED
        guard.state=PublishState.VERIFYING;_ensure(page,route["target_url"]);actual=parse_full(page,discover_schema(page),trainers)
        post=[]
        for d in a["candidate_diff.json"]["fields"]:
            status="DEFERRED" if d["action"]=="DEFERRED_RELATION" else "PERSISTED" if actual[d["field"]]==d["after"] else "PRESERVED" if d["action"] in {"PRESERVE_EXISTING","UNCHANGED"} and actual[d["field"]]==d["before"] else "MISMATCH"
            post.append({"field":d["field"],"expected_before":d["before"],"expected_after":d["after"],"actual_after":actual[d["field"]],"status":status,"match":status!="MISMATCH"})
        save(folder/"post_write_diff.json",{"publish_run_id":run_id,"fields":post});guard.state=PublishState.VERIFIED if all(x["match"] for x in post) else PublishState.VERIFYING
        manager.stop();post_inventory=refresh_live_index(settings);duplicate_report=audit_duplicates(post_inventory);target_rows=[x for x in post_inventory["products"] if x["kb_product_id"]==route.get("kb_product_id")];target_ok=len(target_rows)==1
        duplicate_error=any(any(x["kb_product_id"]==route.get("kb_product_id") for x in group["products"]) for group in duplicate_report["duplicate_groups"]);count_ok=post_inventory["count"]==inventory["count"] if route["identity_decision"]=="UPDATE_EXISTING" else post_inventory["count"]==inventory["count"]+1
        verified=guard.state==PublishState.VERIFIED and target_ok and not duplicate_error
        result="PASS" if verified else "VERIFIED_WITH_DUPLICATE_ERROR" if duplicate_error else "MANUAL_REVIEW_REQUIRED"
        report={"publish_run_id":run_id,"slug":slug,"mode":route["identity_decision"],"candidate_hash":supplied_hash,"state":"VERIFIED" if verified else guard.state.value,"save_clicks":save_clicks,"submission":{"status":"SUCCESS" if verified else "SUBMISSION_UNCERTAIN" if uncertain else "MANUAL_REVIEW_REQUIRED","network":guard.network_audit},"verification":{"edit_form":"PASS" if all(x["match"] for x in post) else "FAIL","target_id":"PASS" if target_ok else "FAIL","duplicate_check":"PASS" if not duplicate_error else "FAIL","before_count":inventory["count"],"after_count":post_inventory["count"],"count_expectation":"PASS" if count_ok else "WARNING"},"server_write_count":guard.save_requests,"result":result};save(folder/"publish_report.json",report);return report
    finally:manager.stop()
