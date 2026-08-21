"""Resolver preflight, orchestration, and offline reporting."""
import hashlib,json,os,re
from pathlib import Path
from app.core.database import Database
from app.resolver.engine import ResolverContext,resolve_product,load
from app.resolver.models import ProductStatus,ResolutionMethod,ResolutionStatus,SourceRef,TargetAudience
from app.resolver.validation import validate_resolved_product

REQUIRED=["data/site_models/training_catalog.json","data/products","data/kb_site_models/kb_site_model.json","data/kb_site_models/kb_training_form_schema.json","data/kb_site_models/kb_existing_products.json","data/kb_site_models/kb_product_snapshots","data/kb_site_models/kb_categories.json","data/kb_site_models/kb_category_observations.json","data/kb_site_models/kb_trainers.json","data/kb_site_models/kb_policies.json","data/kb_site_models/kb_faq.json","data/kb_site_models/kb_locations.json","data/kb_site_models/kb_promos.json","data/kb_site_models/kb_idn_links.json"]
DEBUG_SLUGS=["ai-automation-tools","pentest","robotik-iot","content-creator-social-media-mastery","rcna-wlan-exam"]
def preflight(settings,database:Database,build=True):
    missing=[x for x in REQUIRED if not Path(x).exists()];database.initialize_database();ctx=None
    if not missing and build:ctx=ResolverContext()
    from app.resolver.inference import ollama_status
    from app.research.browser import BrowserResearchProvider
    ollama=ollama_status(settings);browser=BrowserResearchProvider(settings).check() if not missing else {"available":False}
    return {"pass":not missing,"missing":missing,"idn_products":sum(1 for _ in Path("data/products").glob("*/facts.json")),"kb_model":Path(REQUIRED[2]).exists(),"offline_available":not missing,"index_chunks":ctx.index["count"] if ctx else 0,"categories":len(ctx.categories) if ctx else 0,"ollama":ollama,"browser_research":browser}
def _compact_input(slug,result,ctx):
    facts=load(Path("data/products")/slug/"facts.json")
    def val(key):
        n=facts.get(key,{})
        return n.get("value") if n.get("value") is not None else n.get("values")
    def compact(v,depth=0):
        if isinstance(v,str):return v[:350]
        if isinstance(v,list):return [compact(x,depth+1) for x in v[:5]]
        if isinstance(v,dict):return {k:compact(x,depth+1) for k,x in list(v.items())[:8]}
        return v
    current=result.payload.model_dump(mode="json");semantic={k:compact(current[k]) for k in ("short_description","learning_outcomes","target_audiences","prerequisites","practice_examples","selling_points","claims_to_avoid")}
    return {"identity":{"name":result.payload.full_name,"category":result.payload.category},"facts":compact({"description":val("description"),"curriculum":val("curriculum"),"benefits":val("benefits"),"facilities":val("facilities"),"target_audiences":val("target_audiences"),"certifications":val("certifications"),"tools":val("tools"),"practice":val("practice")}),"internal_knowledge":[{"type":x["type"],"title":x["title"],"text":x["text"][:350]} for x in __import__("app.resolver.knowledge",fromlist=["retrieve"]).retrieve(ctx.index,result.payload.full_name,{"POLICY","FAQ","EXISTING_PRODUCT"},2)],"current_payload":semantic}
def _recalculate(result):
    complete=sum(x.status in {ResolutionStatus.RESOLVED,ResolutionStatus.NOT_APPLICABLE} for x in result.fields.values());result.completion=round(100*complete/len(result.fields),2);result.needs_review=any(x.needs_review or x.status in {ResolutionStatus.REVIEW_REQUIRED,ResolutionStatus.UNRESOLVED} for x in result.fields.values());result.product_status=ProductStatus.REVIEW_REQUIRED if result.needs_review else ProductStatus.RESOLVED
def _sentence_safe(text,max_length=500):
    if len(text)<=max_length:return text.strip()
    cut=text[:max_length];end=max(cut.rfind("."),cut.rfind("!"),cut.rfind("?"));return (cut[:end+1] if end>100 else cut.rsplit(" ",1)[0]+".").strip()
