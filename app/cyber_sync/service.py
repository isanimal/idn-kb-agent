import hashlib,json,re
from datetime import datetime,timezone,timedelta
from pathlib import Path

from app.candidate.service import candidate_preflight,dry_run as candidate_dry_run
from app.core.database import Database
from app.identity.service import LIVE,load,refresh_live_index,resolve_identity,route_product
from app.live_publish.reconcile import reconcile_live_run
from app.live_publish.service import live_preflight,publish_live,validate_frozen
from app.merge.service import merge_check
from app.quality.service import run_one as quality_one
from app.resolver.engine import ResolverContext,canonical_url
from app.resolver.service import run_one as resolve_one

ROOT=Path("data/cyber_sync");CATALOG=Path("data/site_models/training_catalog.json");EXCLUDED_SLUG="pentest";EXCLUDED_NAME="basic penetration testing";PUBLISHABLE={"READY","READY_WITH_WARNINGS"}
OPTIONAL_WARNING_CODES={"GENERIC_NEXT_CLASS_REASON","TRAINER_NOT_MAPPED","AMBIGUOUS_PRICE_NOT_PUBLISHED","OPTIONAL_FIELD_MISSING"}
class CyberSyncError(RuntimeError):pass
def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def _slug(product):return product.get("slug") or Path(product["canonical_url"].rstrip("/")).name
def _norm(value):return " ".join(re.findall(r"[a-z0-9]+",value.lower()))
def discover():
    catalog=load(CATALOG);inventory=load(LIVE);ctx=ResolverContext();products=[];excluded=0
    for group in catalog["categories"]:
        for item in group["products"]:
            slug=_slug(item);name=item["name"];category,method,confidence=ctx.category(group["name"],name,None)
            if category!="Cybersecurity":continue
            if slug==EXCLUDED_SLUG or _norm(name)==EXCLUDED_NAME:excluded+=1;continue
            identity=resolve_identity(slug,{"full_name":name,"short_name":None,"seo_url":item["canonical_url"]},inventory)
            products.append({"slug":slug,"name":name,"source_url":item["canonical_url"],"source_category":group["name"],"mapped_category":category,"mapping_method":method,"mapping_confidence":confidence,"kb_status":"EXISTS" if identity.decision.value=="UPDATE_EXISTING" else "NOT_IN_KB" if identity.decision.value=="CREATE_NEW" else "AMBIGUOUS","identity_decision":identity.decision.value,"kb_product_id":identity.existing_product["kb_product_id"] if identity.existing_product else None})
    result={"scope":"Cybersecurity","generated_at":datetime.now(timezone.utc).isoformat(),"total_catalog":sum(len(x["products"]) for x in catalog["categories"]),"cybersecurity_products":len(products),"excluded_pentest":excluded,"products":products,"server_writes":0};save(ROOT/"discovery.json",result);return result
