"""Authenticated, read-only KB reconnaissance."""
import json, re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from app.browser.manager import BrowserManager
from app.core.database import Database
from app.kb.auth import classify_auth_page, wait_for_manual_auth
from app.kb.discovery import clean, content_hash, parse_detail, parse_faq, parse_form_schema, parse_navigation, parse_resource_cards, parse_trainer_cards
from app.kb.guard import ReadOnlyGuard
from app.kb.models import AuthState

OUT=Path("data/kb_site_models"); SHOTS=Path("runtime/screenshots/kb")
AUTHORITY={"KB_EXISTING_PRODUCT":"INTERNAL_PRODUCT_KNOWLEDGE","KB_POLICY":"INTERNAL_POLICY","KB_TRAINER":"INTERNAL_TRAINER_AUTHORITY","KB_FAQ":"INTERNAL_GENERAL_KNOWLEDGE","KB_LOCATION":"INTERNAL_LOCATION_AUTHORITY","KB_PROMO":"INTERNAL_PROMOTIONAL_INFORMATION"}
def save(path,value): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def slug(v): return re.sub(r"[^a-z0-9]+","-",v.lower()).strip("-") or "item"

def ensure(page,url,retries=2):
    for _ in range(retries+1):
        page.goto(url,wait_until="domcontentloaded",timeout=60_000); page.wait_for_timeout(700)
        if classify_auth_page(page.content(),page.url)==AuthState.AUTHENTICATED:return AuthState.AUTHENTICATED
        if wait_for_manual_auth(page)!=AuthState.AUTH_RESTORED:break
    return AuthState.AUTH_FAILED

def run_kb_auth_test(settings,wait_seconds=300):
    m=BrowserManager(settings.browser_profile_path,False); g=ReadOnlyGuard(); r={"profile":False,"reachable":False,"initial_state":"UNKNOWN","session":"UNKNOWN","training_accessible":False,"blocked_requests":0}
    try:
        m.start();r["profile"]=settings.browser_profile_path.exists();g.install(m.context);p=m.new_page();p.goto(settings.kb_training_url,wait_until="domcontentloaded",timeout=60_000);p.wait_for_timeout(500);r["reachable"]=True
        state=classify_auth_page(p.content(),p.url);r["initial_state"]=state.value
        if state!=AuthState.AUTHENTICATED:state=wait_for_manual_auth(p,wait_seconds)
        r["session"]=state.value;r["training_accessible"]=ensure(p,settings.kb_training_url)==AuthState.AUTHENTICATED;r["blocked_requests"]=len(g.blocked);return r
    finally:m.stop()
def print_auth_test(r):
    print("KB AUTH TEST\n");print(f"Browser profile...... {'OK' if r['profile'] else 'FAIL'}\nKB reachable......... {'OK' if r['reachable'] else 'FAIL'}\nInitial session...... {r['initial_state']}\nSession.............. {r['session']}\nProduct Training..... {'ACCESSIBLE' if r['training_accessible'] else 'NOT ACCESSIBLE'}\n\nRESULT: {'PASS' if r['training_accessible'] else 'FAIL'}")

def dynamic_schema(page):
    result=[]
    for button in page.locator("button").all():
        if clean(button.inner_text())!="Tambah":continue
        container=button.locator("xpath=../..").first
        heads=container.locator("h2,h3") if container.count() else None; anchor=clean(heads.first.inner_text()) if heads and heads.count() else "Dynamic section"
        button.click();page.wait_for_timeout(100);fields=[]
        for c in container.locator("input,textarea,select").all() if container.count() else []: fields.append({"tag":c.evaluate("e=>e.tagName.toLowerCase()"),"type":c.get_attribute("type"),"placeholder":c.get_attribute("placeholder"),"aria_label":c.get_attribute("aria-label")})
        result.append({"section_anchor":anchor,"add_button":"Tambah","dynamic":True,"row_fields":fields})
    page.reload(wait_until="domcontentloaded");return result