def _persist(result,database,inference_meta,research_meta):
    folder=Path("data/resolved_products")/result.slug;folder.mkdir(parents=True,exist_ok=True);payload=result.payload.model_dump(mode="json")
    (folder/"resolved.json").write_text(result.model_dump_json(indent=2)+"\n",encoding="utf-8");(folder/"publish_payload.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    trace={"slug":result.slug,"fields":[{"field":k,"status":v.status,"method":v.method,"selected_source":v.source_type,"note":v.note,"warnings":["SOURCE_CONFLICT"] if v.conflict else []} for k,v in result.fields.items()],"inference":inference_meta,"research":research_meta};(folder/"resolution_trace.json").write_text(json.dumps(trace,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");(folder/"inference.json").write_text(json.dumps(inference_meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");(folder/"research.json").write_text(json.dumps(research_meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    h=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest();database.upsert_resolved_product(slug=result.slug,status=result.product_status.value,resolution_hash=h,resolved_path=str(folder/"resolved.json"),publish_payload_path=str(folder/"publish_payload.json"),completion=result.completion,needs_review=result.needs_review,source_url=result.source_url)
def run_one(slug,settings,database,research=False,ctx=None,local_ai=False):
    # Local resolver runs first. Research is gap-only; a complete local result causes zero API calls.
    ctx=ctx or ResolverContext()
    result=resolve_product(slug,database,ctx)
    checkpoint_path=Path("data/resolved_products")/slug/"checkpoint.json";checkpoint={"facts_loaded":True,"local_resolved":True,"research_completed":False,"ollama_enriched":False,"validated":False,"payload_generated":False};checkpoint_path.write_text(json.dumps(checkpoint,indent=2)+"\n",encoding="utf-8")
    gaps=[name for name,value in result.fields.items() if value.needs_review];research_meta={"mode":"browser" if research else "disabled","used":False,"fetches":0,"cache_hits":0,"evidence":[]}
    if research and gaps:
        from app.research.browser import BrowserResearchProvider
        provider=BrowserResearchProvider(settings);known=[result.source_url]
        for field in gaps:
            found=provider.research_field(slug,result.payload.full_name,field,known);research_meta["evidence"].extend(found["evidence"])
        research_meta.update(used=bool(research_meta["evidence"]),fetches=provider.fetches,cache_hits=provider.cache_hits)
        fmt=result.payload.training_formats[0] if result.payload.training_formats else None
        if fmt and fmt.format=="UNKNOWN":
            observed=set()
            for evidence in research_meta["evidence"]:
                text=evidence["evidence"]
                for match in re.finditer(r"(?:format|metode|pelaksanaan|kelas)\s+(?:training\s+)?(?:adalah\s+|:\s*)?(hybrid|offline|online)\b|\b(hybrid|offline|online)\s+(?:class|kelas)\b",text,re.I):observed.add((match.group(1) or match.group(2)).lower().title())
            if len(observed)==1:fmt.format=observed.pop();field=result.fields["training_formats"];field.sources.extend([SourceRef(url=e["url"],type=e["authority"],title=e["title"]) for e in research_meta["evidence"]]);field.method=ResolutionMethod.OFFICIAL_RESEARCH;field.source_type="IDN_PRIMARY";field.note="Explicit format found in official IDN source.";field.confidence=.9
            if len(observed)==0 and fmt.format=="UNKNOWN":pass
        # Price candidates without labels intentionally remain review-required.
        if fmt and fmt.format!="UNKNOWN" and "labels must be reviewed" not in (fmt.price_note or ""):
            field=result.fields["training_formats"];field.status=ResolutionStatus.RESOLVED;field.needs_review=False
    checkpoint["research_completed"]=True;checkpoint_path.write_text(json.dumps(checkpoint,indent=2)+"\n",encoding="utf-8")
    inference_meta={"provider":"NONE","used":False,"fallback":False}
    if local_ai or research:
        from app.resolver.inference import RuleBasedInferenceProvider,select_inference_provider,semantic_field_errors
        provider,status=select_inference_provider(settings)
        try:enriched,inference_meta=provider.enrich(_compact_input(slug,result,ctx))
        except Exception as exc:
            enriched,inference_meta=RuleBasedInferenceProvider().enrich(_compact_input(slug,result,ctx));inference_meta["ollama_error"]=str(exc);inference_meta["fallback"]=True
        inference_meta["used"]=provider.provider_name=="OLLAMA" and inference_meta.get("provider")=="OLLAMA";field_fallbacks=semantic_field_errors(enriched)
        p=result.payload;original=p.model_copy(deep=True);p.short_description=_sentence_safe(enriched.short_description)
        p.learning_outcomes=original.learning_outcomes if "learning_outcomes" in field_fallbacks or not enriched.learning_outcomes else enriched.learning_outcomes
        p.target_audiences=[TargetAudience.model_validate(x.model_dump()) for x in enriched.target_audiences] if enriched.target_audiences else original.target_audiences
        p.prerequisites=enriched.prerequisites or original.prerequisites;p.practice_examples=enriched.practice_examples or original.practice_examples
        p.selling_points=original.selling_points if "selling_points" in field_fallbacks or not enriched.selling_points else enriched.selling_points
        # Deterministic claims are mandatory; Ollama may refine but cannot remove them.
        p.claims_to_avoid=list(dict.fromkeys([*p.claims_to_avoid,*enriched.claims_to_avoid]))
        inference_meta["field_fallbacks"]=sorted(field_fallbacks);inference_meta["fallback"]=bool(inference_meta.get("fallback") or field_fallbacks)
        for key in ("short_description","learning_outcomes","target_audiences","prerequisites","practice_examples","selling_points"):
            result.fields[key].value=getattr(p,key);used_ollama=inference_meta.get("provider")=="OLLAMA" and key not in field_fallbacks and (getattr(enriched,key) or key=="short_description");result.fields[key].method=ResolutionMethod.LOCAL_INFERENCE if used_ollama else ResolutionMethod.DERIVED;result.fields[key].source_type="OLLAMA" if used_ollama else "RULE_BASED"
    checkpoint["ollama_enriched"]=bool(inference_meta.get("provider")=="OLLAMA");checkpoint_path.write_text(json.dumps(checkpoint,indent=2)+"\n",encoding="utf-8")
    facts=load(Path("data/products")/slug/"facts.json");errors=validate_resolved_product(result,ctx,facts);result.warnings=list(dict.fromkeys([*result.warnings,*errors]));_recalculate(result);_persist(result,database,inference_meta,research_meta);checkpoint["validated"]=True;checkpoint["payload_generated"]=True;checkpoint_path.write_text(json.dumps(checkpoint,indent=2)+"\n",encoding="utf-8");return result,{"calls":1 if inference_meta.get("provider")=="OLLAMA" and not inference_meta.get("cache_hit") else 0,"cache_hits":int(bool(inference_meta.get("cache_hit"))),"research_fetches":research_meta["fetches"],"fallback":bool(inference_meta.get("fallback")),"provider":inference_meta.get("provider")}
def run_batch(limit,settings,database,research=False,local_ai=False):
    ctx=ResolverContext();available={x.parent.name for x in Path("data/products").glob("*/facts.json")};ordered=[x for x in DEBUG_SLUGS if x in available]+sorted(available-set(DEBUG_SLUGS));results=[]
    for slug in ordered[:limit if limit is not None else len(ordered)]:results.append(run_one(slug,settings,database,research,ctx,local_ai)[0])
    return results
def build_report(database:Database):
    rows=database.list_resolved_products();methods={};conflicts=0
    for row in rows:
        p=Path(row["resolved_path"])
        if not p.exists():continue
        data=json.loads(p.read_text(encoding="utf-8"));conflicts+=len(data.get("source_conflicts",[]))
        for f in data["fields"].values():methods[f["method"]]=methods.get(f["method"],0)+1
    inference={"ollama":0,"rule_based":0,"cache_hits":0};research={"fetches":0,"cache_hits":0}
    for row in rows:
        folder=Path(row["resolved_path"]).parent
        if (folder/"inference.json").exists():
            m=json.loads((folder/"inference.json").read_text(encoding="utf-8"));inference["ollama"]+=int(m.get("provider")=="OLLAMA");inference["rule_based"]+=int(m.get("provider")=="RULE_BASED");inference["cache_hits"]+=int(bool(m.get("cache_hit")))
        if (folder/"research.json").exists():
            m=json.loads((folder/"research.json").read_text(encoding="utf-8"));research["fetches"]+=m.get("fetches",0);research["cache_hits"]+=m.get("cache_hits",0)
    report={"products_available":sum(1 for _ in Path("data/products").glob("*/facts.json")),"products_resolved":sum(x["status"]=="RESOLVED" for x in rows),"review_required":sum(x["needs_review"] for x in rows),"failed":sum(x["status"]=="FAILED" for x in rows),"average_completion":round(sum(x["completion"] for x in rows)/len(rows),2) if rows else 0,"methods":methods,"source_conflicts":conflicts,"research":research,"inference":inference,"no_kb_writes":True}
    out=Path("data/resolver/resolver_report.json");out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8");return report