def _scoped_quality(slug,settings):
    report,_=quality_one(slug,settings,False);path=Path("data/publish_ready")/slug;data=load(path/"quality_report.json");payload=load(path/"publish_payload.json");warnings=[]
    # Optional unresolved structures are omitted, never guessed.
    unknown_formats=[x for x in payload.get("training_formats",[]) if x.get("format")=="UNKNOWN"]
    if unknown_formats:payload["training_formats"]=[];warnings.append("UNKNOWN_FORMAT_OMITTED")
    ambiguous=[x for x in payload.get("training_formats",[]) if x.get("price_note","").startswith("Observed price candidates")]
    for row in ambiguous:row["public_price_reference"]=None;row["private_price_reference"]=None
    if ambiguous:warnings.append("AMBIGUOUS_PRICE_NOT_PUBLISHED")
    if any(x.get("code")=="GENERIC_NEXT_CLASS_REASON" for x in data.get("errors",[])):payload["next_classes"]=[];warnings.append("NEXT_CLASS_DEFERRED")
    unsafe=[x for x in data.get("errors",[]) if x.get("blocking") or x.get("code") not in OPTIONAL_WARNING_CODES]
    cross_domain=any(x in json.dumps(payload,ensure_ascii=False).lower() for x in ("capcut","video editing","social media mastery","arduino","workflow automation"))
    if cross_domain:unsafe.append({"code":"CROSS_DOMAIN_CONTAMINATION"})
    if payload.get("category")!="Cybersecurity":unsafe.append({"code":"WRONG_CATEGORY"})
    if unsafe:readiness="BLOCKED" if any(x.get("blocking") for x in unsafe) or cross_domain else "REVIEW_REQUIRED"
    elif data.get("publish_readiness")=="READY" and not warnings:readiness="READY"
    else:readiness="READY_WITH_WARNINGS"
    if readiness in PUBLISHABLE:
        save(path/"publish_payload.json",payload);data["cyber_scoped_original_readiness"]=data["publish_readiness"];data["cyber_scoped_readiness"]=readiness;data["publish_readiness"]="READY";save(path/"quality_report.json",data)
    return readiness,warnings,[x.get("code") for x in unsafe]
def _manifest_hash(products,inventory_hash):
    body={"products":[{"slug":x["slug"],"mode":x["mode"],"candidate_hash":x["candidate_hash"]} for x in products],"inventory_hash":inventory_hash};return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def prepare(settings):
    discovery=discover();inventory=load(LIVE);database=Database(settings.database_path);database.initialize_database();rows=[];publishable=[]
    for item in discovery["products"]:
        slug=item["slug"]
        if slug==EXCLUDED_SLUG or _norm(item["name"])==EXCLUDED_NAME:continue
        try:
            resolve_one(slug,settings,database,False,local_ai=False);readiness,warnings,unsafe=_scoped_quality(slug,settings)
            if readiness not in PUBLISHABLE:rows.append({**item,"readiness":readiness,"warnings":warnings,"reasons":unsafe});continue
            route,_,_=route_product(slug,settings,database,inventory)
            if route["identity_decision"]=="REVIEW_REQUIRED":rows.append({**item,"readiness":"REVIEW_REQUIRED","reasons":["IDENTITY_AMBIGUOUS"]});continue
            if route["identity_decision"]=="UPDATE_EXISTING":merge_check(slug)
            check=candidate_preflight(slug)
            if not check["ready"]:raise CyberSyncError(",".join(check["reasons"]))
            candidate=candidate_dry_run(slug,settings)
            if candidate["live_candidate_readiness"]!="READY":raise CyberSyncError("CANDIDATE_NOT_READY")
            row={**item,"mode":route["identity_decision"],"candidate_hash":candidate["candidate_hash"],"readiness":readiness,"warnings":warnings,"status":"PREPARED"};rows.append(row);publishable.append({k:row[k] for k in ("slug","name","mode","candidate_hash","readiness","warnings")})
        except Exception as exc:rows.append({**item,"readiness":"BLOCKED","reasons":[type(exc).__name__+":"+str(exc)]})
        save(ROOT/"products"/slug/"preparation.json",rows[-1])
    ordered=sorted(publishable,key=lambda x:x["slug"]);digest=_manifest_hash(ordered,inventory["inventory_hash"]);manifest={"scope":"Cybersecurity","generated_at":datetime.now(timezone.utc).isoformat(),"excluded":[EXCLUDED_SLUG],"inventory_hash":inventory["inventory_hash"],"products":ordered,"cyber_manifest_hash":digest,"ttl_minutes":60};save(ROOT/"current_manifest.json",manifest);summary={"total_cyber":len(discovery["products"]),"excluded_pentest":discovery["excluded_pentest"],"products":rows,"manifest":manifest,"server_writes":0};save(ROOT/"preparation_report.json",summary);return summary
