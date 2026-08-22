import hashlib,json,re,unicodedata
from datetime import datetime,timezone,timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qsl,urljoin,urlsplit,urlunsplit,urlencode

from bs4 import BeautifulSoup

from app.browser.manager import BrowserManager
from app.kb.auth import classify_auth_page,wait_for_manual_auth
from app.kb.discovery import clean,parse_detail,parse_resource_cards
from app.kb.guard import ReadOnlyGuard
from app.kb.models import AuthState
from app.identity.models import Candidate,IdentityDecision,IdentityResult,MatchMethod

LIVE=Path("data/runtime_indexes/kb_products_live.json");DUPLICATES=Path("data/runtime_indexes/kb_duplicate_audit.json");ROUTES=Path("data/publish_routes")
TRACKING={"utm_source","utm_medium","utm_campaign","utm_term","utm_content","gclid","fbclid"}

def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def load(path):return json.loads(Path(path).read_text(encoding="utf-8"))
def canonical_url(value:str|None)->str|None:
    if not value:return None
    p=urlsplit(value.strip());query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in TRACKING]
    path=re.sub(r"/+","/",p.path).rstrip("/") or "/"
    return urlunsplit((p.scheme.lower() or "https",p.netloc.lower().removeprefix("www."),path,urlencode(query),""))
def normalize_name(value:str)->str:
    value=unicodedata.normalize("NFKC",value).lower().replace("&"," and ")
    value=re.sub(r"[\u2010-\u2015\u2212]","-",value);value=re.sub(r"\s*-\s*","-",value)
    value=re.sub(r"(?<=\d)[.,](?=\d)",".",value);return " ".join(re.findall(r"[a-z0-9]+(?:[.-][a-z0-9]+)*",value))
def product_id(url):
    return dict(parse_qsl(urlsplit(url).query)).get("id")