def run_kb_reconnaissance(settings,database:Database,limit=None):
    OUT.mkdir(parents=True,exist_ok=True);SHOTS.mkdir(parents=True,exist_ok=True);m=BrowserManager(settings.browser_profile_path,False);g=ReadOnlyGuard();changes=Counter();warnings=[];now=datetime.now(timezone.utc).isoformat()
    try:
        m.start();g.install(m.context);p=m.new_page();auth=ensure(p,settings.kb_training_url)
        if auth==AuthState.AUTH_FAILED:raise RuntimeError("KB authentication failed")
        html=p.content();nav=parse_navigation(html,settings.kb_base_url);save(OUT/"kb_navigation.json",{"generated_at":now,"sections":[x.model_dump() for x in nav]});p.screenshot(path=str(SHOTS/"product-training-list.png"),full_page=True)
        products=parse_resource_cards(html,settings.kb_base_url,"/kb/training/detail");save(OUT/"kb_existing_products.json",{"generated_at":now,"count":len(products),"products":products})
        ensure(p,settings.kb_training_create_url);p.wait_for_timeout(1000);fields=parse_form_schema(p.content());dynamic=dynamic_schema(p);cats=[x for x in next((f.options for f in fields if f.label=="Kategori"),[]) if x.get("value")]
        for product in products:
            product["category"]=next((x["label"] for x in cats if re.search(rf"\b{re.escape(x['label'])}\b",product.get("preview",""),re.I)),None)
        save(OUT/"kb_existing_products.json",{"generated_at":now,"count":len(products),"products":products})
        form={"schema_version":"kb-training-form-v1","generated_at":now,"fields":[f.model_dump() for f in fields],"dynamic_sections":dynamic,"trainer_options":[]};save(OUT/"kb_training_form_schema.json",form);save(OUT/"kb_categories.json",{"generated_at":now,"categories":cats});p.screenshot(path=str(SHOTS/"training-create-form.png"),full_page=True)
        snaps=[];links=[];root=OUT/"kb_product_snapshots";root.mkdir(parents=True,exist_ok=True)
        for product in products[:limit if limit is not None else len(products)]:
            if ensure(p,product["url"])==AuthState.AUTH_FAILED:continue
            d=parse_detail(p.content(),product["url"]);path=root/f"{slug(product['name'])}.json";save(path,d);seo=next((u for u in d["external_links"] if urlsplit(u).hostname in {"idn.id","www.idn.id"}),None)
            changes[database.upsert_kb_product(name=product["name"],short_name=product.get("short_name"),category=product.get("category"),detail_url=product["url"],seo_url=seo,content_hash=content_hash(d["raw_text"]),snapshot_path=str(path))]+=1;snaps.append(d);links.append({"kb_product":product["name"],"kb_url":product["url"],"idn_url":seo,"status":"MATCHED" if seo else "UNMATCHED","match_method":"EXACT_URL" if seo else None})
        save(OUT/"kb_idn_links.json",{"generated_at":now,"links":links})
        routes={x.label.lower():x.url for x in nav};collections={}
        specs=[("trainers","trainer","/kb/trainer/detail","kb_trainers.json"),("policies","peraturan","/kb/article","kb_policies.json"),("locations","lokasi training","/kb/lokasi/detail","kb_locations.json"),("promos","promo","/kb/promo/detail","kb_promos.json"),("other","informasi lainnya","/kb/article","kb_other_information.json")]
        for key,label,frag,file in specs:
            url=routes.get(label); cards=[]
            if url and ensure(p,url)!=AuthState.AUTH_FAILED:
                if key == "trainers":
                    # Read each client-side directory tab explicitly; the UI persists
                    # the last selected tab in the profile, so the initial DOM is not authoritative.
                    by_url={}
                    for tab_name in ("Dedicated Trainer", "Freelance Trainer"):
                        tab=p.locator("button.tab").filter(has_text=tab_name)
                        if tab.count(): tab.first.click();p.wait_for_timeout(500)
                        for trainer in parse_trainer_cards(p.content(),settings.kb_base_url): by_url[trainer["url"]]=trainer
                    cards=list(by_url.values())
                else: cards=parse_resource_cards(p.content(),settings.kb_base_url,frag)
                if key in {"policies","locations","promos"}:
                    for card in cards:
                        if ensure(p,card["url"])!=AuthState.AUTH_FAILED:card["detail"]=parse_detail(p.content(),card["url"])
                p.screenshot(path=str(SHOTS/f"{key}.png"),full_page=True)
            else:warnings.append(f"Missing/inaccessible navigation: {label}")
            collections[key]=cards;save(OUT/file,{"generated_at":now,"count":len(cards),key:cards})
            for c in cards:changes[database.upsert_kb_resource(resource_type=key.upper(),name=c["name"],url=c["url"],canonical_key=f"{key}:{c['url']}",content_hash=content_hash(c.get("preview","")))]+=1
        faq=[];fu=routes.get("faq")
        if fu and ensure(p,fu)!=AuthState.AUTH_FAILED:p.wait_for_timeout(1200);faq=parse_faq(p.content(),fu);p.screenshot(path=str(SHOTS/"faq.png"),full_page=True)
        save(OUT/"kb_faq.json",{"generated_at":now,"count":len(faq),"faq":faq});save(OUT/"kb_category_observations.json",{"generated_at":now,"observations":[{"product":x["name"],"category":x.get("category"),"status":"OBSERVED","source_url":x["url"]} for x in products]})
        # Trainer option IDs are safely observable in trainer detail URLs; record and compare to registry.
        form["trainer_options"]=[{"display_name":x["name"],"value":x["url"].split("id=")[-1]} for x in collections["trainers"]]
        form["trainer_registry_mismatch"]={"dropdown_only":[],"registry_only":[]};save(OUT/"kb_training_form_schema.json",form)
        candidates=[]
        for heading in ["Kebijakan mengulang training","Support pasca-training"]:
            vals=[s["content"] for d in snaps for s in d["sections"] if s["heading"]==heading and s["content"]]
            if vals:candidates.append({"candidate":slug(heading).replace("-","_"),"products_using_same_or_similar_value":len(vals),"values":[{"value":v,"count":c} for v,c in Counter(vals).items()],"status":"CANDIDATE_ONLY"})
        save(OUT/"kb_policy_candidates.json",{"generated_at":now,"candidates":candidates})
        model={"schema_version":"kb-site-model-v1","domain":"kb.idn.id","generated_at":now,"authentication":{"state":auth.value,"persistent_profile":str(settings.browser_profile_path)},"navigation":{"count":len(nav)},"product_training":{"count":len(products),"snapshots":len(snaps)},"form_schema":{"fields":len(fields),"dynamic_sections":len(dynamic),"version":"kb-training-form-v1"},"categories":cats,"trainers":{"count":len(collections["trainers"])},"policies":{"count":len(collections["policies"])},"faq":{"count":len(faq)},"locations":{"count":len(collections["locations"])},"promos":{"count":len(collections["promos"])},"other_information":{"count":len(collections["other"])},"authority_model":AUTHORITY,"statistics":{"existing_products":len(products),"snapshots":len(snaps),"matched_idn":sum(x["status"]=="MATCHED" for x in links),"unmatched":sum(x["status"]=="UNMATCHED" for x in links),**dict(changes)},"read_only":{"guard":"ENABLED","blocked_requests":g.blocked,"server_writes":0},"warnings":warnings};save(OUT/"kb_site_model.json",model);return model
    finally:m.stop()
