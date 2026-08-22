"""Command-line entry point for the Step 1 runtime foundation."""

import argparse
import logging
import sys
import json
from pathlib import Path

from app.browser.manager import BrowserManager
from app.core.config import get_settings
from app.core.database import Database
from app.core.health import print_health_report, run_health_checks
from app.core.logger import configure_logging
from app.core.runtime import RuntimeLock, ShutdownCoordinator, ensure_runtime_directories
from app.site_model.reconnaissance import run_reconnaissance
from app.extractor.pipeline import generate_summary, manual_validation_samples, run_extraction
from app.extractor.audit import print_parser_audit, run_parser_audit
from app.kb.reconnaissance import print_auth_test, run_kb_auth_test, run_kb_reconnaissance
from app.resolver.service import build_report as build_resolver_report, preflight as resolver_preflight, run_batch as resolver_batch, run_one as resolve_one
from app.quality.service import build_report as build_quality_report, run_batch as quality_batch, run_one as quality_one
from app.identity.service import LIVE as KB_LIVE_INDEX, audit_duplicates, load as identity_load, refresh_live_index, route_product
from app.publisher.service import PublishReadinessViolation, dry_run as publisher_dry_run, preflight as publisher_preflight_check, report as publisher_saved_report
from app.merge.service import merge_batch, merge_check, merge_report
from app.candidate.service import candidate_preflight, candidate_report, dry_run as candidate_dry_run, parity_model
from app.live_publish.service import LivePublishError, live_preflight, publish_live
from app.live_publish.reconcile import ReconciliationError, live_run_report, reconcile_live_run
from app.canary.service import CanaryError, discover as canary_discover, execute as canary_execute, preflight as canary_preflight


def command_health() -> int:
    settings = get_settings()
    results = run_health_checks(settings)
    print_health_report(results)
    return 0 if all(result.ok for result in results) else 1


def command_db_test() -> int:
    settings = get_settings()
    database = Database(settings.database_path)
    database.initialize_database()
    source_url = "internal://db-test"
    job = database.find_job("SELF_TEST", source_url)
    if job is None:
        job_id = database.create_job("SELF_TEST", "Database test job", source_url)
    else:
        job_id = int(job["id"])
    database.update_job_status(job_id, "COMPLETED", last_action="db-test passed")
    print(database.get_job(job_id))
    return 0


