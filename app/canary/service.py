import hashlib,json
from datetime import datetime,timezone,timedelta
from pathlib import Path

from app.identity.service import LIVE,load,refresh_live_index
from app.live_publish.reconcile import reconcile_live_run
from app.live_publish.service import live_preflight,publish_live,validate_frozen

ROOT=Path("data/canary");QUALITY=Path("data/publish_ready");ROUTES=Path("data/publish_routes");CANDIDATES=Path("data/live_candidates")
MAX_AGE_MINUTES=60

class CanaryError(RuntimeError):pass

def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def plan_hash(products,inventory_hash):
    body={"products":[{"slug":x["slug"],"mode":x["mode"],"candidate_hash":x["candidate_hash"]} for x in products],"inventory_hash":inventory_hash}
    return hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
def _status(slug):
    q=QUALITY/slug/"quality_report.json";route=ROUTES/slug/"route.json";candidate=CANDIDATES/slug/"candidate_report.json";reasons=[]
    if not q.exists():return {"slug":slug,"group":"NOT_READY","reasons":["QUALITY_MISSING"]}
    quality=load(q)
    if quality.get("publish_readiness")!="READY":return {"slug":slug,"group":"REVIEW_REQUIRED","reasons":["QUALITY_REVIEW_REQUIRED"]}
    if not route.exists():return {"slug":slug,"group":"NOT_READY","reasons":["ROUTE_MISSING"]}
    r=load(route);mode=r.get("identity_decision")
    if mode not in {"UPDATE_EXISTING","CREATE_NEW"}:reasons.append("IDENTITY_REVIEW_REQUIRED")
    if mode=="UPDATE_EXISTING":
        merge=Path("data/merge_ready")/slug/"merge_report.json"
        if not merge.exists() or load(merge).get("merge_readiness")!="READY":reasons.append("MERGE_NOT_READY")
    if not candidate.exists():reasons.append("CANDIDATE_MISSING")
    else:
        c=load(candidate)
        if c.get("live_candidate_readiness")!="READY" or c.get("effective_conflicts") or c.get("round_trip")!="PASS":reasons.append("CANDIDATE_NOT_READY")
    return {"slug":slug,"product":quality.get("product_name") or load(QUALITY/slug/"publish_payload.json").get("full_name",slug),"mode":mode,"group":mode if not reasons else "NOT_READY","reasons":reasons,"candidate_hash":load(candidate).get("candidate_hash") if candidate.exists() else None,"category":load(QUALITY/slug/"publish_payload.json").get("category")}
def discover(create_plan=True):
    slugs=sorted(x.name for x in QUALITY.iterdir() if x.is_dir() and x.name!="pentest");rows=[_status(x) for x in slugs];eligible=[x for x in rows if x["group"] in {"UPDATE_EXISTING","CREATE_NEW"}]
    # Balance modes first, then category diversity; never exceed five.
    selected=[]
    for mode in ("UPDATE_EXISTING","CREATE_NEW"):
        for row in [x for x in eligible if x["mode"]==mode][:2]:selected.append(row)
    for row in eligible:
        if len(selected)>=5:break
        if row not in selected:selected.append(row)
    result={"generated_at":datetime.now(timezone.utc).isoformat(),"groups":{g:[x for x in rows if x["group"]==g] for g in ("UPDATE_EXISTING","CREATE_NEW","REVIEW_REQUIRED","NOT_READY")},"selected":selected,"server_writes":0}
    if create_plan and selected:
        inventory=load(LIVE);run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-canary");products=[{"slug":x["slug"],"product":x["product"],"mode":x["mode"],"candidate_hash":x["candidate_hash"],"status":"READY"} for x in selected];updates=sum(x["mode"]=="UPDATE_EXISTING" for x in products);creates=sum(x["mode"]=="CREATE_NEW" for x in products);digest=plan_hash(products,inventory["inventory_hash"]);plan={"schema_version":"canary-plan-v1","canary_run_id":run_id,"generated_at":result["generated_at"],"inventory_hash":inventory["inventory_hash"],"products":products,"population":{"updates":updates,"creates":creates,"minimum_updates":2,"minimum_creates":1,"sufficient":updates>=2 and creates>=1},"canary_plan_hash":digest,"ttl_minutes":MAX_AGE_MINUTES};folder=ROOT/run_id;save(folder/"plan.json",plan);save(ROOT/"current.json",{"run_id":run_id,"plan_hash":digest});result["plan"]=plan
    return result
