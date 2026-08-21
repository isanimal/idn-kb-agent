"""Field-by-field deterministic resolver; performs no network or browser actions."""
import hashlib,json,re
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit,urlunsplit
from app.core.database import Database
from app.resolver.knowledge import build_internal_index,load
from app.resolver.models import *

PRODUCTS=Path("data/products");KB=Path("data/kb_site_models");OUT=Path("data/resolved_products");RES=Path("data/resolver")
FIELDS=list(KBProductPayload.model_fields)
def norm(v:str)->str:return re.sub(r"[^a-z0-9]+"," ",v.lower()).strip()
def canonical_url(v:str)->str:
    p=urlsplit(v);return urlunsplit((p.scheme.lower(),p.netloc.lower().removeprefix("www."),p.path.rstrip("/")+"/","",""))
def fact_value(facts:dict,key:str):
    node=facts
    for part in key.split("."):node=node.get(part,{}) if isinstance(node,dict) else {}
    if not isinstance(node,dict) or node.get("status")!="FOUND":return None
    return node.get("value") if node.get("value") is not None else (node.get("values") or None)
def evidence_sources(facts:dict,key:str,kind="IDN_PRODUCT"):
    node=facts
    for part in key.split("."):node=node.get(part,{}) if isinstance(node,dict) else {}
    return [SourceRef(url=x["source_url"],type=kind,title=x.get("source_section")) for x in node.get("evidence",[]) if x.get("source_url")]
def as_list(v):return v if isinstance(v,list) else ([] if v in (None,"") else [v])
def strings(v)->list[str]:
    out=[]
    for x in as_list(v):
        if isinstance(x,str):out.append(x)
        elif isinstance(x,dict):out.extend(strings(x.get("items") or x.get("title") or x.get("name")))
    return [x for x in out if x]
def rv(value,status=ResolutionStatus.RESOLVED,method=ResolutionMethod.DIRECT_FACT,source_type="IDN_PRIMARY",sources=None,confidence=.95,note=None,needs_review=False,**kw):
    return ResolvedValue(status=status,value=value,confidence=confidence,source_type=source_type,sources=sources or [],method=method,needs_review=needs_review,note=note,**kw)
def na(value):return rv(value,status=ResolutionStatus.NOT_APPLICABLE,method=ResolutionMethod.SAFE_DEFAULT,source_type="NONE",confidence=1,note="No authoritative value available; intentionally left empty.")

class ResolverContext:
    def __init__(self):
        self.catalog=load(Path("data/site_models/training_catalog.json"));self.products=[p for c in self.catalog["categories"] for p in c["products"]]
        self.categories=[x["label"] for x in load(KB/"kb_categories.json")["categories"]];self.trainers=load(KB/"kb_trainers.json")["trainers"]
        self.links=load(KB/"kb_idn_links.json")["links"];self.existing=load(KB/"kb_existing_products.json")["products"];self.index=build_internal_index()
        self.by_url={canonical_url(x["idn_url"]):x for x in self.links if x.get("idn_url")};self.category_map=self.build_category_map()
    def build_category_map(self):
        counts={}
        existing_by_name={norm(x["name"]):x for x in self.existing}
        for p in self.products:
            match=self.by_url.get(canonical_url(p["canonical_url"]));kbp=existing_by_name.get(norm(p["name"]))
            category=(next((x.get("category") for x in self.existing if match and x["name"]==match["kb_product"]),None) or (kbp or {}).get("category"))
            if category:counts.setdefault((p["category"],category),0);counts[(p["category"],category)]+=1
        rows=[{"source_category":s,"destination_category":d,"method":"OBSERVED","evidence_count":n} for (s,d),n in sorted(counts.items())]
        RES.mkdir(parents=True,exist_ok=True);data={"allowed_categories":self.categories,"mappings":rows};(RES/"category_mapping.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");return data
    def category(self,source:str,name:str,existing:dict|None):
        if existing and existing.get("category") in self.categories:return existing["category"],"INTERNAL_KB",1.0
        votes=[x for x in self.category_map["mappings"] if norm(x["source_category"])==norm(source)]
        if votes:return max(votes,key=lambda x:x["evidence_count"])["destination_category"],"OBSERVED",.95
        text=norm(source+" "+name);rules=[("IoT & Robotik",("iot","robot")),("Cybersecurity",("security","pentest","hacking","forensic")),("AI",(" ai ","automation","machine learning")),("Programming",("android","flutter","python","program","web development")),("Sysadmin",("linux","server","devops","cloud")),("Networking",("cisco","mikrotik","juniper","wlan","network","fortinet","huawei","ruijie")),("Lainnya",("content creator","social media","digital marketing"))]
        for cat,words in rules:
            if cat in self.categories and any(w.strip() in text for w in words):return cat,"DETERMINISTIC_CLASSIFIER",.8
        return "Lainnya" if "Lainnya" in self.categories else self.categories[0],"SAFE_DEFAULT",.6
    def match(self,url,name):
        hit=self.by_url.get(canonical_url(url))
        if hit:return "MATCHED",next((x for x in self.existing if x["name"]==hit["kb_product"]),None),"EXACT_URL"
        exact=[x for x in self.existing if norm(x["name"])==norm(name)]
        return ("MATCHED",exact[0],"EXACT_NAME") if len(exact)==1 else (("AMBIGUOUS",None,None) if len(exact)>1 else ("UNMATCHED",None,None))
    def snapshot(self,existing):
        if not existing:return None
        path=KB/"kb_product_snapshots"/(re.sub(r"[^a-z0-9]+","-",existing["name"].lower()).strip("-")+".json")
        return load(path) if path.exists() else None

