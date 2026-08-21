import json,re
from datetime import datetime,timezone
from pathlib import Path

from app.browser.manager import BrowserManager
from app.identity.service import LIVE,load,publisher_preflight,route_is_fresh
from app.kb.auth import classify_auth_page,wait_for_manual_auth
from app.kb.guard import ReadOnlyGuard,ReadOnlyViolation
from app.kb.models import AuthState

OUT=Path("data/publisher_dry_runs");SHOTS=Path("runtime/screenshots/publisher")
STATIC={
 "full_name":("Nama lengkap produk",lambda v:v),"short_name":("Nama singkat",lambda v:v or ""),"category":("Kategori",lambda v:v),"seo_url":("Link landing page SEO",lambda v:v),
 "short_description":("Deskripsi singkat",lambda v:v),"learning_outcomes":("Selesai training, peserta bisa apa saja",lambda v:"\n".join(v)),"prerequisites":("Prasyarat",lambda v:"\n".join(v)),
 "repeat_policy":("Kebijakan mengulang training",lambda v:v),"practice_examples":("Contoh praktek",lambda v:"\n".join(v)),"post_training_support":("Support pasca-training",lambda v:v),
 "selling_points":("Poin jualan utama",lambda v:"\n".join(v)),"claims_to_avoid":("Klaim yang dihindari",lambda v:"\n".join(v)),"additional_notes":("Catatan tambahan",lambda v:v),
}
HIGH_RISK={"training_formats","trainer_references","certifications","repeat_policy","active"}
def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")

class PublishReadinessViolation(RuntimeError):pass
class PublisherPreflightViolation(RuntimeError):pass

def preflight(slug):
    inventory=load(LIVE);route_path=Path("data/publish_routes")/slug/"route.json"
    if not route_path.exists():raise PublisherPreflightViolation("ROUTE_MISSING")
    route=load(route_path);result=publisher_preflight(slug,inventory)
    return {"slug":slug,"route":route,"dry_run_allowed":bool(result.get("dry_run_allowed")),"live_publish_allowed":bool(result.get("live_publish_allowed")),"reason":result["reason"],"route_fresh":route_is_fresh(route,inventory,route.get("route_ttl_seconds",3600))}

def _ensure(page,url):
    page.goto(url,wait_until="domcontentloaded",timeout=60_000);page.wait_for_timeout(700)
    if classify_auth_page(page.content(),page.url)!=AuthState.AUTHENTICATED and wait_for_manual_auth(page)!=AuthState.AUTH_RESTORED:raise PublisherPreflightViolation("AUTH_FAILED")
    page.get_by_text(re.compile(r"(?:Ubah|Tambah) Produk Training"),exact=False).wait_for(timeout=20_000)

def _current(control):
    return control.input_value() if control.count() else None
def _field_value(control,field):
    if not control.count():return None
    if field=="category":return control.locator("option:checked").inner_text().strip()
    return control.input_value()

def decide_action(field,current,proposed,conflicts):
    if field in conflicts or (field in HIGH_RISK and current not in ("",proposed)):
        return "PRESERVE_EXISTING","CONFLICT_PRESERVED"
    if proposed in (None,"",[]):return ("PRESERVE_EXISTING","PRESERVE_EXISTING") if current else ("UNCHANGED","UNCHANGED")
    if current==proposed:return "UNCHANGED","UNCHANGED"
    action="FILL_EMPTY" if not current else "UPDATE_VALUE";return action,"APPLIED_LOCALLY"

