import json,re
from datetime import datetime,timezone
from pathlib import Path

from app.identity.service import load

OUT=Path("data/merge_ready")
SEMANTIC_LIST={"learning_outcomes","prerequisites","practice_examples","selling_points"}
SEMANTIC_TEXT={"short_description"}
INTERNAL_POLICY={"repeat_policy","post_training_support","claims_to_avoid","additional_notes"}
COMMERCIAL_CURRENT={"seo_url","training_formats"}
STOP={"yang","dan","atau","untuk","dengan","dari","pada","dalam","peserta","training","pelatihan","mampu","memahami","melakukan","membuat","menggunakan","dasar","sebuah","secara","serta","tidak","dapat","akan","adalah"}
GENERIC=re.compile(r"sesuai (?:materi|kurikulum)|ingin mempelajari|membangun pemahaman|menguasai materi",re.I)
RISKY=re.compile(r"pasti|dijamin|terbaik|langsung kerja|meningkatkan gaji|penghasilan tambahan|marketability",re.I)

def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def norm(value):return " ".join(re.findall(r"[a-z0-9]+",str(value).lower()))
def as_items(value):
    if isinstance(value,list):
        return [json.dumps(x,ensure_ascii=False,sort_keys=True) if isinstance(x,dict) else str(x) for x in value]
    return [x.strip() for x in str(value or "").splitlines() if x.strip()]
def topics(value):
    text=" ".join(as_items(value));return {x for x in re.findall(r"[a-z0-9]+",text.lower()) if len(x)>2 and x not in STOP}
def semantic_metrics(value,product_name=""):
    items=as_items(value);topic=topics(value);text=" ".join(items);technical=sum(bool(re.search(r"\b(?:nmap|cve|cvss|metasploit|linux|windows|http|smb|ftp|ssh|vlan|api|arduino|esp8266|lab|scope|retest|report|enumeration|scanning)\b",x,re.I)) for x in items)
    generic=sum(bool(GENERIC.search(x)) for x in items);risky=sum(bool(RISKY.search(x)) for x in items);specific=int(bool(product_name and any(t in norm(text) for t in norm(product_name).split() if len(t)>3)))
    score=max(0,min(100,35+min(25,len(topic))+min(20,technical*3)+specific*10-generic*8-risky*12))
    return {"score":score,"items":len(items),"topics":sorted(topic),"topic_count":len(topic),"technical_items":technical,"generic_penalty":generic,"unsupported_claim_penalty":risky}
def dedup(items):
    out=[];seen=set()
    for x in items:
        key=norm(x)
        if key and key not in seen:seen.add(key);out.append(x)
    return out

def decide(field,existing,new,product_name=""):
    if existing in (None,"",[]) and new in (None,"",[]):return "UNCHANGED","Both existing and new values are empty.",None,None
    if existing in (None,"",[]):return "FILL_EMPTY","Existing KB field is empty.",None,None
    if new in (None,"",[]):return "KEEP_EXISTING","New payload is empty; erasure is forbidden.",None,None
    if norm(existing)==norm(new):return "UNCHANGED","Values are semantically identical after normalization.",None,None
    if field in INTERNAL_POLICY:return "KEEP_EXISTING","Existing internal policy has priority and must not be shortened.",semantic_metrics(existing),semantic_metrics(new)
    if field in COMMERCIAL_CURRENT:return "REPLACE_WITH_NEW","Current unambiguous IDN primary fact has priority.",None,None
    if field in SEMANTIC_LIST|SEMANTIC_TEXT:
        oldm,newm=semantic_metrics(existing,product_name),semantic_metrics(new,product_name);oldtopics=set(oldm["topics"]);newtopics=set(newm["topics"]);lost=oldtopics-newtopics;new_unique=newtopics-oldtopics;lost_ratio=len(lost)/len(oldtopics) if oldtopics else 0
        oldm["lost_topics"]=sorted(lost);newm["new_topics"]=sorted(new_unique);newm["lost_topic_ratio"]=round(lost_ratio,3)
        if lost_ratio>.35 or oldm["score"]>newm["score"]+8:return "KEEP_EXISTING","INFORMATION_COVERAGE_REGRESSION prevented.",oldm,newm
        if field in SEMANTIC_LIST and new_unique:return "AUGMENT_EXISTING","New grounded unique content augments existing coverage without replacement.",oldm,newm
        if newm["score"]>=oldm["score"]+12:return "REPLACE_WITH_NEW","New semantic value is clearly stronger by deterministic metrics.",oldm,newm
        return "KEEP_EXISTING","Existing curated value is not demonstrably worse.",oldm,newm
    return "REPLACE_WITH_NEW","New quality-approved value is applicable.",None,None

