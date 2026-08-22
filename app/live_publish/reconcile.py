import hashlib
from datetime import datetime,timezone
from pathlib import Path

from app.browser.manager import BrowserManager
from app.candidate.service import SECTIONS,_ensure,discover_schema,normalize_dynamic_state,parse_full,save
from app.identity.service import load
from app.kb.guard import ReadOnlyGuard

ROOT=Path("data/live_publish")

class ReconciliationError(RuntimeError):pass

def _payload(document):return document.get("data",document)
def _digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def _folder(slug,run_id):
    folder=(ROOT/slug/run_id).resolve();root=(ROOT/slug).resolve()
    if root not in folder.parents:raise ReconciliationError("INVALID_RUN_ID")
    if not folder.exists():raise ReconciliationError("LIVE_RUN_NOT_FOUND")
    return folder

def reconcile_live_run(slug,run_id,settings):
    folder=_folder(slug,run_id);original_report=folder/"publish_report.json";original_diff=folder/"post_write_diff.json";immutable={str(p):_digest(p) for p in (original_report,original_diff)}
    report=load(original_report);write_time_diff=load(original_diff);diff=_payload(load(folder/"candidate_diff.json"));target=_payload(load(folder/"pre_write_target_snapshot.json"));trainers=load("data/kb_site_models/kb_training_form_schema.json").get("trainer_options",[])
    manager=BrowserManager(settings.browser_profile_path,False);guard=ReadOnlyGuard()
    try:
        manager.start();guard.install(manager.context);page=manager.new_page();_ensure(page,target["edit_url"]);schema=discover_schema(page)
        if any(x["client_side_probe_added"] for x in schema["sections"].values()):_ensure(page,target["edit_url"])
        raw=parse_full(page,schema,trainers,normalize_dynamic=False);actual=parse_full(page,schema,trainers,normalize_dynamic=True)
        if guard.blocked:raise ReconciliationError("UNEXPECTED_MUTATION")
    finally:manager.stop()
    fields=[];corrections=[];write_time_by_field={x["field"]:x for x in write_time_diff["fields"]}
    for item in diff["fields"]:
        field=item["field"];expected=item["after"]
        if field in SECTIONS:
            expected=normalize_dynamic_state(field,expected);raw_value=raw[field];normalized=actual[field];historical_raw=write_time_by_field.get(field,{}).get("actual_after",raw_value);historical_normalized=normalize_dynamic_state(field,historical_raw)
            if historical_raw!=historical_normalized:corrections.append({"field":field,"reason":"EMPTY_DEFAULT_ROW_OR_PLACEHOLDER","raw":historical_raw,"normalized":historical_normalized,"evidence":"ORIGINAL_POST_WRITE_DIFF"})
            elif raw_value!=normalized:corrections.append({"field":field,"reason":"EMPTY_DEFAULT_ROW_OR_PLACEHOLDER","raw":raw_value,"normalized":normalized,"evidence":"CURRENT_EDIT_FORM"})
        else:raw_value=actual[field];normalized=actual[field]
        deferred=item["action"]=="DEFERRED_RELATION";match=True if deferred else expected==normalized
        fields.append({"field":field,"expected":expected,"actual_raw":raw_value,"actual_normalized":normalized,"status":"DEFERRED" if deferred else "PERSISTED" if match else "MISMATCH","match":match})
    mismatched=sum(not x["match"] for x in fields);deferred=sum(x["status"]=="DEFERRED" for x in fields);effective="VERIFIED" if not mismatched else "MANUAL_REVIEW_REQUIRED"
    reconciled={"publish_run_id":run_id,"fields":fields};reconciliation={"publish_run_id":run_id,"original_result":report["result"],"write":{"method":report["submission"]["network"][0]["method"],"status":report["submission"]["network"][0].get("status"),"save_clicks":report["save_clicks"]},"normalization_corrections":corrections,"verification":{"fields_total":len(fields),"matched":len(fields)-mismatched,"mismatched":mismatched,"deferred":deferred},"target_id":report["verification"]["target_id"],"duplicate_check":report["verification"]["duplicate_check"],"count":{"before":report["verification"]["before_count"],"after":report["verification"]["after_count"]},"effective_publish_status":effective,"additional_server_writes":0,"result":effective}
    save(folder/"post_write_diff_reconciled.json",reconciled);save(folder/"reconciliation_report.json",reconciliation)
    if effective=="VERIFIED":save(folder/"VERIFIED.json",{"publish_run_id":run_id,"verified_at":datetime.now(timezone.utc).isoformat(),"verification_method":"POST_WRITE_READ_ONLY_RECONCILIATION","effective_result":"VERIFIED","server_writes":report["server_write_count"],"additional_server_writes":0})
    if any(_digest(Path(path))!=digest for path,digest in immutable.items()):raise ReconciliationError("ORIGINAL_AUDIT_MODIFIED")
    return reconciliation

def live_run_report(slug,run_id):
    folder=_folder(slug,run_id);original=load(folder/"publish_report.json");reconciliation=load(folder/"reconciliation_report.json")
    return {"original":original,"reconciliation":reconciliation}