def command_browser_test() -> int:
    settings = get_settings()
    screenshot = Path("runtime/screenshots/idn-training-test.png")
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    manager = BrowserManager(settings.browser_profile_path, settings.headless)
    try:
        manager.start()
        page = manager.new_page()
        page.goto(settings.idn_training_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_load_state("networkidle", timeout=30_000)
        print(f"Page title: {page.title()}")
        print(f"Final URL: {page.url}")
        page.screenshot(path=str(screenshot), full_page=True)
        print(f"Screenshot: {screenshot}")
        return 0
    except Exception:
        logging.getLogger("browser-test").exception("Browser test failed")
        return 1
    finally:
        manager.stop()


def command_run() -> int:
    settings = get_settings()
    lock = RuntimeLock()
    shutdown = ShutdownCoordinator()
    shutdown.add(lock.release)
    shutdown.install()
    try:
        lock.acquire()
        Database(settings.database_path).initialize_database()
        results = run_health_checks(settings, include_browser=False)
        print_health_report(results)
        print("\nAutonomous pipeline is not enabled in Step 1.")
        return 0 if all(result.ok for result in results) else 1
    finally:
        shutdown.shutdown()


def command_idn_learn(limit: int) -> int:
    settings = get_settings()
    database = Database(settings.database_path)
    database.initialize_database()
    report = run_reconnaissance(settings, database, sample_limit=limit)
    print(f"IDN reconnaissance complete: {report.training_products_found} products, "
          f"{report.sample_landing_pages_analyzed} samples, {report.pages_failed} failures")
    return 0 if report.pages_failed == 0 else 2


def command_idn_report() -> int:
    model_path = Path("data/site_models/idn_site_model.json")
    catalog_path = Path("data/site_models/training_catalog.json")
    if not model_path.exists() or not catalog_path.exists():
        print("No saved IDN site model. Run: python main.py idn-learn", file=sys.stderr)
        return 1
    model = json.loads(model_path.read_text(encoding="utf-8"))
    stats = model.get("statistics", {})
    print("IDN SITE MODEL\n")
    print(f"Categories.............. {stats.get('categories', 0)}")
    print(f"Training Products....... {stats.get('products', 0)}")
    print(f"Unique URLs............. {stats.get('unique_urls', 0)}")
    print(f"Samples Analyzed........ {stats.get('samples_analyzed', 0)}")
    print(f"Supporting Pages........ {stats.get('supporting_pages', 0)}")
    print(f"Warnings................ {len(model.get('warnings', []))}")
    print(f"\nModel:\n{model_path}\n\nCatalog:\n{catalog_path}")
    return 0


def command_idn_extract(limit: int | None, force: bool) -> int:
    settings = get_settings(); database = Database(settings.database_path); database.initialize_database()
    summary = run_extraction(settings, database, limit=limit, force=force)
    print(f"IDN extraction complete: {summary['completed']} completed, {summary['partial']} partial, "
          f"{summary['failed']} failed, {summary['pending']} pending")
    return 2 if summary["failed"] else 0


def command_extraction_report() -> int:
    settings = get_settings(); database = Database(settings.database_path); database.initialize_database()
    summary = generate_summary(database)
    coverage = summary["field_coverage"]
    print("IDN TRAINING EXTRACTION REPORT\n")
    print(f"Products registered...... {summary['total_products']}")
    print(f"Extracted................ {summary['completed']}")
    print(f"Partial.................. {summary['partial']}")
    print(f"Failed................... {summary['failed']}")
    for label, key in (("Description", "description"), ("Curriculum", "curriculum"), ("Price", "price"),
                       ("Duration", "duration"), ("Prerequisite", "prerequisites"), ("Trainer", "trainers")):
        print(f"{label + ' found':27} {coverage.get(key, {}).get('FOUND', 0)}")
    print(f"\nAverage coverage......... {summary['average_coverage']:.1%}")
    print(f"Unknown headings......... {sum(summary['unknown_headings'].values())}")
    print(f"Quality warnings......... {sum(summary['quality_flags'].values())}")
    print("\nDataset:\ndata/products/\n\nSummary:\ndata/site_models/training_extraction_summary.json")
    samples = manual_validation_samples(database)
    if samples:
        print("\nMANUAL EXTRACTION VALIDATION")
        for item in samples:
            facts = item["facts"]
            def shown(key):
                field = facts[key]; return field.get("value") if field.get("value") is not None else field.get("values")
            print(f"\nProduct: {item['product']}\nCategory: {item['category']}\nURL: {item['url']}\n"
                  f"Description: {shown('description')}\nDuration: {shown('duration')}\nPrice: {shown('price')}\n"
                  f"Curriculum: {shown('curriculum')}\nTrainer: {shown('trainers')}\nStatus: REVIEW_REQUIRED")
    return 0


def command_parser_audit() -> int:
    audit = run_parser_audit()
    print_parser_audit(audit)
    return 0 if all(audit["regression"].values()) else 2


def command_kb_auth_test() -> int:
    result = run_kb_auth_test(get_settings())
    print_auth_test(result)
    return 0 if result["training_accessible"] else 2


def command_kb_learn(limit: int | None) -> int:
    settings=get_settings(); db=Database(settings.database_path); db.initialize_database()
    model=run_kb_reconnaissance(settings,db,limit)
    stats=model["statistics"]
    print(f"KB reconnaissance complete: {stats['existing_products']} products, {stats['snapshots']} snapshots, "
          f"{stats.get('new',0)} new, {stats.get('updated',0)} updated, {stats.get('unchanged',0)} unchanged")
    return 0


def command_kb_report() -> int:
    path=Path("data/kb_site_models/kb_site_model.json")
    if not path.exists(): print("No saved KB site model. Run: python main.py kb-learn",file=sys.stderr);return 1
    m=json.loads(path.read_text(encoding="utf-8"));s=m["statistics"]
    print("KB SITE MODEL\n");print(f"Authentication......... learned\nNavigation sections.... {m['navigation']['count']}\nExisting products...... {s['existing_products']}\nCategories............. {len(m['categories'])}\nTrainers............... {m['trainers']['count']}\nPolicies............... {m['policies']['count']}\nFAQ.................... {m['faq']['count']}\nLocations.............. {m['locations']['count']}\nPromos................. {m['promos']['count']}\n\nForm fields............ {m['form_schema']['fields']}\nDynamic sections....... {m['form_schema']['dynamic_sections']}\n\nMatched to IDN......... {s['matched_idn']}\nUnmatched.............. {s['unmatched']}\nWarnings............... {len(m['warnings'])}")
    return 0


def command_kb_form_report() -> int:
    path=Path("data/kb_site_models/kb_training_form_schema.json")
    if not path.exists(): print("No saved KB form schema.",file=sys.stderr);return 1
    schema=json.loads(path.read_text(encoding="utf-8"));print("KB TRAINING FORM\n")
    for f in schema["fields"]:
        print(f"{f['label']}\n  type: {f['control_type']}\n  required: {'YES' if f['required'] else 'NO'}")
        if f["options"]: print("  options: "+", ".join(x["label"] for x in f["options"]))
    print("\nDynamic sections:")
    for d in schema["dynamic_sections"]:print(f"- {d['section_anchor']}: {len(d['row_fields'])} row control(s)")
    return 0

def command_resolver_preflight()->int:
    s=get_settings();db=Database(s.database_path);r=resolver_preflight(s,db)
    o=r['ollama'];b=r['browser_research'];ready=r['pass'] and r['offline_available']
    print("RESOLVER PREFLIGHT\n");print(f"IDN dataset............ {'OK' if r['idn_products'] else 'MISSING'} ({r['idn_products']} products)\nKB dataset............. {'OK' if r['kb_model'] else 'MISSING'}\nInternal knowledge..... {'OK' if r['index_chunks'] else 'MISSING'} ({r['index_chunks']} chunks)\nCategory map........... {'OK' if r['categories'] else 'MISSING'}\nBrowser research....... {'OK' if b.get('available') else 'OPTIONAL_UNAVAILABLE'}\nOllama................. {'OK' if o.get('runtime') else 'OPTIONAL_UNAVAILABLE'}\nConfigured model....... {o.get('configured_model') or '<not configured>'}\nModel available........ {'YES' if o.get('model_installed') else 'NO'}\nDatabase............... OK\nOffline mode........... {'READY' if r['offline_available'] else 'BLOCKED'}\n\nRESULT: {'READY' if ready else 'FAIL'}")
    if r["missing"]:print("Missing: "+", ".join(r["missing"]));return 2
    return 0
def command_resolve_product(slug,research,local_ai)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    s=get_settings();db=Database(s.database_path);db.initialize_database();result,metrics=resolve_one(slug,s,db,research,local_ai=local_ai)
    print(f"RESOLVED PRODUCT\n\nProduct................. {result.payload.full_name}\nCategory................ {result.payload.category}\nCompletion.............. {result.completion:.2f}%\nInference provider...... {metrics.get('provider')}\nInference calls......... {metrics['calls']}\nInference cache hits.... {metrics['cache_hits']}\nResearch fetches........ {metrics['research_fetches']}\nRule fallback........... {'YES' if metrics['fallback'] else 'NO'}\nConflicts............... {len(result.source_conflicts)}\nStatus.................. {result.product_status.value}\nKB writes............... 0")
    return 0 if result.product_status.value=="RESOLVED" else 1
def command_resolve_batch(limit,research,local_ai)->int:
    s=get_settings();db=Database(s.database_path);db.initialize_database();results=resolver_batch(limit,s,db,research,local_ai)
    for r in results:print(f"{r.slug:40} {r.completion:6.2f}% {r.product_status.value}")
    print(f"\nResolved {len(results)} product(s); KB writes: 0");return 0
def command_resolve_report()->int:
    s=get_settings();db=Database(s.database_path);db.initialize_database();r=build_resolver_report(db);m=r["methods"]
    print("RESOLVER REPORT\n");print(f"Products available......... {r['products_available']}\nProducts resolved.......... {r['products_resolved']}\nReview required............ {r['review_required']}\nFailed..................... {r['failed']}\n\nAverage completion.......... {r['average_completion']:.2f}%\n\nDirect IDN facts............ {m.get('DIRECT_FACT',0)}\nInternal KB resolutions..... {m.get('INTERNAL_KB',0)}\nBrowser research............ {m.get('OFFICIAL_RESEARCH',0)}\nOllama inference............ {m.get('LOCAL_INFERENCE',0)}\nRule/derived fields......... {m.get('DERIVED',0)}\nSafe defaults............... {m.get('SAFE_DEFAULT',0)}\nSource conflicts............ {r['source_conflicts']}\n\nOllama products............. {r['inference']['ollama']}\nInference cache hits........ {r['inference']['cache_hits']}\nResearch fetches............ {r['research']['fetches']}\nResearch cache hits......... {r['research']['cache_hits']}\n\nNo KB writes................ YES");return 0
def command_ollama_check()->int:
    from app.resolver.inference import ollama_status
    s=get_settings();r=ollama_status(s);status="PASS" if r['runtime'] and r['model_installed'] else "CONFIG_REQUIRED" if r['runtime'] else "OPTIONAL_UNAVAILABLE"
    print("OLLAMA CHECK\n");print(f"Runtime................. {'AVAILABLE' if r['runtime'] else 'UNAVAILABLE'}\nEndpoint................ {r['endpoint']}\nConfigured model........ {r['configured_model'] or '<not configured>'}\nInstalled models........ {', '.join(r['installed_models']) or '-'}\nModel installed......... {'YES' if r['model_installed'] else 'NO'}\nContext.................. {r['context']}\nTemperature.............. {r['temperature']}\n\nRESULT: {status}");return 0 if status=="PASS" else 1
def command_research_check()->int:
    from app.research.browser import BrowserResearchProvider
    s=get_settings();r=BrowserResearchProvider(s).check();print("BROWSER RESEARCH CHECK\n");print(f"Provider................ {r['provider']}\nIDN official source..... {'AVAILABLE' if r['available'] else 'UNAVAILABLE'}\nHTTP status.............. {r.get('status_code','-')}\nPaid search API.......... NO\n\nRESULT: {'PASS' if r['available'] else 'OPTIONAL_UNAVAILABLE'}");return 0 if r['available'] else 1
def command_research_report()->int:
    s=get_settings();db=Database(s.database_path);db.initialize_database();m=db.research_metrics();print("RESEARCH REPORT\n");print(f"Completed................ {m.get('COMPLETED',0)}\nFailed................... {m.get('FAILED',0)}\nCached................... {m.get('CACHED',0)}\nNo KB writes............. YES");return 0

def _print_quality_result(report,trace)->None:
    before=trace["before"]["payload"];after=trace["after"]["payload"]
    print(f"{after['full_name'].upper()}\n\nBEFORE\nLearning Outcomes:\n- "+"\n- ".join(before["learning_outcomes"]) + "\nPractice:\n- "+"\n- ".join(before["practice_examples"])+f"\nRepeat:\n{before['repeat_policy']}\n\nAFTER\nLearning Outcomes:\n- "+"\n- ".join(after["learning_outcomes"])+"\nPractice:\n- "+"\n- ".join(after["practice_examples"])+f"\nRepeat:\n{after['repeat_policy']}\n\nQuality: {report.score} / {report.publish_readiness.value}\nIssues remaining: {len(report.errors)+len(report.warnings)}\nRepair calls: {trace['repair'].get('calls',0)}\nKB writes: 0")

def command_quality_check(slug,repair)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    report,trace=quality_one(slug,get_settings(),repair);_print_quality_result(report,trace)
    return 0 if report.publish_readiness.value!="BLOCKED" else 2

def command_quality_batch(limit,repair)->int:
    if limit is not None and limit<0:raise ValueError("--limit must be zero or greater")
    rows=quality_batch(limit,get_settings(),repair)
    for report,trace in rows:
        print(f"{report.slug:40} {report.score:3} {report.publish_readiness.value:15} issues={len(report.errors)+len(report.warnings):2} repair_calls={trace['repair'].get('calls',0)}")
    print(f"\nEvaluated {len(rows)} product(s); KB writes: 0");return 0

def command_quality_report()->int:
    report=build_quality_report();issues=report["issues"]
    print("PUBLISH QUALITY REPORT\n");print(f"Products evaluated.......... {report['products_evaluated']}\n\nREADY....................... {report['READY']}\nREVIEW_REQUIRED............. {report['REVIEW_REQUIRED']}\nBLOCKED..................... {report['BLOCKED']}\n\nAverage quality............. {report['average_quality']:.2f}\n\nIssues:\nCross-field duplicates...... {issues.get('CROSS_FIELD_DUPLICATION',0)}\nGeneric content............. {issues.get('GENERIC_LOW_INFORMATION',0)}\nUnsupported claims.......... {issues.get('UNSUPPORTED_MARKETING_CLAIM',0)}\nField relevance errors...... {issues.get('FIELD_RELEVANCE',0)}\nCommercial safety........... {issues.get('COMMERCIAL_FIELD_CONTAMINATION',0)}\nLanguage mismatches......... {issues.get('OUTPUT_LANGUAGE_MISMATCH',0)}\n\nOllama repair calls......... {report['ollama']['repair_calls']}\nOllama cache hits........... {report['ollama']['cache_hits']}\nOllama failures............. {report['ollama']['failures']}\nKB writes................... 0")
    return 0

def command_kb_product_index_refresh()->int:
    data=refresh_live_index(get_settings());print(f"KB PRODUCT INDEX REFRESH\n\nProducts............... {data['count']}\nInventory hash......... {data['inventory_hash']}\nBlocked requests....... {len(data['read_only']['blocked_requests'])}\nKB writes.............. 0\n\nRESULT: PASS");return 0

def command_duplicate_audit(refresh=False)->int:
    inventory=refresh_live_index(get_settings()) if refresh else identity_load(KB_LIVE_INDEX);result=audit_duplicates(inventory)
    print(f"KB DUPLICATE AUDIT\n\nLive KB products....... {inventory['count']}\nDuplicate groups....... {len(result['duplicate_groups'])}\nKB writes.............. 0")
    for group in result["duplicate_groups"]:print(f"\n{group['reason']}: "+", ".join(x["name"] for x in group["products"]))
    return 0

def command_dedup_check(slug)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    s=get_settings();db=Database(s.database_path);db.initialize_database();inventory=identity_load(KB_LIVE_INDEX);route,identity,diff=route_product(slug,s,db,inventory);existing=identity.existing_product or {}
    print(f"DEDUP CHECK\n\nProduct................ {identity.source_name}\nLive KB products....... {inventory['count']}\nExact URL............... {'YES' if identity.match_method.value=='EXACT_CANONICAL_URL' else 'NO'}\nExact normalized name.. {'YES' if identity.match_method.value=='EXACT_NORMALIZED_NAME' else 'NO'}\nExisting product....... {existing.get('name','-')}\nKB product ID........... {existing.get('kb_product_id','-')}\nDecision................ {route['identity_decision']}\nMatch................... {route['match_method']}\nConfidence.............. {route['confidence']:.2f}\nPublish readiness....... {route['publish_readiness']}\nPublisher allowed....... {'YES' if route['publisher_allowed'] else 'NO'}\nKB writes............... 0")
    return 0

def command_dedup_batch(limit)->int:
    s=get_settings();db=Database(s.database_path);db.initialize_database();inventory=identity_load(KB_LIVE_INDEX);paths=sorted(Path("data/publish_ready").glob("*/publish_payload.json"));rows=[]
    for path in paths[:limit if limit is not None else len(paths)]:rows.append(route_product(path.parent.name,s,db,inventory)[0])
    for r in rows:print(f"{r['slug']:40} {r['identity_decision']:16} {r['match_method']}")
    print(f"\nRouted {len(rows)} product(s); KB writes: 0");return 0

def command_publisher_preflight(slug)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    result=publisher_preflight_check(slug);route=result["route"]
    print(f"PUBLISHER PREFLIGHT\n\nProduct................. {slug}\nIdentity route.......... {route['identity_decision']}\nRoute fresh............. {'YES' if result['route_fresh'] else 'NO'}\nBlocking conflicts...... {', '.join(route.get('blocking_conflicts',[])) or '-'}\n\nDRY RUN: {'READY' if result['dry_run_allowed'] else 'BLOCKED'}\nLIVE PUBLISH: {'READY' if result['live_publish_allowed'] else 'BLOCKED'}\nReason.................. {result['reason']}\nKB writes............... 0")
    return 0 if result["dry_run_allowed"] else 2

def command_publish_dry_run(slug)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    try:r=publisher_dry_run(slug,get_settings())
    except PublishReadinessViolation as exc:print(str(exc));return 2
    print(f"PUBLISHER DRY RUN\n\nProduct................. {slug}\nRoute................... {r['mode']}\nTarget.................. {r['target_url']}\nFields evaluated........ {len(r['actions'])}\nConflicts detected...... {r['conflicts']['detected']}\nConflicts preserved..... {r['conflicts']['preserved']}\nConflicts overwritten... {r['conflicts']['overwritten']}\nReadback................ {r['validation']['readback']}\nSave clicked............ NO\nServer writes........... 0\n\nDRY RUN: PASS\nLIVE PUBLISH: {'READY' if r['live_publish_allowed'] else 'BLOCKED'}")
    return 0

def command_publisher_report(slug)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    r=publisher_saved_report(slug);print(f"PUBLISHER REPORT\n\nProduct................. {slug}\nSystem Status........... {r['system_status']}\nRoute................... {r['mode']}\n\nConflicts:\nDetected................ {r['conflicts']['detected']}\nPreserved............... {r['conflicts']['preserved']}\nOverwritten............. {r['conflicts']['overwritten']}\n\nDry run allowed......... {'YES' if r['dry_run_allowed'] else 'NO'}\nLive publish allowed.... {'YES' if r['live_publish_allowed'] else 'NO'}\nDry run................. {r['dry_run']}\nServer writes........... 0\nSave clicked............ NO")
    if r["blocking_conflicts"]:print("Reason.................. HIGH_RISK_CONFLICT: "+", ".join(r["blocking_conflicts"]))
    return 0

def _print_merge(r)->None:
    print(f"UPDATE MERGE REPORT\n\nProduct................ {r['slug']}\nIdentity............... {r['identity_decision']}\n\nFields:")
    for x in r["fields"]:print(f"{x['field'] + '':27} {x['decision']}")
    c=r["counts"];print(f"\nRegression prevented... {r['regressions_prevented']}\nNew fields added....... {c['FILL_EMPTY']}\nExisting preserved..... {c['KEEP_EXISTING']}\nReplaced............... {c['REPLACE_WITH_NEW']}\nAugmented.............. {c['AUGMENT_EXISTING']}\nUnchanged.............. {c['UNCHANGED']}\nReview required........ {c['REVIEW_REQUIRED']}\n\nMerge readiness........ {r['merge_readiness']}\nKB writes.............. 0")
def command_merge_check(slug,local_ai)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    r,_=merge_check(slug,local_ai);_print_merge(r);return 0 if r["merge_readiness"]=="READY" else 2
def command_merge_report(slug)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    _print_merge(merge_report(slug));return 0
def command_merge_batch(limit)->int:
    rows=merge_batch(limit)
    for r in rows:print(f"{r['slug']:40} {r['merge_readiness']:16} prevented={r['regressions_prevented']}")
    print(f"\nMerged {len(rows)} product(s); KB writes: 0");return 0
def command_publisher_parity_report()->int:
    from app.resolver.models import KBProductPayload
    p=parity_model();fields=list(KBProductPayload.model_fields);missing=[x for x in fields if x not in p];print(f"PUBLISHER FIELD PARITY\n\nTOTAL PAYLOAD FIELDS....... {len(fields)}\nIMPLEMENTED................ {sum(x in p for x in fields)}\nMISSING.................... {len(missing)}")
    if missing:print("Missing: "+", ".join(missing));return 2
    return 0
def command_candidate_preflight(slug)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    r=candidate_preflight(slug);print(f"LIVE CANDIDATE PREFLIGHT\n\nProduct................. {slug}\nQuality................. {r['route']['publish_readiness']}\nIdentity................ {r['route']['identity_decision']}\nPayload fields.......... {r['parity']['total']}\nMapped.................. {r['parity']['implemented']}\nMissing.................. {len(r['parity']['missing'])}\n\nRESULT: {'READY' if r['ready'] else 'BLOCKED'}")
    if r["reasons"]:print("Reasons: "+", ".join(r["reasons"]));return 2
    return 0
def command_candidate_dry_run(slug)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    r=candidate_dry_run(slug,get_settings());print(f"LIVE CANDIDATE DRY RUN\n\nProduct................. {slug}\nMode.................... {r['mode']}\nField parity............ {r['field_parity']['implemented']}/{r['field_parity']['total']}\nRound trip.............. {r['round_trip']}\nContent regression...... {'YES' if r['content_regression'] else 'NO'}\nUnexpected removal...... {'YES' if r['unexpected_removal'] else 'NO'}\nDeferred relations...... {r['deferred_relations_count']}\nCandidate hash.......... {r['candidate_hash']}\nLive candidate.......... {r['live_candidate_readiness']}\nServer writes........... 0\nSave clicked............ NO");return 0 if r["live_candidate_readiness"]=="READY" else 2
def command_candidate_report(slug)->int:
    if not slug:print("--slug is required",file=sys.stderr);return 2
    r=candidate_report(slug);c=r["changes"];d=r["dynamic"];print(f"LIVE CANDIDATE REPORT\n\nProduct................ {slug}\nMode................... {r['mode']}\n\nQuality................ {r['quality']}\nIdentity............... {r['identity']}\nMerge.................. {r['merge']}\n\nField parity:\nPayload fields......... {r['field_parity']['total']}\nMapped................. {r['field_parity']['implemented']}\nUnsupported............ {len(r['field_parity']['missing'])}\n\nChanges:\nUNCHANGED.............. {c['UNCHANGED']}\nFILL_EMPTY............. {c['FILL_EMPTY']}\nREPLACE................ {c['REPLACE_WITH_NEW']}\nAUGMENT................ {c['AUGMENT']}\nPRESERVE............... {c['PRESERVE_EXISTING']}\nDEFERRED............... {c['DEFERRED_RELATION']}\n\nDynamic:")
    for k,v in d.items():print(f"{k:24} {v}")
    print(f"\nOriginal conflicts..... {r['original_conflicts']}\nResolved by preserve... {r['resolved_by_preserve']}\nEffective conflicts.... {r['effective_conflicts']}\n\nRound trip............. {r['round_trip']}\nContent regression..... {'YES' if r['content_regression'] else 'NO'}\nUnexpected removal..... {'YES' if r['unexpected_removal'] else 'NO'}\n\nCandidate hash......... {r['candidate_hash']}\nLive candidate......... {r['live_candidate_readiness']}\nServer writes.......... 0")
    return 0
def command_live_preflight(slug,candidate_hash)->int:
    if not slug or not candidate_hash:print("--slug and --candidate-hash are required",file=sys.stderr);return 2
    r=live_preflight(slug,candidate_hash,get_settings());print(f"LIVE PUBLISH PREFLIGHT\n\nProduct................ {r['product']}\nMode................... {r['mode']}\n\nCandidate:\nReadiness.............. READY\nHash supplied.......... {r['hash_supplied']}\nHash recomputed........ {r['hash_recomputed']}\nAge.................... OK ({r['candidate_age_seconds']}s)\n\nIdentity:\nKB ID.................. {r['kb_product_id']}\nLive identity.......... {r['identity']}\nDuplicate check........ {r['duplicate_check']}\n\nBaseline:\nStored hash............ {r['stored_baseline_hash']}\nCurrent hash........... {r['current_baseline_hash']}\nMatch.................. {'YES' if r['baseline_match'] else 'NO'}\n\nSchema................. {r['schema']}\nRound trip............. {r['round_trip']}\nRegression............. {r['regression']}\nEffective conflicts.... {r['effective_conflicts']}\nDeferred relations..... {r['deferred_relations']}\nSave request policy.... {r['save_request_policy']}\n\nRESULT................. {r['result']}\nSERVER WRITES.......... 0");return 0
def command_publish_live(slug,candidate_hash,confirm_write)->int:
    if not slug or not candidate_hash or not confirm_write:print("publish-live requires --slug, --candidate-hash, and --confirm-write",file=sys.stderr);return 2
    r=publish_live(slug,candidate_hash,confirm_write,get_settings());print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r["result"]=="PASS" else 2
def command_reconcile_live_run(slug,run_id)->int:
    if not slug or not run_id:print("--slug and --run-id are required",file=sys.stderr);return 2
    r=reconcile_live_run(slug,run_id,get_settings());v=r["verification"];print(f"LIVE RUN RECONCILIATION\n\nRun.................... {run_id}\nOriginal result........ {r['original_result']}\nMatched................ {v['matched']}\nMismatched............. {v['mismatched']}\nDeferred............... {v['deferred']}\nNormalization fixes.... {len(r['normalization_corrections'])}\nAdditional writes...... 0\n\nEFFECTIVE RESULT....... {r['effective_publish_status']}");return 0 if r["result"]=="VERIFIED" else 2
def command_live_run_report(slug,run_id)->int:
    if not slug or not run_id:print("--slug and --run-id are required",file=sys.stderr);return 2
    data=live_run_report(slug,run_id);o=data["original"];r=data["reconciliation"];v=r["verification"];print(f"FIRST LIVE WRITE\n\nProduct................ Basic Penetration Testing\nHTTP write............. {r['write']['method']} {r['write']['status']}\nServer writes.......... {o['server_write_count']}\nAdditional writes...... {r['additional_server_writes']}\n\nTarget ID.............. {r['target_id']}\nDuplicate check........ {r['duplicate_check']}\nCount.................. {r['count']['before']} -> {r['count']['after']}\n\nField verification:\nMatched................ {v['matched']}\nMismatch............... {v['mismatched']}\nDeferred............... {v['deferred']}\n\nOriginal result........ {r['original_result']}\nEffective result....... {r['effective_publish_status']}");return 0
def command_canary_discover()->int:
    r=canary_discover();print("CANARY DISCOVERY")
    for label,key in (("Eligible UPDATE","UPDATE_EXISTING"),("Eligible CREATE","CREATE_NEW"),("Review required","REVIEW_REQUIRED"),("Not ready","NOT_READY")):
        print(f"\n{label}:")
        for x in r["groups"][key]:print(f"- {x['slug']}"+(f" ({', '.join(x['reasons'])})" if x.get("reasons") else ""))
    print("\nRecommended canary set:")
    for i,x in enumerate(r["selected"],1):print(f"{i}. {x['slug']} [{x['mode']}]")
    if r.get("plan"):print(f"\nCanary plan hash....... {r['plan']['canary_plan_hash']}")
    print("Server writes.......... 0");return 0
def command_canary_preflight(plan_hash)->int:
    if not plan_hash:print("--plan-hash is required",file=sys.stderr);return 2
    r=canary_preflight(plan_hash,get_settings());print(f"CANARY PREFLIGHT\n\nProducts............... {len(r['products'])}")
    for i,x in enumerate(r["products"],1):print(f"\n{i}. {x['product']}\n   Mode................ {x['mode']}\n   Candidate........... {x['candidate']}\n   Identity............ {x['identity']}\n   Baseline............ {'PASS' if x['baseline'] else 'FAIL'}\n   Duplicate check..... {x['duplicate_check']}")
    print(f"\nRESULT................ {r['result']}\nSERVER WRITES.......... 0");return 0 if r["result"]=="READY" else 2
def command_canary_live(plan_hash,confirm_write)->int:
    if not plan_hash or not confirm_write:print("canary-live requires --plan-hash and --confirm-write",file=sys.stderr);return 2
    r=canary_execute(plan_hash,confirm_write,get_settings());print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r["result"]=="VERIFIED" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="IDN KB Agent runtime foundation")
    parser.add_argument("command", choices=("health", "db-test", "browser-test", "run", "idn-learn", "idn-report",
                                            "idn-extract", "extraction-report", "parser-audit", "kb-auth-test",
                                            "kb-learn", "kb-report", "kb-form-report", "resolver-preflight", "resolve-product", "resolve-batch", "resolve-report", "research-report", "ollama-check", "research-check", "quality-check", "quality-batch", "quality-report", "kb-product-index-refresh", "duplicate-audit", "dedup-check", "dedup-batch", "publisher-preflight", "publish-dry-run", "publisher-report", "merge-check", "merge-report", "merge-batch", "publisher-parity-report", "candidate-preflight", "candidate-dry-run", "candidate-report", "live-preflight", "publish-live", "reconcile-live-run", "live-run-report", "canary-discover", "canary-preflight", "canary-live"))
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximum landing pages sampled by idn-learn (catalog remains complete)")
    parser.add_argument("--force", action="store_true", help="Re-extract completed products")
    parser.add_argument("--slug")
    parser.add_argument("--candidate-hash")
    parser.add_argument("--confirm-write",action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--plan-hash")
    parser.add_argument("--repair",action="store_true",help="Allow at most one cached/local Ollama quality repair call per product")
    parser.add_argument("--refresh",action="store_true",help="Refresh live read-only KB inventory before duplicate audit")
    mode=parser.add_mutually_exclusive_group();mode.add_argument("--offline",action="store_true");mode.add_argument("--local-ai",action="store_true");mode.add_argument("--research",action="store_true")
    args = parser.parse_args()
    ensure_runtime_directories()
    configure_logging(get_settings().log_level)
    try:
        commands = {"health": command_health, "db-test": command_db_test,
                    "browser-test": command_browser_test, "run": command_run,
                    "idn-report": command_idn_report, "extraction-report": command_extraction_report,
                    "parser-audit": command_parser_audit, "kb-auth-test": command_kb_auth_test,
                    "kb-report": command_kb_report, "kb-form-report": command_kb_form_report,
                    "resolver-preflight":command_resolver_preflight,"resolve-report":command_resolve_report,"research-report":command_research_report,"ollama-check":command_ollama_check,"research-check":command_research_check}
        if args.command=="resolve-product":return command_resolve_product(args.slug,args.research,args.local_ai)
        if args.command=="resolve-batch":return command_resolve_batch(args.limit,args.research,args.local_ai)
        if args.command=="quality-check":return command_quality_check(args.slug,args.repair)
        if args.command=="quality-batch":return command_quality_batch(args.limit,args.repair)
        if args.command=="quality-report":return command_quality_report()
        if args.command=="kb-product-index-refresh":return command_kb_product_index_refresh()
        if args.command=="duplicate-audit":return command_duplicate_audit(args.refresh)
        if args.command=="dedup-check":return command_dedup_check(args.slug)
        if args.command=="dedup-batch":return command_dedup_batch(args.limit)
        if args.command=="publisher-preflight":return command_publisher_preflight(args.slug)
        if args.command=="publish-dry-run":return command_publish_dry_run(args.slug)
        if args.command=="publisher-report":return command_publisher_report(args.slug)
        if args.command=="merge-check":return command_merge_check(args.slug,args.local_ai)
        if args.command=="merge-report":return command_merge_report(args.slug)
        if args.command=="merge-batch":return command_merge_batch(args.limit)
        if args.command=="publisher-parity-report":return command_publisher_parity_report()
        if args.command=="candidate-preflight":return command_candidate_preflight(args.slug)
        if args.command=="candidate-dry-run":return command_candidate_dry_run(args.slug)
        if args.command=="candidate-report":return command_candidate_report(args.slug)
        if args.command=="live-preflight":return command_live_preflight(args.slug,args.candidate_hash)
        if args.command=="publish-live":return command_publish_live(args.slug,args.candidate_hash,args.confirm_write)
        if args.command=="reconcile-live-run":return command_reconcile_live_run(args.slug,args.run_id)
        if args.command=="live-run-report":return command_live_run_report(args.slug,args.run_id)
        if args.command=="canary-discover":return command_canary_discover()
        if args.command=="canary-preflight":return command_canary_preflight(args.plan_hash)
        if args.command=="canary-live":return command_canary_live(args.plan_hash,args.confirm_write)
        if args.command == "idn-learn":
            if args.limit is not None and args.limit < 0:
                parser.error("--limit must be zero or greater")
            return command_idn_learn(10 if args.limit is None else args.limit)
        if args.command == "idn-extract":
            if args.limit is not None and args.limit < 0: parser.error("--limit must be zero or greater")
            return command_idn_extract(args.limit, args.force)
        if args.command == "kb-learn":
            if args.limit is not None and args.limit < 0: parser.error("--limit must be zero or greater")
            return command_kb_learn(args.limit)
        return commands[args.command]()
    except (LivePublishError,ReconciliationError,CanaryError) as exc:
        print(str(exc),file=sys.stderr);return 2
    except KeyboardInterrupt:
        logging.getLogger("main").info("Interrupted by user")
        return 130
    except Exception:
        logging.getLogger("main").exception("Command failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