def existing_baseline(slug):
    report_path=Path("data/publisher_dry_runs")/slug/"dry_run_report.json"
    if report_path.exists():
        report=load(report_path);return {x["field"]:x.get("before") for x in report["actions"] if "before" in x}
    route=load(Path("data/publish_routes")/slug/"route.json");inventory=load("data/runtime_indexes/kb_products_live.json");product=next((x for x in inventory["products"] if x["kb_product_id"]==route.get("kb_product_id")),None)
    if not product:raise FileNotFoundError("LIVE_BASELINE_TARGET_MISSING")
    snapshots=Path("data/kb_site_models/kb_product_snapshots").glob("*.json");snapshot=next((load(x) for x in snapshots if load(x).get("name")==product["name"]),None)
    if not snapshot:raise FileNotFoundError("KB_SNAPSHOT_BASELINE_MISSING")
    sections={norm(x.get("heading")):x.get("content","") for x in snapshot.get("sections",[])}
    def section(*names):return next((sections[norm(x)] for x in names if norm(x) in sections),None)
    return {"full_name":product["name"],"short_name":product.get("short_name"),"category":product.get("category"),"seo_url":product.get("seo_url"),"short_description":section("Deskripsi singkat"),"learning_outcomes":section("Setelah training selesai, peserta bisa","Selesai training, peserta bisa apa saja"),"prerequisites":section("Prasyarat"),"repeat_policy":section("Kebijakan mengulang training"),"practice_examples":section("Contoh praktek"),"post_training_support":section("Support pasca-training"),"selling_points":section("Poin jualan utama"),"claims_to_avoid":section("Klaim yang dihindari"),"additional_notes":section("Catatan tambahan")}
def _existing_value(field,baseline):
    value=baseline.get(field)
    return as_items(value) if field in SEMANTIC_LIST and value not in (None,"") else value

def merge_check(slug,local_ai=False):
    route=load(Path("data/publish_routes")/slug/"route.json")
    if route["identity_decision"]!="UPDATE_EXISTING":raise ValueError("MERGE_NOT_REQUIRED")
    new=load(Path("data/publish_ready")/slug/"publish_payload.json");baseline=existing_baseline(slug);merged=json.loads(json.dumps(new));rows=[];prevented=0
    for field,new_value in new.items():
        existing=_existing_value(field,baseline);decision,reason,oldm,newm=decide(field,existing,new_value,new["full_name"])
        if decision in {"KEEP_EXISTING","UNCHANGED"} and existing not in (None,"",[]):merged[field]=existing
        elif decision=="AUGMENT_EXISTING":merged[field]=dedup([*as_items(existing),*as_items(new_value)])
        if decision=="KEEP_EXISTING" and "REGRESSION" in reason:prevented+=1
        rows.append({"field":field,"decision":decision,"reason":reason,"existing_score":oldm["score"] if oldm else None,"new_score":newm["score"] if newm else None,"metrics":{"existing":oldm,"new":newm} if oldm or newm else None})
    counts={x:sum(r["decision"]==x for r in rows) for x in ("KEEP_EXISTING","FILL_EMPTY","REPLACE_WITH_NEW","AUGMENT_EXISTING","UNCHANGED","REVIEW_REQUIRED")}
    readiness="REVIEW_REQUIRED" if counts["REVIEW_REQUIRED"] else "READY";report={"schema_version":"merge-report-v1","slug":slug,"identity_decision":route["identity_decision"],"generated_at":datetime.now(timezone.utc).isoformat(),"fields":rows,"counts":counts,"regressions_prevented":prevented,"review_required":readiness!="READY","merge_readiness":readiness,"local_ai":{"requested":local_ai,"used":False,"reason":"No uncertain comparison required"},"source_artifacts_mutated":False,"kb_writes":0}
    folder=OUT/slug;save(folder/"merge_report.json",report);save(folder/"merged_payload.json",merged);save(folder/"merge_trace.json",{"slug":slug,"baseline_source":f"data/publisher_dry_runs/{slug}/dry_run_report.json","new_source":f"data/publish_ready/{slug}/publish_payload.json","decisions":rows,"ollama":report["local_ai"]});return report,merged

def merge_report(slug):return load(OUT/slug/"merge_report.json")
def merge_batch(limit=None):
    routes=sorted(Path("data/publish_routes").glob("*/route.json"));selected=[]
    for path in routes:
        route=load(path)
        if route["identity_decision"]=="UPDATE_EXISTING" and (Path("data/publisher_dry_runs")/path.parent.name/"dry_run_report.json").exists():selected.append(path.parent.name)
    return [merge_check(x)[0] for x in selected[:limit if limit is not None else len(selected)]]