def inventory_hash(products):return hashlib.sha256(json.dumps(products,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def _auth(page,url):
    page.goto(url,wait_until="domcontentloaded",timeout=60_000);page.wait_for_timeout(700);state=classify_auth_page(page.content(),page.url)
    if state==AuthState.AUTHENTICATED:return
    if wait_for_manual_auth(page)!=AuthState.AUTH_RESTORED:raise RuntimeError("KB authentication failed")

def _parse_edit_url(html,base,expected_id):
    soup=BeautifulSoup(html,"html.parser")
    for a in soup.select('a[href*="/kb/training/edit"]'):
        url=urljoin(base,a.get("href",""));params=dict(parse_qsl(urlsplit(url).query))
        if params.get("id")==expected_id:return url
    return None

def _discover_edit_url(page,detail_url,expected_id):
    """Follow the observed read-only Ubah control once; never fill or submit."""
    button=page.get_by_role("button",name="Ubah",exact=True)
    if not button.count():return None
    button.first.click();page.wait_for_timeout(400)
    url=page.url;params=dict(parse_qsl(urlsplit(url).query))
    valid="/kb/training/edit" in urlsplit(url).path and params.get("id")==expected_id and "new" not in params
    discovered=url if valid else None
    page.goto(detail_url,wait_until="domcontentloaded",timeout=60_000);page.wait_for_timeout(200)
    return discovered

def refresh_live_index(settings)->dict:
    """Read-only live inventory. It never visits create or edit routes."""
    manager=BrowserManager(settings.browser_profile_path,False);guard=ReadOnlyGuard();products=[];generated=datetime.now(timezone.utc).isoformat()
    try:
        manager.start();guard.install(manager.context);page=manager.new_page();_auth(page,settings.kb_training_url)
        page.wait_for_selector('a[href*="/kb/training/detail"]',timeout=20_000)
        seen={};rounds=0
        while rounds<30:
            for p in parse_resource_cards(page.content(),settings.kb_base_url,"/kb/training/detail"):seen[p["url"]]=p
            next_link=page.locator('a[rel="next"], a:has-text("Next"), a:has-text("Berikutnya")')
            if not next_link.count() or next_link.first.get_attribute("aria-disabled")=="true":break
            href=next_link.first.get_attribute("href")
            if not href:break
            _auth(page,urljoin(settings.kb_base_url,href));rounds+=1
        category_path=Path("data/kb_site_models/kb_categories.json");categories=[x["label"] for x in load(category_path).get("categories",[]) if x.get("label")] if category_path.exists() else []
        for item in seen.values():
            _auth(page,item["url"]);html=page.content();detail=parse_detail(html,item["url"]);kid=product_id(item["url"])
            seo=next((u for u in detail["external_links"] if urlsplit(u).hostname in {"idn.id","www.idn.id"}),None)
            edit=_parse_edit_url(html,settings.kb_base_url,kid) or _discover_edit_url(page,item["url"],kid)
            preview=item.get("preview","");category=next((x for x in categories if re.search(rf"\b{re.escape(x)}\b",preview,re.I)),None)
            products.append({"kb_product_id":kid,"name":item["name"],"short_name":item.get("short_name"),"category":category,"detail_url":item["url"],"edit_url":edit,"seo_url":seo,"canonical_seo_url":canonical_url(seo),"normalized_name":normalize_name(item["name"]),"active":None,"snapshot":detail})
        if not products:raise RuntimeError("LIVE_INVENTORY_EMPTY: refusing to replace the last valid inventory")
        data={"schema_version":"kb-live-products-v1","generated_at":generated,"count":len(products),"inventory_hash":inventory_hash(products),"products":products,"read_only":{"guard":"ENABLED","blocked_requests":guard.blocked,"server_writes":0}}
        save(LIVE,data);return data
    finally:manager.stop()

def audit_duplicates(inventory=None)->dict:
    inventory=inventory or load(LIVE);groups=[]
    for reason,key in (("SAME_CANONICAL_URL","canonical_seo_url"),("SAME_NORMALIZED_NAME","normalized_name")):
        by={}
        for p in inventory["products"]:
            if p.get(key):by.setdefault(p[key],[]).append(p)
        groups.extend({"reason":reason,"identity":value,"products":rows} for value,rows in by.items() if len(rows)>1)
    result={"generated_at":datetime.now(timezone.utc).isoformat(),"inventory_hash":inventory["inventory_hash"],"duplicate_groups":groups};save(DUPLICATES,result);return result

def resolve_identity(slug,payload,inventory)->IdentityResult:
    source_name=payload["full_name"];source_url=payload["seo_url"];cu=canonical_url(source_url);nn=normalize_name(source_name);products=inventory["products"]
    exact_url=[p for p in products if cu and p.get("canonical_seo_url")==cu]
    if len(exact_url)==1:return IdentityResult(source_slug=slug,source_name=source_name,source_url=source_url,decision=IdentityDecision.UPDATE_EXISTING,confidence=1,match_method=MatchMethod.EXACT_CANONICAL_URL,existing_product=exact_url[0])
    if len(exact_url)>1:return IdentityResult(source_slug=slug,source_name=source_name,source_url=source_url,decision=IdentityDecision.REVIEW_REQUIRED,confidence=1,match_method=MatchMethod.EXACT_CANONICAL_URL,candidate_matches=[Candidate(kb_product_id=x["kb_product_id"],name=x["name"],detail_url=x["detail_url"],score=1,reasons=["duplicate canonical URL"]) for x in exact_url],warnings=["AMBIGUOUS_EXACT_URL"])
    exact_name=[p for p in products if p["normalized_name"]==nn]
    if len(exact_name)==1:
        warnings=[];links=load("data/kb_site_models/kb_idn_links.json") if Path("data/kb_site_models/kb_idn_links.json").exists() else {"links":[]}
        if any(x.get("kb_product")==exact_name[0]["name"] and x.get("status")=="UNMATCHED" for x in links["links"]):warnings.append("STALE_KB_LINK_MAPPING")
        return IdentityResult(source_slug=slug,source_name=source_name,source_url=source_url,decision=IdentityDecision.UPDATE_EXISTING,confidence=.99,match_method=MatchMethod.EXACT_NORMALIZED_NAME,existing_product=exact_name[0],warnings=warnings)
    if len(exact_name)>1:return IdentityResult(source_slug=slug,source_name=source_name,source_url=source_url,decision=IdentityDecision.REVIEW_REQUIRED,confidence=.99,match_method=MatchMethod.EXACT_NORMALIZED_NAME,candidate_matches=[Candidate(kb_product_id=x["kb_product_id"],name=x["name"],detail_url=x["detail_url"],score=.99,reasons=["duplicate normalized name"]) for x in exact_name],warnings=["AMBIGUOUS_EXACT_NAME"])
    candidates=[]
    aliases={normalize_name(payload.get("short_name") or ""),nn};aliases.discard("")
    for p in products:
        p_aliases={p["normalized_name"],normalize_name(p.get("short_name") or "")};score=max(SequenceMatcher(None,nn,x).ratio() for x in p_aliases if x)
        reasons=[]
        if aliases&p_aliases:score=max(score,.9);reasons.append("alias")
        if score>=.72:candidates.append(Candidate(kb_product_id=p["kb_product_id"],name=p["name"],detail_url=p["detail_url"],score=round(score,3),reasons=reasons or ["fuzzy name"]))
    candidates.sort(key=lambda x:x.score,reverse=True)
    if candidates:return IdentityResult(source_slug=slug,source_name=source_name,source_url=source_url,decision=IdentityDecision.REVIEW_REQUIRED,confidence=candidates[0].score,match_method=MatchMethod.ALIAS_CANDIDATE if candidates[0].reasons==["alias"] else MatchMethod.FUZZY_CANDIDATE,candidate_matches=candidates[:5],warnings=["POSSIBLE_DUPLICATE"])
    return IdentityResult(source_slug=slug,source_name=source_name,source_url=source_url,decision=IdentityDecision.CREATE_NEW,confidence=1,match_method=MatchMethod.NO_CANDIDATE)

HIGH_RISK={"training_formats","trainer_references","certifications","repeat_policy","active"}
def build_diff(payload,existing):
    old=existing.get("snapshot",{});sections={x.get("heading"):x.get("content") for x in old.get("sections",[])};mapping={"short_description":"Deskripsi singkat","repeat_policy":"Kebijakan mengulang training","post_training_support":"Support pasca-training"};rows=[];blocked=False
    for field,new in payload.items():
        current=sections.get(mapping.get(field,""))
        if new in (None,"",[]):classification="PRESERVE_EXISTING" if current else "UNCHANGED"
        elif current in (None,"",[]):classification="FILL_EMPTY"
        elif str(new).strip()==str(current).strip():classification="UNCHANGED"
        else:classification="CONFLICT" if field in HIGH_RISK else "UPDATE_VALUE";blocked|=classification=="CONFLICT"
        rows.append({"field":field,"classification":classification,"existing":current,"proposed":new})
    return {"policy":"PRESERVE_EXISTING_ON_EMPTY","fields":rows,"high_risk_conflict":blocked}

def route_product(slug,settings,database,inventory=None):
    inventory=inventory or load(LIVE);quality=load(Path("data/publish_ready")/slug/"quality_report.json");payload=load(Path("data/publish_ready")/slug/"publish_payload.json");identity=resolve_identity(slug,payload,inventory);existing=identity.existing_product;target=None
    if identity.decision==IdentityDecision.UPDATE_EXISTING:target=(existing or {}).get("edit_url")
    elif identity.decision==IdentityDecision.CREATE_NEW:target=settings.kb_training_create_url
    warnings=list(identity.warnings)
    if identity.decision==IdentityDecision.UPDATE_EXISTING and not target:warnings.append("EDIT_ROUTE_NOT_DISCOVERED")
    diff=build_diff(payload,existing) if existing else {"policy":"CREATE","fields":[],"high_risk_conflict":False}
    if diff["high_risk_conflict"]:warnings.append("HIGH_RISK_CONFLICT")
    dry_allowed=quality["publish_readiness"]=="READY" and identity.decision!=IdentityDecision.REVIEW_REQUIRED and bool(target)
    blocking=[x["field"] for x in diff["fields"] if x["classification"]=="CONFLICT"]
    live_allowed=dry_allowed and not blocking
    checked=datetime.now(timezone.utc).isoformat();route={"schema_version":"publish-route-v2","slug":slug,"publish_readiness":quality["publish_readiness"],"identity_decision":identity.decision.value,"kb_product_id":(existing or {}).get("kb_product_id"),"target_url":target if identity.decision!=IdentityDecision.REVIEW_REQUIRED else None,"match_method":identity.match_method.value,"confidence":identity.confidence,"inventory_hash":inventory["inventory_hash"],"checked_against_live_kb_at":checked,"route_ttl_seconds":3600,"warnings":warnings,"candidate_matches":[x.model_dump() for x in identity.candidate_matches],"blocking_conflicts":blocking,"dry_run_allowed":dry_allowed,"live_publish_allowed":live_allowed,"publisher_allowed":live_allowed}
    out=ROUTES/slug;save(out/"route.json",route);save(out/"diff.json",diff);database.upsert_publish_route(slug=slug,decision=identity.decision.value,kb_product_id=route["kb_product_id"],match_method=route["match_method"],confidence=route["confidence"],inventory_hash=route["inventory_hash"],checked_at=checked,route_path=str(out/"route.json"));return route,identity,diff

def route_is_fresh(route,inventory,max_age=3600):
    checked=datetime.fromisoformat(route["checked_against_live_kb_at"]);return datetime.now(timezone.utc)-checked<=timedelta(seconds=max_age) and route["inventory_hash"]==inventory["inventory_hash"]

def publisher_preflight(slug,inventory=None):
    """Reusable fail-closed boundary for a future publisher; performs no navigation or writes."""
    inventory=inventory or load(LIVE);path=ROUTES/slug/"route.json"
    if not path.exists():return {"allowed":False,"reason":"ROUTE_MISSING"}
    route=load(path)
    if not route_is_fresh(route,inventory,route.get("route_ttl_seconds",3600)):return {"allowed":False,"reason":"ROUTE_STALE"}
    if route.get("publish_readiness")!="READY":return {"allowed":False,"dry_run_allowed":False,"live_publish_allowed":False,"reason":"QUALITY_NOT_READY"}
    if route.get("identity_decision")=="REVIEW_REQUIRED":return {"allowed":False,"reason":"IDENTITY_REVIEW_REQUIRED"}
    if not route.get("target_url"):return {"allowed":False,"reason":"TARGET_ROUTE_MISSING"}
    dry=bool(route.get("dry_run_allowed",route.get("publisher_allowed")));live=bool(route.get("live_publish_allowed",route.get("publisher_allowed")))
    if not dry:return {"allowed":False,"dry_run_allowed":False,"live_publish_allowed":live,"reason":"DRY_RUN_BLOCKED"}
    return {"allowed":True,"dry_run_allowed":dry,"live_publish_allowed":live,"reason":"READY" if live else "HIGH_RISK_CONFLICT_PRESERVED"}