def section(snapshot,title):
    if not snapshot:return None
    return next((x.get("content") for x in snapshot.get("sections",[]) if norm(x.get("heading",""))==norm(title)),None)
def resolve_product(slug:str,database:Database,ctx:ResolverContext|None=None)->ResolvedProduct:
    ctx=ctx or ResolverContext();path=PRODUCTS/slug/"facts.json"
    if not path.exists():raise FileNotFoundError(f"Unknown product slug: {slug}")
    facts=load(path);name=fact_value(facts,"identity.full_name") or facts.get("metadata",{}).get("registry_name") or slug;url=fact_value(facts,"identity.source_url")
    source_cat=fact_value(facts,"identity.source_category") or "";match_status,existing,match_method=ctx.match(url,name);snap=ctx.snapshot(existing)
    cat,cat_method,cat_conf=ctx.category(source_cat,name,existing);sources=[SourceRef(url=url,type="IDN_PRODUCT")]
    short=(existing or {}).get("short_name") or fact_value(facts,"identity.short_name") or name
    desc=fact_value(facts,"description") or section(snap,"Deskripsi singkat") or f"Training {name}."
    duration=fact_value(facts,"duration");price=fact_value(facts,"price");fmt=fact_value(facts,"training_format")
    if not fmt:
        snapshot_format=section(snap,"Format, durasi & harga referensi") or "";fmt=next((x for x in ("Hybrid","Offline","Online") if re.search(rf"\b{x}\b",snapshot_format,re.I)),None)
    duration_text=(f"{duration.get('days'):g} hari" if isinstance(duration,dict) and duration.get("days") else (str(duration) if duration else None));schedule=None
    if isinstance(duration,dict) and duration.get("daily_schedule"):schedule=f"{duration['daily_schedule'].get('start')}–{duration['daily_schedule'].get('end')} {duration['daily_schedule'].get('timezone','')}".strip()
    raw_prices=as_list(price);prices=[x.get("amount") if isinstance(x,dict) else x for x in raw_prices]
    ambiguous_prices=len(prices)>1 and not any(isinstance(x,dict) and x.get("qualifier") for x in raw_prices)
    formats=[TrainingFormat(format=str(fmt or "UNKNOWN"),duration=duration_text,schedule=schedule,public_price_reference=None if ambiguous_prices else (prices[0] if prices and isinstance(prices[0],int) else None),private_price_reference=None,price_note=("Observed price candidates: "+", ".join(str(x) for x in prices)+"; labels must be reviewed." if ambiguous_prices else "Harga referensi; konfirmasi harga dan jadwal terbaru ke IDN."))]
    format_needs_review=not fmt or ambiguous_prices
    curriculum=fact_value(facts,"curriculum");outcomes=[]
    for item in as_list(curriculum):
        if isinstance(item,dict):outcomes.extend(strings(item.get("items"))[:3])
    outcomes=(strings(fact_value(facts,"benefits"))+outcomes)[:12]
    audiences=[TargetAudience(audience=x) for x in strings(fact_value(facts,"target_audiences"))]
    if not audiences:audiences=[TargetAudience(audience="Peserta yang ingin mempelajari "+name,problem_solved="Membangun pemahaman dan keterampilan sesuai kurikulum training.")]
    prereq=strings(fact_value(facts,"prerequisites"));practice=strings(fact_value(facts,"practice")) or outcomes[:4]
    certs=[Certification(name=x,relationship="RELATED") for x in strings(fact_value(facts,"certifications"))]
    if not certs and re.search(r"\bexam\b",name,re.I):certs=[Certification(name=re.sub(r"\s*\+\s*exam.*$","",name,flags=re.I),relationship="EXAM_INCLUDED",notes="Relationship derived directly from the IDN registry product title containing '+ Exam'.")]
    tools=[Tool(name=x) for x in strings(fact_value(facts,"tools"))]
    if not tools:
        curriculum_text=norm(json.dumps(curriculum,ensure_ascii=False));tool_rules=[("Arduino",("arduino",)),("ESP8266",("esp8266",)),("n8n",("n8n",)),("Wireless access point",("fat ap","fit ap","access point")),("WLAN controller",("wireless ac","virtual ac")),("Google Sheets",("google sheets",)),("Telegram",("telegram",))]
        tools=[Tool(name=tool) for tool,words in tool_rules if any(word in curriculum_text for word in words)]
    explicit_trainers=strings(fact_value(facts,"trainers"));trainer_refs=[]
    for t in explicit_trainers:
        raw_base=re.split(r"\s*\(",t)[0];bases={norm(raw_base),norm(raw_base.split(",")[0])};matches=[x for x in ctx.trainers if norm(x["name"]) in bases]
        if len(matches)==1:trainer_refs.append(TrainerReference(trainer_name=matches[0]["name"],kb_trainer_id=matches[0]["url"].split("id=")[-1]))
    repeat=section(snap,"Kebijakan mengulang training");repeat_origin="KB_INTERNAL" if repeat else None
    if not repeat:repeat=fact_value(facts,"repeat_policy");repeat_origin="IDN_PRIMARY" if repeat else None
    if not repeat and any("mengulang" in x.lower() for x in strings(fact_value(facts,"facilities"))):repeat="Gratis mengulang training hingga dua kali, mengikuti ketentuan dan ketersediaan batch IDN.";repeat_origin="IDN_PRIMARY"
    if not repeat:repeat="Kebijakan mengulang training perlu dikonfirmasi kepada admin IDN.";repeat_origin="SAFE_DEFAULT"
    support=section(snap,"Support pasca-training");support_origin="KB_INTERNAL" if support else None
    if not support:support=" ".join(strings(fact_value(facts,"support_information")));support_origin="IDN_PRIMARY" if support else None
    if not support:support="Support pasca-training mengikuti program dan kebijakan IDN yang berlaku.";support_origin="SAFE_DEFAULT"
    selling=(strings(fact_value(facts,"benefits"))+strings(fact_value(facts,"facilities")))[:10]
    claims=["Jangan menjanjikan harga referensi sebagai harga final.","Jangan menjanjikan trainer tertentu jika belum dikonfirmasi.","Jangan menjanjikan kelulusan sertifikasi."]
    if any(x in norm(name+" "+desc) for x in ("pentest","security","hacking")):claims.append("Praktik keamanan hanya boleh dilakukan pada sistem yang memiliki izin eksplisit.")
    if any(x in norm(name+" "+desc) for x in (" ai ","automation","machine learning")):claims.append("Jangan menjanjikan keluaran AI selalu benar atau bebas kesalahan.")
    candidates=[];name_tokens=set(norm(name).split())
    for p in ctx.products:
        if p["canonical_url"]==url:continue
        overlap=len(name_tokens&set(norm(p["name"]).split()))
        if p["category"]==source_cat and overlap:candidates.append((overlap,p))
    next_classes=[NextClass(training_name=p["name"],canonical_source_url=p["canonical_url"],reason="Katalog IDN dalam keluarga training yang sama.",confidence=min(.9,.6+.1*n)) for n,p in sorted(candidates,key=lambda x:-x[0])[:2]]
    payload=KBProductPayload(full_name=name,short_name=short,category=cat,seo_url=url,training_formats=formats,target_audiences=audiences,certifications=certs,tools=tools,next_classes=next_classes,trainer_references=trainer_refs,short_description=desc,learning_outcomes=outcomes,prerequisites=prereq,repeat_policy=repeat,practice_examples=practice,post_training_support=support,selling_points=selling,claims_to_avoid=claims,additional_notes="Informasi komersial, jadwal, lokasi, trainer, dan fasilitas perlu dikonfirmasi sebelum ditawarkan.")
    field_values=payload.model_dump();resolved={}
    direct={"full_name","seo_url","training_formats","short_description"};derived={"target_audiences","learning_outcomes","practice_examples","selling_points","claims_to_avoid","next_classes"};internal={"short_name","repeat_policy","post_training_support","trainer_references"}
    for key,value in field_values.items():
        if isinstance(value,list) and not value:resolved[key]=na(value)
        elif key=="training_formats" and format_needs_review:resolved[key]=rv(value,status=ResolutionStatus.REVIEW_REQUIRED,method=ResolutionMethod.DIRECT_FACT,source_type="IDN_PRIMARY",sources=sources,confidence=.55,needs_review=True,note="Format is missing or multiple price values lack authoritative labels.")
        elif key=="trainer_references":resolved[key]=rv(value,method=ResolutionMethod.DIRECT_FACT,source_type="IDN_TRAINER_EXACT+KB_REGISTRY",sources=sources,confidence=.98,note="Exact normalized name match only.")
        elif key=="repeat_policy":resolved[key]=rv(value,method=ResolutionMethod.INTERNAL_KB if repeat_origin=="KB_INTERNAL" else ResolutionMethod.DIRECT_FACT if repeat_origin=="IDN_PRIMARY" else ResolutionMethod.SAFE_DEFAULT,source_type=repeat_origin,sources=sources,confidence=.95 if repeat_origin!="SAFE_DEFAULT" else .7)
        elif key=="post_training_support":resolved[key]=rv(value,method=ResolutionMethod.INTERNAL_KB if support_origin=="KB_INTERNAL" else ResolutionMethod.DIRECT_FACT if support_origin=="IDN_PRIMARY" else ResolutionMethod.SAFE_DEFAULT,source_type=support_origin,sources=sources,confidence=.9 if support_origin!="SAFE_DEFAULT" else .7)
        elif key in internal:resolved[key]=rv(value,method=ResolutionMethod.INTERNAL_KB,source_type="KB_INTERNAL",sources=[SourceRef(url=(snap or {}).get("url",url),type="KB_EXISTING_PRODUCT")] if snap else sources,confidence=.9)
        elif key in derived:resolved[key]=rv(value,method=ResolutionMethod.DERIVED,source_type="IDN_FACTS",sources=sources,confidence=.82,note="Derived conservatively from extracted product facts and catalog.")
        elif key=="category":resolved[key]=rv(value,method=ResolutionMethod.INTERNAL_KB if cat_method=="INTERNAL_KB" else ResolutionMethod.DERIVED,source_type=cat_method,sources=sources,confidence=cat_conf)
        else:resolved[key]=rv(value,sources=sources)
    complete=sum(x.status in {ResolutionStatus.RESOLVED,ResolutionStatus.NOT_APPLICABLE} for x in resolved.values());completion=round(100*complete/len(resolved),2)
    conflicts=[]
    if snap and price and section(snap,"Format, durasi & harga referensi") and str(price.get("amount") if isinstance(price,dict) else price) not in re.sub(r"\D","",section(snap,"Format, durasi & harga referensi")):
        conflicts.append({"field":"training_formats","status":"SOURCE_CONFLICT","selected":"IDN_PRIMARY","alternative":"KB_EXISTING_PRODUCT"});resolved["training_formats"].conflict=True;resolved["training_formats"].alternatives=[section(snap,"Format, durasi & harga referensi")]
    needs=any(x.needs_review or x.status in {ResolutionStatus.REVIEW_REQUIRED,ResolutionStatus.UNRESOLVED} for x in resolved.values())
    result=ResolvedProduct(slug=slug,source_url=url,product_status=ProductStatus.REVIEW_REQUIRED if needs else ProductStatus.RESOLVED,completion=completion,needs_review=needs,fields=resolved,payload=payload,source_conflicts=conflicts,warnings=[] if match_status!="AMBIGUOUS" else ["Ambiguous exact KB match"])
    folder=OUT/slug;folder.mkdir(parents=True,exist_ok=True);dump=lambda n,v:(folder/n).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    dump("resolved.json",result.model_dump(mode="json"));dump("publish_payload.json",payload.model_dump(mode="json"));dump("resolution_trace.json",{"slug":slug,"match":{"status":match_status,"method":match_method},"fields":[{"field":k,"status":v.status,"method":v.method,"selected_source":v.source_type,"warnings":(["SOURCE_CONFLICT"] if v.conflict else [])} for k,v in resolved.items()]});dump("resolver_comparison.json",{"match_status":match_status,"existing_kb_product":existing,"conflicts":conflicts});dump("research.json",{"product":slug,"mode":"offline","results":[]})
    h=hashlib.sha256(json.dumps(payload.model_dump(mode="json"),sort_keys=True).encode()).hexdigest();database.upsert_resolved_product(slug=slug,status=result.product_status.value,resolution_hash=h,resolved_path=str(folder/"resolved.json"),publish_payload_path=str(folder/"publish_payload.json"),completion=completion,needs_review=needs,source_url=url)
    return result
