import hashlib,json,re
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit

from app.browser.manager import BrowserManager
from app.identity.service import LIVE,canonical_url,load,normalize_name,publisher_preflight,route_is_fresh
from app.kb.auth import classify_auth_page,wait_for_manual_auth
from app.kb.guard import ReadOnlyGuard,ReadOnlyViolation
from app.kb.models import AuthState
from app.merge.service import norm
from app.publisher.service import STATIC,PublisherPreflightViolation,publisher_payload_path

MODELS=Path("data/publisher_models");DRY=Path("data/publisher_dry_runs");OUT=Path("data/live_candidates");SHOTS=Path("runtime/screenshots/candidates")
PARITY={
 "full_name":("Nama lengkap produk","input"),"short_name":("Nama singkat","input"),"category":("Kategori","select"),"seo_url":("Link landing page SEO","input"),
 "advertising_links":("Link landing page iklan","dynamic_rows"),"training_formats":("Format training, durasi & harga referensi","dynamic_rows"),"target_audiences":("Target peserta","dynamic_rows"),
 "certifications":("Sertifikasi yang relate","dynamic_rows"),"tools":("Tools / perangkat","dynamic_rows"),"next_classes":("Kelas lanjutan","dynamic_rows"),"trainer_references":("Trainer referensi","multiselect"),
 "short_description":("Deskripsi singkat","textarea"),"learning_outcomes":("Selesai training, peserta bisa apa saja","textarea"),"prerequisites":("Prasyarat","textarea"),
 "repeat_policy":("Kebijakan mengulang training","textarea"),"practice_examples":("Contoh praktek","textarea"),"post_training_support":("Support pasca-training","textarea"),
 "selling_points":("Poin jualan utama","textarea"),"claims_to_avoid":("Klaim yang dihindari","textarea"),"additional_notes":("Catatan tambahan","textarea"),"active":("Aktif — tampil di daftar dan dipakai AI untuk menjawab","checkbox"),
}
SECTIONS={
 "advertising_links":("Link landing page iklan",["url","label"]),
 "training_formats":("Format training, durasi & harga referensi",["format","duration","schedule","public_price_reference","private_price_reference"]),
 "target_audiences":("Target peserta",["audience","problem_solved"]),
 "certifications":("Sertifikasi yang relate",["name","level","exam_price","exam_duration","question_count","passing_score","open_book","retake_policy"]),
 "tools":("Tools / perangkat",["name","provided_by"]),
 "next_classes":("Kelas lanjutan",["training_name","reason"]),
}
EXPECTED_LABELS={
 "advertising_links":["URL","Keterangan"],
 "training_formats":["Format","Durasi","Jam training","Harga public referensi (Rp)","Harga private/in-house referensi (Rp)"],
 "target_audiences":["Target peserta","Masalah yang diselesaikan"],
 "certifications":["Nama sertifikat","Level","Biaya ujian referensi (Rp)","Durasi exam","Jumlah soal","Skor lulus minimal","Open book","Kebijakan retake exam"],
 "tools":["Nama tools / perangkat","Disiapkan oleh"],
 "next_classes":["Produk lanjutan","Alasan"],
}
def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def parity_model():
    data={k:{"destination":v[0],"type":v[1],"implemented":True} for k,v in PARITY.items()};save(MODELS/"form_parity.json",data);return data
def parity_check(payload):
    parity=parity_model();missing=[k for k,v in payload.items() if k not in parity and v not in (None,"",[])];return {"total":len(payload),"implemented":sum(k in parity for k in payload),"missing":missing}
def _ensure(page,url):
    page.goto(url,wait_until="domcontentloaded",timeout=60_000);page.wait_for_timeout(700)
    if classify_auth_page(page.content(),page.url)!=AuthState.AUTHENTICATED and wait_for_manual_auth(page)!=AuthState.AUTH_RESTORED:raise PublisherPreflightViolation("AUTH_FAILED")
    page.get_by_text(re.compile(r"(?:Ubah|Tambah Produk|Produk Training Baru).*Training|Produk Training Baru"),exact=False).wait_for(timeout=20_000)