def _current_plan(supplied):
    pointer=load(ROOT/"current.json");plan=load(ROOT/pointer["run_id"]/"plan.json")
    if supplied!=plan["canary_plan_hash"] or supplied!=plan_hash(plan["products"],plan["inventory_hash"]):raise CanaryError("CANARY_PLAN_HASH_MISMATCH")
    if datetime.now(timezone.utc)-datetime.fromisoformat(plan["generated_at"])>timedelta(minutes=plan["ttl_minutes"]):raise CanaryError("CANARY_PLAN_STALE")
    return plan,ROOT/pointer["run_id"]
def preflight(supplied,settings):
    plan,folder=_current_plan(supplied);inventory=refresh_live_index(settings);results=[]
    for product in plan["products"]:
        validate_frozen(product["slug"],product["candidate_hash"]);r=live_preflight(product["slug"],product["candidate_hash"],settings,refresh=False);results.append({"slug":product["slug"],"product":product["product"],"mode":product["mode"],"candidate":"READY","identity":r["identity"],"baseline":r["baseline_match"],"duplicate_check":r["duplicate_check"],"result":r["result"]})
    product_ready=all(x["result"]=="ARMED_CANDIDATE_READY" for x in results);population=plan.get("population",{});sufficient=bool(population.get("sufficient"));report={"canary_run_id":plan["canary_run_id"],"plan_hash":supplied,"generated_at":datetime.now(timezone.utc).isoformat(),"products":results,"population":population,"inventory_hash":inventory["inventory_hash"],"result":"READY" if product_ready and sufficient else "POPULATION_INSUFFICIENT" if product_ready else "BLOCKED","server_writes":0};save(folder/"preflight.json",report);return report
def execute(supplied,confirm_write,settings):
    if not confirm_write:raise CanaryError("EXPLICIT_WRITE_CONFIRMATION_REQUIRED")
    plan,folder=_current_plan(supplied);check=preflight(supplied,settings)
    if check["result"]!="READY":raise CanaryError(check["result"])
    rows=[];writes=0
    for product in plan["products"]: # Deliberately sequential; no executor/concurrency.
        result=publish_live(product["slug"],product["candidate_hash"],True,settings);writes+=result.get("server_write_count",0);verified=result.get("result")=="PASS"
        if not verified and result.get("publish_run_id"):
            rec=reconcile_live_run(product["slug"],result["publish_run_id"],settings);verified=rec["result"]=="VERIFIED"
        row={"slug":product["slug"],"mode":product["mode"],"publish_run_id":result.get("publish_run_id"),"result":"VERIFIED" if verified else result.get("result")};rows.append(row);product_folder=folder/"products"/product["slug"];save(product_folder/"candidate_hash.json",{"candidate_hash":product["candidate_hash"]});save(product_folder/"publish_run_reference.json",row);save(product_folder/"verification.json",{"verified":verified})
        if not verified:break
    attempted=len(rows);verified=sum(x["result"]=="VERIFIED" for x in rows);report={"run_id":plan["canary_run_id"],"planned":len(plan["products"]),"attempted":attempted,"verified":verified,"failed":attempted-verified,"updates":{"attempted":sum(x["mode"]=="UPDATE_EXISTING" for x in rows),"verified":sum(x["mode"]=="UPDATE_EXISTING" and x["result"]=="VERIFIED" for x in rows)},"creates":{"attempted":sum(x["mode"]=="CREATE_NEW" for x in rows),"verified":sum(x["mode"]=="CREATE_NEW" and x["result"]=="VERIFIED" for x in rows)},"server_writes":writes,"result":"VERIFIED" if verified==len(plan["products"]) else "STOPPED"};save(folder/"execution_report.json",report);return report