def _manifest(supplied):
    manifest=load(ROOT/"current_manifest.json")
    if supplied!=manifest["cyber_manifest_hash"] or supplied!=_manifest_hash(manifest["products"],manifest["inventory_hash"]):raise CyberSyncError("CYBER_MANIFEST_HASH_MISMATCH")
    if datetime.now(timezone.utc)-datetime.fromisoformat(manifest["generated_at"])>timedelta(minutes=manifest["ttl_minutes"]):raise CyberSyncError("CYBER_MANIFEST_STALE")
    return manifest
def preflight(supplied,settings):
    manifest=_manifest(supplied);inventory=refresh_live_index(settings);results=[]
    for item in manifest["products"]:
        try:validate_frozen(item["slug"],item["candidate_hash"]);result=live_preflight(item["slug"],item["candidate_hash"],settings,refresh=False);results.append({**item,"result":result["result"]})
        except Exception as exc:results.append({**item,"result":"BLOCKED","reason":type(exc).__name__+":"+str(exc)})
    failures=sum(x["result"]!="ARMED_CANDIDATE_READY" for x in results);report={"scope":"Cybersecurity","manifest_hash":supplied,"generated_at":datetime.now(timezone.utc).isoformat(),"products":results,"skipped_review":sum(x["readiness"]=="REVIEW_REQUIRED" for x in load(ROOT/"preparation_report.json")["products"]),"blocked":sum(x["readiness"]=="BLOCKED" for x in load(ROOT/"preparation_report.json")["products"]),"preflight_failures":failures,"result":"READY" if results and not failures else "BLOCKED","server_writes":0};save(ROOT/"preflight.json",report);return report
def execute(supplied,confirm_write,settings):
    if not confirm_write:raise CyberSyncError("EXPLICIT_WRITE_CONFIRMATION_REQUIRED")
    manifest=_manifest(supplied);rows=[];writes=0;stop=False;run=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-cyber");run_root=ROOT/run
    save(run_root/"manifest.json",manifest)
    if (ROOT/"preflight.json").exists():save(run_root/"preflight.json",load(ROOT/"preflight.json"))
    for item in manifest["products"]:
        product_root=run_root/"products"/item["slug"]
        save(product_root/"candidate_reference.json",item)
        try:
            live_preflight(item["slug"],item["candidate_hash"],settings,refresh=True);result=publish_live(item["slug"],item["candidate_hash"],True,settings);writes+=result.get("server_write_count",0);verified=result.get("result")=="PASS"
            if not verified and result.get("publish_run_id"):verified=reconcile_live_run(item["slug"],result["publish_run_id"],settings)["result"]=="VERIFIED"
            rows.append({"slug":item["slug"],"status":"VERIFIED" if verified else "POST_WRITE_ERROR","publish_run_id":result.get("publish_run_id")})
            save(product_root/"publish_reference.json",{"publish_run_id":result.get("publish_run_id"),"result":result.get("result")});save(product_root/"verification.json",rows[-1])
            if not verified:stop=True;break
        except Exception as exc:
            # Once an observed mutation exists, any error is post-save and stops the batch.
            observed_writes=int(getattr(exc,"server_write_count",0));writes+=observed_writes;after_save=bool(observed_writes);rows.append({"slug":item["slug"],"status":"POST_WRITE_ERROR" if after_save else "SKIPPED_PRE_SAVE","reason":str(exc)});save(product_root/"verification.json",rows[-1])
            if after_save:stop=True;break
    report={"scope":"Cybersecurity","attempted":len(rows),"verified":sum(x["status"]=="VERIFIED" for x in rows),"skipped_before_save":sum(x["status"]=="SKIPPED_PRE_SAVE" for x in rows),"server_writes":writes,"result":"STOPPED_POST_WRITE_ERROR" if stop else "VERIFIED_WITH_SKIPS" if any(x["status"]!="VERIFIED" for x in rows) else "VERIFIED","products":rows};save(run_root/"execution_report.json",report);return report