def _section(page,title):return page.get_by_text(title,exact=True).first.locator("xpath=../../..")
def _control_value(control):
    if control.evaluate("e=>e.tagName")=="SELECT":return control.locator("option:checked").inner_text().strip()
    return control.input_value()
PLACEHOLDER_VALUES={"— Pilih produk —","— Belum diisi —","— Pilih —"}
def normalize_dynamic_state(field,rows):
    normalized=[]
    for original in rows:
        row={k:("" if isinstance(v,str) and v.strip() in PLACEHOLDER_VALUES else v) for k,v in original.items()}
        if field=="advertising_links":meaningful=bool(row.get("url") or row.get("label"))
        elif field=="training_formats":meaningful=bool(row.get("duration") or row.get("schedule") or row.get("public_price_reference") not in (None,"","0","Rp 0") or row.get("private_price_reference") not in (None,"","0","Rp 0") or row.get("format") not in (None,"","Offline"))
        elif field=="target_audiences":meaningful=bool(row.get("audience") or row.get("problem_solved"))
        elif field=="certifications":meaningful=any(v not in (None,"") for v in row.values())
        elif field=="tools":meaningful=bool(row.get("name"))
        elif field=="next_classes":meaningful=bool(row.get("training_name") or row.get("reason"))
        else:meaningful=any(v not in (None,"") for v in row.values())
        if meaningful:normalized.append(row)
    return normalized
def _rows(page,key,normalize=True):
    title,keys=SECTIONS[key];section=_section(page,title);output=[]
    for row in section.locator(".entry-row").all():
        controls=row.locator("input,textarea,select").all();values={}
        for semantic,control in zip(keys,controls):values[semantic]=_control_value(control)
        output.append(values)
    return normalize_dynamic_state(key,output) if normalize else output
def discover_schema(page):
    sections={}
    for key,(title,keys) in SECTIONS.items():
        section=_section(page,title);labels=[];options={}
        row=section.locator(".entry-row").first
        probe_added=False
        if not row.count():
            row=_add_row(page,key);probe_added=True
        if row.count():
            labels=row.locator("label").all_inner_texts()
            for i,control in enumerate(row.locator("select").all()):options[str(i)]=control.locator("option").all_inner_texts()
        expected=EXPECTED_LABELS[key];ambiguous=bool(labels and labels!=expected)
        sections[key]={"section":title,"row_selector":".entry-row","semantic_keys":keys,"labels":labels,"select_options":options,"schema_ambiguous":ambiguous,"existing_rows":section.locator('.entry-row').count()-(1 if probe_added else 0),"add_button":"Tambah","client_side_probe_added":probe_added}
    data={"schema_version":"publisher-live-form-v1","generated_at":datetime.now(timezone.utc).isoformat(),"sections":sections};save(MODELS/"live_form_schema.json",data);return data
def parse_full(page,schema,trainer_options,normalize_dynamic=True):
    state={}
    for key,(label,_) in PARITY.items():
        if key in SECTIONS:state[key]=_rows(page,key,normalize_dynamic)
        elif key=="trainer_references":
            trigger=page.get_by_label("Trainer referensi",exact=False).first;text=trigger.inner_text().strip();names=[] if "Pilih trainer" in text else [x.strip() for x in text.split(",") if x.strip()];by_name={x["display_name"]:x["value"] for x in trainer_options};state[key]=[{"trainer_name":x,"kb_trainer_id":by_name.get(x)} for x in names]
        elif key=="active":state[key]=page.get_by_label(label,exact=False).first.is_checked()
        else:
            control=page.get_by_label(label,exact=False).first;value=_control_value(control)
            state[key]=[x for x in value.splitlines() if x.strip()] if key in {"learning_outcomes","prerequisites","practice_examples","selling_points","claims_to_avoid"} else value
    return state
def _add_row(page,key):
    section=_section(page,SECTIONS[key][0]);before=section.locator(".entry-row").count();section.get_by_role("button",name="Tambah",exact=True).click();page.wait_for_timeout(100);after=section.locator(".entry-row").count()
    if after!=before+1:raise PublisherPreflightViolation(f"DYNAMIC_ADD_FAILED:{key}")
    return section.locator(".entry-row").nth(after-1)