def dry_run(slug,settings):
    check=preflight(slug);route=check["route"]
    if route["publish_readiness"]!="READY":raise PublishReadinessViolation(f"PublishReadinessViolation: {route['publish_readiness']}")
    if not check["dry_run_allowed"]:raise PublisherPreflightViolation(check["reason"])
    payload=load(Path("data/publish_ready")/slug/"publish_payload.json");schema=load("data/kb_site_models/kb_training_form_schema.json")
    category_labels={x["label"] for f in schema["fields"] if f["label"]=="Kategori" for x in f["options"]};trainer_ids={x["value"] for x in schema.get("trainer_options",[])}
    if payload["category"] not in category_labels:raise PublisherPreflightViolation("SCHEMA_INCOMPATIBLE_CATEGORY")
    invalid_trainers=[x["kb_trainer_id"] for x in payload["trainer_references"] if x["kb_trainer_id"] not in trainer_ids]
    if invalid_trainers:raise PublisherPreflightViolation("SCHEMA_INCOMPATIBLE_TRAINER")
    manager=BrowserManager(settings.browser_profile_path,False);guard=ReadOnlyGuard();actions=[];before={};after={};conflicts=set(route.get("blocking_conflicts",[]));SHOTS.mkdir(parents=True,exist_ok=True)
    try:
        manager.start();guard.install(manager.context);page=manager.new_page();_ensure(page,route["target_url"]);page.screenshot(path=str(SHOTS/f"{slug}-before.png"),full_page=True)
        for field,(label,encode) in STATIC.items():
            proposed=encode(payload.get(field));control=page.get_by_label(label,exact=False).first;current=_field_value(control,field);before[field]=current
            if current is None:raise PublisherPreflightViolation(f"SCHEMA_INCOMPATIBLE:{field}")
            action,status=decide_action(field,current,proposed,conflicts)
            if status=="CONFLICT_PRESERVED":actions.append({"field":field,"action":action,"status":status,"before":current,"proposed":proposed,"after":current});conflicts.add(field);continue
            if action in {"FILL_EMPTY","UPDATE_VALUE"}:
                control.select_option(label=str(proposed)) if field=="category" else control.fill(str(proposed))
            value=_field_value(control,field);after[field]=value;actions.append({"field":field,"action":action,"status":"APPLIED_LOCALLY" if action in {"FILL_EMPTY","UPDATE_VALUE"} else action,"before":current,"proposed":proposed,"after":value})
        # Dynamic high-risk fields: fill only truly empty controls; preserve differing existing values.
        format_value=(page.locator('main select').nth(1).input_value() if page.locator('main select').count()>1 else None);format_action={"field":"training_formats","action":"PRESERVE_EXISTING","status":"VALIDATED_EXISTING","observed_format":format_value,"proposed":payload["training_formats"],"local_fills":[]}
        if payload["training_formats"]:
            proposed_format=payload["training_formats"][0];price=page.locator('main input[placeholder="0"]').first
            if price.count() and not price.input_value() and proposed_format.get("public_price_reference") is not None:
                price.fill(str(proposed_format["public_price_reference"]));format_action["local_fills"].append({"field":"public_price_reference","action":"FILL_EMPTY","after":price.input_value()})
        actions.append(format_action)
        trainer_trigger=page.get_by_label("Trainer referensi",exact=False).first;trainer_before=trainer_trigger.inner_text().strip() if trainer_trigger.count() else ""
        trainer_action={"field":"trainer_references","action":"PRESERVE_EXISTING","status":"VALIDATED_EXACT_IDS","trainer_ids":[x["kb_trainer_id"] for x in payload["trainer_references"]],"before":trainer_before}
        if trainer_trigger.count() and ("Pilih trainer" in trainer_before or not trainer_before) and payload["trainer_references"]:
            for trainer in payload["trainer_references"]:
                if trainer_trigger.get_attribute("aria-expanded")!="true":trainer_trigger.click()
                option=page.get_by_text(trainer["trainer_name"],exact=True)
                if not option.count():raise PublisherPreflightViolation(f"SCHEMA_INCOMPATIBLE_TRAINER_OPTION:{trainer['kb_trainer_id']}")
                option.first.click();page.wait_for_timeout(50)
            page.keyboard.press("Escape");trainer_action.update(action="FILL_EMPTY",status="APPLIED_LOCALLY",after=trainer_trigger.inner_text().strip())
        actions.append(trainer_action)
        page.screenshot(path=str(SHOTS/f"{slug}-after-local.png"),full_page=True)
        if any(x["after"]!=_field_value(page.get_by_label(STATIC[x["field"]][0],exact=False).first,x["field"]) for x in actions if x["field"] in STATIC):raise PublisherPreflightViolation("READBACK_MISMATCH")
        if guard.blocked:raise ReadOnlyViolation(f"Unexpected mutation request blocked: {guard.blocked}")
        result={"schema_version":"publisher-dry-run-v1","slug":slug,"generated_at":datetime.now(timezone.utc).isoformat(),"system_status":"PASS","mode":route["identity_decision"],"target_url":route["target_url"],"dry_run":"PASS","dry_run_allowed":True,"live_publish_allowed":False if conflicts else check["live_publish_allowed"],"blocking_conflicts":sorted(conflicts),"conflicts":{"detected":len(conflicts),"preserved":sum(x["status"]=="CONFLICT_PRESERVED" for x in actions),"overwritten":0},"actions":actions,"validation":{"category":payload["category"],"category_valid":True,"trainer_exact_ids":not invalid_trainers,"observed_format":format_value,"readback":"PASS"},"screenshots":[str(SHOTS/f"{slug}-before.png"),str(SHOTS/f"{slug}-after-local.png")],"network":{"blocked_mutations":guard.blocked,"server_writes":0},"save_clicked":False}
        save(OUT/slug/"dry_run_report.json",result);return result
    finally:manager.stop()

def report(slug):
    path=OUT/slug/"dry_run_report.json"
    if not path.exists():raise FileNotFoundError("No publisher dry-run report")
    return load(path)
