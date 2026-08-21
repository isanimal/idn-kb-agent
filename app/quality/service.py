"""Offline-first quality orchestration. This module never mutates resolver artifacts."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.quality.models import QualityReport
from app.quality.validator import evaluate, sanitize_payload
from app.resolver.inference import SemanticEnrichment, extract_json_object, indonesian_language_ok, ollama_status
from app.resolver.models import KBProductPayload, ResolvedProduct, TargetAudience

RESOLVED_ROOT = Path("data/resolved_products")
OUTPUT_ROOT = Path("data/publish_ready")
CACHE_ROOT = Path("data/inference_cache")
PROMPT_VERSION = "quality-repair-v1"
DEBUG_SLUGS = ["robotik-iot", "pentest", "ai-automation-tools", "content-creator-social-media-mastery", "rcna-wlan-exam"]


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _semantic(payload: KBProductPayload) -> SemanticEnrichment:
    return SemanticEnrichment(
        short_description=payload.short_description,
        learning_outcomes=payload.learning_outcomes[:5],
        target_audiences=[x.model_dump() for x in payload.target_audiences[:3]],
        prerequisites=payload.prerequisites[:4],
        practice_examples=payload.practice_examples[:4],
        selling_points=payload.selling_points[:5],
        claims_to_avoid=payload.claims_to_avoid[:5],
    )


def _repair_input(result: ResolvedProduct, payload: KBProductPayload, facts: dict, report: QualityReport) -> dict:
    evidence = {k:facts.get(k) for k in ("identity","description","curriculum","benefits","target_audiences","prerequisites","tools","practice")}
    return {"product":{"slug":result.slug,"name":payload.full_name,"category":payload.category},"factual_evidence":evidence,"current_semantic_fields":_semantic(payload).model_dump(),"quality_violations":[x.model_dump() for x in [*report.errors,*report.warnings]]}


def _ollama_repair(settings, input_data: dict, client=None) -> tuple[SemanticEnrichment, dict]:
    raw_input=json.dumps(input_data,ensure_ascii=False,sort_keys=True)
    key=hashlib.sha256(f"{settings.ollama_model}|{PROMPT_VERSION}|{raw_input}".encode()).hexdigest();path=CACHE_ROOT/f"{key}.json"
    if path.exists():
        cached=_load(path);return SemanticEnrichment.model_validate(cached["output"]),cached["metadata"]|{"cache_hit":True,"calls":0}
    status=ollama_status(settings,client=client)
    if not settings.ollama_enabled or not status["runtime"] or not status["model_installed"]:
        raise RuntimeError("Configured local Ollama model is unavailable")
    prompt=Path("prompts/resolver/quality_repair.txt").read_text(encoding="utf-8").replace("{{INPUT_JSON}}",json.dumps(input_data,ensure_ascii=False,separators=(",",":")))
    response=(client or httpx.Client(timeout=settings.ollama_timeout_seconds)).post(settings.ollama_base_url.rstrip("/")+"/api/chat",json={"model":settings.ollama_model,"stream":False,"think":False,"format":SemanticEnrichment.model_json_schema(),"messages":[{"role":"user","content":prompt}],"options":{"num_ctx":min(settings.ollama_context_size,8192),"temperature":settings.ollama_temperature,"num_predict":700}})
    response.raise_for_status();body=response.json();output=SemanticEnrichment.model_validate(extract_json_object(body["message"]["content"]))
    if not indonesian_language_ok(output):raise ValueError("OUTPUT_LANGUAGE_MISMATCH")
    meta={"provider":"OLLAMA","model":settings.ollama_model,"prompt_version":PROMPT_VERSION,"created_at":datetime.now(timezone.utc).isoformat(),"cache_hit":False,"calls":1}
    CACHE_ROOT.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps({"metadata":meta,"output":output.model_dump()},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return output,meta


def _apply_semantic(payload: KBProductPayload, repaired: SemanticEnrichment) -> KBProductPayload:
    candidate=payload.model_copy(deep=True)
    candidate.short_description=repaired.short_description
    candidate.learning_outcomes=list(repaired.learning_outcomes)
    candidate.target_audiences=[TargetAudience.model_validate(x.model_dump()) for x in repaired.target_audiences]
    candidate.prerequisites=list(repaired.prerequisites)
    candidate.practice_examples=list(repaired.practice_examples)
    candidate.selling_points=list(repaired.selling_points)
    candidate.claims_to_avoid=list(dict.fromkeys([*candidate.claims_to_avoid,*repaired.claims_to_avoid]))
    return candidate


def run_one(slug: str, settings, repair: bool=False, client=None) -> tuple[QualityReport, dict]:
    folder=RESOLVED_ROOT/slug
    if not (folder/"resolved.json").exists():raise FileNotFoundError(f"No resolver artifact for slug: {slug}")
    result=ResolvedProduct.model_validate(_load(folder/"resolved.json"));facts=_load(Path("data/products")/slug/"facts.json")
    original_hash=hashlib.sha256((folder/"resolved.json").read_bytes()).hexdigest()
    payload,changes=sanitize_payload(result,facts);initial=evaluate(result,payload,facts,changes)
    report=initial;repair_meta={"requested":repair,"attempted":False,"calls":0,"cache_hit":False,"accepted":False}
    if repair and (initial.errors or initial.warnings):
        repair_meta["attempted"]=True
        try:
            enriched,meta=_ollama_repair(settings,_repair_input(result,payload,facts,initial),client)
            repair_meta.update(meta)
            candidate=_apply_semantic(payload,enriched);candidate,_=sanitize_payload(result,facts) if False else (candidate,[])
            candidate_report=evaluate(result,candidate,facts,changes)
            if candidate_report.score>initial.score or (candidate_report.score==initial.score and len(candidate_report.errors)+len(candidate_report.warnings)<len(initial.errors)+len(initial.warnings)):
                payload,report=candidate,candidate_report;repair_meta["accepted"]=True
        except Exception as exc:repair_meta["error"]=str(exc)
    out=OUTPUT_ROOT/slug;out.mkdir(parents=True,exist_ok=True)
    trace={"schema_version":"quality-trace-v1","slug":slug,"generated_at":datetime.now(timezone.utc).isoformat(),"source_resolved_sha256":original_hash,"deterministic_changes":changes,"before":{"payload":result.payload.model_dump(mode="json"),"quality":initial.model_dump(mode="json")},"after":{"payload":payload.model_dump(mode="json"),"quality":report.model_dump(mode="json")},"repair":repair_meta,"kb_writes":0}
    (out/"publish_payload.json").write_text(json.dumps(payload.model_dump(mode="json"),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (out/"quality_report.json").write_text(report.model_dump_json(indent=2)+"\n",encoding="utf-8")
    (out/"quality_trace.json").write_text(json.dumps(trace,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if hashlib.sha256((folder/"resolved.json").read_bytes()).hexdigest()!=original_hash:raise RuntimeError("Resolver source artifact changed during quality evaluation")
    return report,trace


def run_batch(limit: int | None, settings, repair: bool=False) -> list[tuple[QualityReport,dict]]:
    available={p.parent.name for p in RESOLVED_ROOT.glob("*/resolved.json")};ordered=[x for x in DEBUG_SLUGS if x in available]
    selected=ordered[:limit if limit is not None else len(ordered)]
    return [run_one(slug,settings,repair) for slug in selected]


def build_report() -> dict:
    reports=[];repairs=[]
    for path in OUTPUT_ROOT.glob("*/quality_report.json"):
        reports.append(_load(path))
        trace=path.parent/"quality_trace.json"
        if trace.exists():repairs.append(_load(trace).get("repair",{}))
    counts={x:sum(r["publish_readiness"]==x for r in reports) for x in ("READY","REVIEW_REQUIRED","BLOCKED")}
    codes={}
    for report in reports:
        for issue in [*report.get("errors",[]),*report.get("warnings",[])]:codes[issue["code"]]=codes.get(issue["code"],0)+1
    return {"products_evaluated":len(reports),**counts,"average_quality":round(sum(x["score"] for x in reports)/len(reports),2) if reports else 0,"issues":codes,"ollama":{"repair_calls":sum(x.get("calls",0) for x in repairs),"cache_hits":sum(bool(x.get("cache_hit")) for x in repairs),"failures":sum(bool(x.get("error")) for x in repairs)},"kb_writes":0}