def _fill_row(row,values):
    controls=row.locator("input,textarea,select").all()
    for control,value in zip(controls,values):
        if value in (None,""):continue
        if control.evaluate("e=>e.tagName")=="SELECT":control.select_option(label=str(value))
        else:control.fill(str(value))
def _canonical_rows(rows,key):
    if key=="advertising_links":
        seen=set();out=[]
        for x in rows:
            cu=canonical_url(x.get("url"))
            if cu and cu not in seen:seen.add(cu);out.append(x)
        return out
    return rows
def _row_identity(field,row):
    if field=="advertising_links":return canonical_url(row.get("url"))
    if field=="training_formats":return norm(row.get("format"))+"|"+norm(row.get("duration"))
    if field=="target_audiences":return normalize_name(row.get("audience"))
    if field=="certifications":return normalize_name(row.get("name"))
    if field=="tools":return normalize_name(row.get("name"))
    if field=="next_classes":return normalize_name(row.get("training_name"))
    if field=="trainer_references":return row.get("kb_trainer_id") or normalize_name(row.get("trainer_name"))
    return json.dumps(row,sort_keys=True,ensure_ascii=False)
def _has_removal(field,before,after):
    if not isinstance(before,list) or not isinstance(after,list):return False
    after_ids={_row_identity(field,x) if isinstance(x,dict) else norm(x) for x in after}
    return any((_row_identity(field,x) if isinstance(x,dict) else norm(x)) not in after_ids for x in before)
def candidate_hash(slug,route,candidate,diff,baseline_hash,inventory_hash):
    body={"slug":slug,"destination":route.get("kb_product_id") or route.get("target_url"),"candidate":candidate,"effective_diff":diff,"baseline_hash":baseline_hash,"inventory_hash":inventory_hash}
    return hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
def candidate_preflight(slug):
    inventory=load(LIVE);route=load(Path("data/publish_routes")/slug/"route.json");payload=load(publisher_payload_path(slug,route["identity_decision"]));parity=parity_check(payload)
    reasons=[]
    if not route_is_fresh(route,inventory,route.get("route_ttl_seconds",3600)):reasons.append("ROUTE_STALE")
    if route["publish_readiness"]!="READY":reasons.append("QUALITY_NOT_READY")
    if route["identity_decision"]=="UPDATE_EXISTING":
        merge=load(Path("data/merge_ready")/slug/"merge_report.json")
        if merge["merge_readiness"]!="READY":reasons.append("MERGE_NOT_READY")
    if parity["missing"]:reasons.append("PUBLISHER_FIELD_UNMAPPED")
    return {"slug":slug,"ready":not reasons,"reasons":reasons,"route":route,"parity":parity,"payload":payload,"inventory":inventory}
