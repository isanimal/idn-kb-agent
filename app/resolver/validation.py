"""Pre-publish contract checks; does not publish."""
import re
from urllib.parse import urlparse
from app.resolver.inference import SemanticEnrichment,indonesian_language_ok
from app.resolver.models import ResolvedProduct
def validate_resolved_product(result:ResolvedProduct,ctx,facts:dict)->list[str]:
    errors=[];p=result.payload
    if p.category not in ctx.categories:errors.append("CATEGORY_OUTSIDE_KB_TAXONOMY")
    ids={x["url"].split("id=")[-1] for x in ctx.trainers}
    if any(x.kb_trainer_id not in ids for x in p.trainer_references):errors.append("UNKNOWN_TRAINER_ID")
    urls={x["canonical_url"] for x in ctx.products}
    if any(x.canonical_source_url not in urls for x in p.next_classes):errors.append("NEXT_CLASS_OUTSIDE_IDN_CATALOG")
    for url in [p.seo_url,*[x.url for x in p.advertising_links]]:
        if url and urlparse(url).scheme not in {"http","https"}:errors.append("INVALID_URL")
    for f in p.training_formats:
        if any(x is not None and (not isinstance(x,int) or x<0) for x in (f.public_price_reference,f.private_price_reference)):errors.append("INVALID_PRICE")
    clip=lambda value:str(value)[:350]
    audiences=[{k:clip(v) if v is not None else None for k,v in x.model_dump().items()} for x in p.target_audiences[:3]]
    semantic=SemanticEnrichment(short_description=clip(p.short_description),learning_outcomes=[clip(x) for x in p.learning_outcomes[:5]],target_audiences=audiences,prerequisites=[clip(x) for x in p.prerequisites[:4]],practice_examples=[clip(x) for x in p.practice_examples[:4]],selling_points=[clip(x) for x in p.selling_points[:5]],claims_to_avoid=[clip(x) for x in p.claims_to_avoid[:5]])
    if not indonesian_language_ok(semantic):errors.append("OUTPUT_LANGUAGE_MISMATCH")
    title=((facts.get("identity") or {}).get("full_name") or {}).get("value","")
    cert_evidence=((facts.get("certifications") or {}).get("evidence") or [])
    if any(x.relationship=="EXAM_INCLUDED" for x in p.certifications) and not re.search(r"\bexam\b",title,re.I) and not cert_evidence:errors.append("UNSUPPORTED_EXAM_INCLUDED")
    return sorted(set(errors))