def dry_run(slug,settings):
    pre=candidate_preflight(slug)
    if not pre["ready"]:raise PublisherPreflightViolation(",".join(pre["reasons"]))
    route,payload,inventory=pre["route"],pre["payload"],pre["inventory"];form_ref=load("data/kb_site_models/kb_training_form_schema.json");manager=BrowserManager(settings.browser_profile_path,False);guard=ReadOnlyGuard();actions=[];deferred=[];screens=[]
    try:
        manager.start();guard.install(manager.context);page=manager.new_page();_ensure(page,route["target_url"]);schema=discover_schema(page)
        if any(x["client_side_probe_added"] for x in schema["sections"].values()):_ensure(page,route["target_url"])
        if payload["certifications"] and (schema["sections"]["certifications"]["schema_ambiguous"] or any(x.get("exam_code") for x in payload["certifications"])):raise PublisherPreflightViolation("CERTIFICATION_SCHEMA_AMBIGUOUS")
        baseline=parse_full(page,schema,form_ref.get("trainer_options",[]));save(DRY/slug/"dynamic_baseline.json",{k:baseline[k] for k in [*SECTIONS,"trainer_references","active"]});save(DRY/slug/"baseline_full.json",baseline)
        SHOTS.mkdir(parents=True,exist_ok=True)
        def shot(name):path=SHOTS/slug/name;page.screenshot(path=str(path),full_page=True);screens.append(str(path))
        shot("01-baseline.png")
        # Static mapping follows merge-ready values; high-risk policy remains preserved.
        merge=load(Path("data/merge_ready")/slug/"merge_report.json") if route["identity_decision"]=="UPDATE_EXISTING" else {"fields":[]};decisions={x["field"]:x["decision"] for x in merge["fields"]}
        for field,(label,_) in PARITY.items():
            if field in SECTIONS or field in {"trainer_references","active"}:continue
            proposed=payload[field];control=page.get_by_label(label,exact=False).first;before=baseline[field];decision=decisions.get(field,"FILL_EMPTY" if before in (None,"",[]) else "REPLACE_WITH_NEW")
            if decision in {"KEEP_EXISTING","UNCHANGED"} or proposed in (None,"",[]):actions.append({"field":field,"action":"PRESERVE_EXISTING" if decision=="KEEP_EXISTING" else "UNCHANGED"});continue
            value="\n".join(proposed) if isinstance(proposed,list) else proposed
            if field=="category":control.select_option(label=str(value))
            else:control.fill(str(value))
            actions.append({"field":field,"action":"FILL_EMPTY" if before in (None,"",[]) else "REPLACE_WITH_NEW"})
        shot("02-static.png")
        # Advertising links: canonical dedup, append only.
        existing_urls={canonical_url(x.get("url")) for x in baseline["advertising_links"]}
        for item in _canonical_rows(payload["advertising_links"],"advertising_links"):
            if canonical_url(item["url"]) in existing_urls:continue
            row=_add_row(page,"advertising_links");_fill_row(row,[item["url"],item.get("label")]);actions.append({"field":"advertising_links","action":"AUGMENT"})
        # Formats: preserve rows, match on normalized identity, fill missing fields only.
        valid_formats=schema["sections"]["training_formats"]["select_options"].get("0",[])
        for item in payload["training_formats"]:
            if item["format"]=="UNKNOWN" or item["format"] not in valid_formats:raise PublisherPreflightViolation("UNSUPPORTED_FORMAT")
            match=next((x for x in _section(page,SECTIONS["training_formats"][0]).locator('.entry-row').all() if norm(_control_value(x.locator('select').first))==norm(item["format"]) and norm(_control_value(x.locator('input').nth(0)))==norm(item.get("duration"))),None)
            row=match or _add_row(page,"training_formats");controls=row.locator("input,select").all();values=[item["format"],item.get("duration"),item.get("schedule"),item.get("public_price_reference"),item.get("private_price_reference")]
            for c,v in zip(controls,values):
                if v is None:continue
                current=_control_value(c)
                if not current or current in {"0","Rp 0"}:
                    if c.evaluate('e=>e.tagName')=='SELECT':c.select_option(label=str(v))
                    else:c.fill(str(v))
            actions.append({"field":"training_formats","action":"FILL_EMPTY" if match else "AUGMENT"})
        shot("03-training-formats.png")
        # Existing target rows are curated; append only genuinely absent audiences.
        target_names={normalize_name(x["audience"]) for x in baseline["target_audiences"]}
        for item in payload["target_audiences"]:
            if normalize_name(item["audience"]) in target_names:continue
            # UPDATE with rich target baseline preserves it; candidate avoids speculative additions.
            if route["identity_decision"]=="UPDATE_EXISTING" and baseline["target_audiences"]:actions.append({"field":"target_audiences","action":"PRESERVE_EXISTING"});break
            row=_add_row(page,"target_audiences");_fill_row(row,[item["audience"],item.get("problem_solved")]);actions.append({"field":"target_audiences","action":"AUGMENT"})
        shot("04-target-audiences.png")
        # Certifications are high risk: preserve existing; create only when unambiguous and empty.
        if payload["certifications"] and not baseline["certifications"]:
            for item in payload["certifications"]:
                row=_add_row(page,"certifications");_fill_row(row,[item.get("name"),item.get("level"),item.get("exam_fee_reference"),item.get("exam_duration"),None,item.get("passing_score"),None,item.get("notes")]);actions.append({"field":"certifications","action":"FILL_EMPTY"})
        else:actions.append({"field":"certifications","action":"PRESERVE_EXISTING" if baseline["certifications"] else "SKIPPED_EMPTY"})
        shot("05-certifications.png")
        # Tools: UNKNOWN provider never guesses.
        tool_names={normalize_name(x["name"]) for x in baseline["tools"]}
        for item in payload["tools"]:
            if normalize_name(item["name"]) in tool_names:continue
            if item.get("provided_by")=="UNKNOWN":actions.append({"field":"tools","action":"REVIEW_REQUIRED","reason":"UNKNOWN_PROVIDER"});continue
            row=_add_row(page,"tools");provider={"IDN":"Disiapkan IDN","PESERTA":"Disiapkan peserta"}.get(item.get("provided_by"));_fill_row(row,[item["name"],provider]);actions.append({"field":"tools","action":"AUGMENT"})
        shot("06-tools.png")
        # Next class exact-only; unavailable targets are deferred pass-2 relations.
        live_by_url={x.get("canonical_seo_url"):x for x in inventory["products"]};live_by_name={normalize_name(x["name"]):x for x in inventory["products"]};next_names={normalize_name(x["training_name"]) for x in baseline["next_classes"]};next_options=schema["sections"]["next_classes"]["select_options"].get("0",[])
        for item in payload["next_classes"]:
            target=live_by_url.get(canonical_url(item["canonical_source_url"])) or live_by_name.get(normalize_name(item["training_name"]))
            if not target:deferred.append({"source_slug":slug,"field":"next_classes","target_name":item["training_name"],"target_url":item["canonical_source_url"],"reason":"TARGET_NOT_YET_IN_KB"});actions.append({"field":"next_classes","action":"DEFERRED_RELATION"});continue
            if normalize_name(target["name"]) in next_names:actions.append({"field":"next_classes","action":"PRESERVE_EXISTING","target_kb_product_id":target["kb_product_id"]});continue
            option=next((x for x in next_options if normalize_name(x)==normalize_name(target["name"])),None)
            if not option:deferred.append({"source_slug":slug,"field":"next_classes","target_name":item["training_name"],"target_url":item["canonical_source_url"],"target_kb_product_id":target["kb_product_id"],"reason":"TARGET_NOT_AVAILABLE_IN_FORM_CONTROL"});actions.append({"field":"next_classes","action":"DEFERRED_RELATION"});continue
            row=_add_row(page,"next_classes");_fill_row(row,[option,item["reason"]]);actions.append({"field":"next_classes","action":"FILL_EMPTY" if not baseline["next_classes"] else "AUGMENT","target_kb_product_id":target["kb_product_id"]});next_names.add(normalize_name(option))
        save(Path("data/deferred_relations.json"),{"generated_at":datetime.now(timezone.utc).isoformat(),"relations":deferred});shot("07-next-classes.png")
        # Trainer exact IDs, append only; never clear.
        selected={x["kb_trainer_id"] for x in baseline["trainer_references"]};trigger=page.get_by_label("Trainer referensi",exact=False).first;valid={x["value"]:x["display_name"] for x in form_ref.get("trainer_options",[])}
        for item in payload["trainer_references"]:
            tid=item["kb_trainer_id"]
            if tid in selected:continue
            if tid not in valid:raise PublisherPreflightViolation("INVALID_TRAINER")
            if trigger.get_attribute("aria-expanded")!="true":trigger.click()
            page.get_by_text(valid[tid],exact=True).click();page.wait_for_timeout(50);actions.append({"field":"trainer_references","action":"FILL_EMPTY" if not selected else "AUGMENT"});selected.add(tid)
        page.keyboard.press("Escape");shot("08-trainers.png")
        active=page.get_by_label(PARITY["active"][0],exact=False).first
        if active.is_checked()!=payload["active"]:actions.append({"field":"active","action":"REVIEW_REQUIRED","reason":"HIGH_RISK_ACTIVE_CHANGE"})
        else:actions.append({"field":"active","action":"UNCHANGED"})
        shot("09-active.png");candidate=parse_full(page,schema,form_ref.get("trainer_options",[]));save(DRY/slug/"candidate_full.json",candidate)
        diff=[]
        for field in PARITY:
            b,c=baseline[field],candidate[field];action="UNCHANGED" if b==c else "FILL_EMPTY" if b in (None,"",[]) else "AUGMENT" if isinstance(b,list) and not _has_removal(field,b,c) else "REPLACE_WITH_NEW"
            if decisions.get(field)=="KEEP_EXISTING" and b==c:action="PRESERVE_EXISTING"
            if field=="next_classes" and deferred:action="DEFERRED_RELATION"
            diff.append({"field":field,"action":action,"before":b,"after":c})
        save(DRY/slug/"effective_diff.json",{"fields":diff});shot("10-final-candidate.png")
        if guard.blocked:raise ReadOnlyViolation(str(guard.blocked))
        removals=[x["field"] for x in diff if _has_removal(x["field"],x["before"],x["after"])]
        regressions=[x["field"] for x in diff if decisions.get(x["field"]) in {"KEEP_EXISTING","UNCHANGED"} and x["before"]!=x["after"]]
        original=route.get("blocking_conflicts",[]);reconciled=[{"original_conflict":x,"resolution":"PRESERVED_EXISTING","effective_change":False} for x in original if decisions.get(x)=="KEEP_EXISTING" and baseline[x]==candidate[x]];effective=[x for x in original if x not in {r["original_conflict"] for r in reconciled}]
        parity=pre["parity"];unsupported=[x for x in actions if x["action"] in {"REVIEW_REQUIRED","UNSUPPORTED"}];round_trip=not regressions and not removals
        baseline_sha=hashlib.sha256(json.dumps(baseline,sort_keys=True,ensure_ascii=False).encode()).hexdigest();effective_doc={"fields":diff};chash=candidate_hash(slug,route,candidate,effective_doc,baseline_sha,inventory["inventory_hash"])
        readiness="READY" if round_trip and not effective and not unsupported and not parity["missing"] else "REVIEW_REQUIRED"
        candidate_route={"slug":slug,"identity_decision":route["identity_decision"],"kb_product_id":route.get("kb_product_id"),"target_url":route["target_url"],"historical_route":f"data/publish_routes/{slug}/route.json","effective_conflict_reconciliation":reconciled,"effective_conflicts":effective,"candidate_hash":chash,"live_candidate_readiness":readiness,"live_publish_implemented":False}
        report={"schema_version":"live-candidate-v1","slug":slug,"generated_at":datetime.now(timezone.utc).isoformat(),"mode":route["identity_decision"],"quality":route["publish_readiness"],"identity":route["identity_decision"],"merge":"READY" if route["identity_decision"]=="UPDATE_EXISTING" else "NOT_REQUIRED","field_parity":parity,"actions":actions,"changes":{x:sum(d["action"]==x for d in diff) for x in ("UNCHANGED","FILL_EMPTY","REPLACE_WITH_NEW","AUGMENT","PRESERVE_EXISTING","DEFERRED_RELATION","REVIEW_REQUIRED")},"dynamic":{k:"DEFERRED" if k=="next_classes" and deferred else "EMPTY" if not candidate[k] else "PASS" for k in SECTIONS}|{"trainer_references":"PASS","active":"PASS"},"original_conflicts":len(original),"resolved_by_preserve":len(reconciled),"effective_conflicts":len(effective),"round_trip":"PASS" if round_trip else "FAIL","content_regression":bool(regressions),"unexpected_removal":bool(removals),"deferred_relations_count":len(deferred),"candidate_hash":chash,"live_candidate_readiness":readiness,"server_writes":0,"save_clicked":False,"screenshots":screens}
        folder=OUT/slug;save(folder/"candidate_payload.json",candidate);save(folder/"candidate_route.json",candidate_route);save(folder/"candidate_diff.json",effective_doc);save(folder/"candidate_report.json",report);save(folder/"baseline_hash.json",{"baseline_hash":baseline_sha,"inventory_hash":inventory["inventory_hash"],"candidate_hash":chash});return report
    finally:manager.stop()
def candidate_report(slug):return load(OUT/slug/"candidate_report.json")
