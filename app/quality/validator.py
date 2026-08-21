import copy
import json
import re
from pathlib import Path
from typing import Any

from app.quality.contracts import SEMANTIC_CONTRACTS
from app.quality.models import PublishReadiness, QualityIssue, QualityReport
from app.resolver.models import KBProductPayload, ResolvedProduct

SEMANTIC_LIST_FIELDS = ("learning_outcomes", "practice_examples", "selling_points")
RISKY_CLAIMS = re.compile(r"\b(pasti|dijamin|guarantee|terbaik|nomor satu|paling|pasti lulus|langsung kerja|meningkatkan gaji|penghasilan tambahan|peluang kerja lebih baik|sangat dicari|marketability|bisa freelance)\b", re.I)
CAREER_CLAIMS = re.compile(r"\b(karier|peluang kerja|penghasilan|gaji|marketability|bug bounty|freelance|langsung kerja|sangat dicari)\b", re.I)
FACILITY_TERMS = re.compile(r"\b(lunch|makan siang|coffee\s*break|coffe\s*break|coffebreak|penginapan|sertifikat|kaos|t-?shirt|akses internet|internet gratis|goodie bag|modul|ruangan ber\s*ac)\b", re.I)
GENERIC_PATTERNS = (
    re.compile(r"menguasai materi yang tersedia di curriculum", re.I),
    re.compile(r"membangun pemahaman dan keterampilan sesuai kurikulum", re.I),
    re.compile(r"peserta yang ingin mempelajari\s+.+", re.I),
    re.compile(r"siapa yang (?:cocok|ingin)\s+.+", re.I),
)
PRACTICE_VERBS = ("melakukan", "membuat", "mengonfigurasi", "menghubungkan", "menguji", "menganalisis", "menggunakan", "menambahkan", "menyusun", "mengendalikan", "mengirim", "menerapkan")
OUTCOME_VERBS = ("memahami", "menjelaskan", "mengidentifikasi", "menggunakan", "mengonfigurasi", "menerapkan", "membuat", "mengintegrasikan", "melakukan", "menganalisis", "mengelola", "troubleshooting", "mampu")
STOPWORDS = {"yang","dan","atau","untuk","dengan","dari","pada","dalam","peserta","training","pelatihan","mampu","memahami","melakukan","membuat","menggunakan","dasar","cara","serta","akan","adalah","apa","itu"}


def normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def tokens(value: str) -> set[str]:
    return {x for x in normalize(value).split() if len(x) > 2 and x not in STOPWORDS}


def similarity(left: str, right: str) -> float:
    a, b = tokens(left), tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def safe_truncate(text: str, maximum: int = 350) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= maximum:
        return text
    candidate = text[: maximum + 1]
    boundary = max(candidate.rfind(". "), candidate.rfind("! "), candidate.rfind("? "))
    if boundary >= max(80, maximum // 2):
        return candidate[: boundary + 1].strip()
    return candidate[:maximum].rsplit(" ", 1)[0].rstrip(" ,;:-") + "."


def deduplicate(values: list[str], near_threshold: float = .88) -> tuple[list[str], list[str]]:
    kept, removed = [], []
    for value in values:
        clean = re.sub(r"\s+", " ", value).strip(" \t\r\n-•")
        if not clean:
            continue
        if any(normalize(clean) == normalize(old) or similarity(clean, old) >= near_threshold for old in kept):
            removed.append(clean)
        else:
            kept.append(clean)
    return kept, removed


def clean_repeat_policy(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    match = re.search(r"(?:gratis\s+)?(?:mengikuti\s+)?(?:mengulang|ulang)[^.]*?(?:\bdua\s+kali\b|\b2\s+kali\b|tanpa syarat|(?:ketentuan|kebijakan)[^.]*)(?:\.|$)", value, re.I)
    candidate = match.group(0).strip() if match else value
    facility = FACILITY_TERMS.search(candidate)
    if facility:
        candidate = candidate[:facility.start()].strip(" ,;:-")
    # Repeated page fragments often concatenate a second policy phrase.
    repeats = list(re.finditer(r"\bgratis\s+(?:mengikuti\s+)?(?:mengulang|ulang)\b", candidate, re.I))
    if len(repeats) > 1:
        candidate = candidate[:repeats[1].start()].strip()
    return candidate.rstrip(".") + "." if candidate else value


def _facts_text(facts: dict) -> str:
    selected = {k: facts.get(k) for k in ("description","curriculum","benefits","facilities","target_audiences","tools","practice","prerequisites","support_information","repeat_policy")}
    return json.dumps(selected, ensure_ascii=False)


def _curriculum_items(facts: dict) -> list[str]:
    node = facts.get("curriculum") or {}
    value = node.get("value") if isinstance(node, dict) else None
    values = value if isinstance(value, list) else []
    output = []
    def visit(node):
        if isinstance(node,str):output.append(node)
        elif isinstance(node,list):
            for item in node:visit(item)
        elif isinstance(node,dict):
            if node.get("objective"):visit(node["objective"])
            visit(node.get("items",[]))
    visit(values)
    return output


def _topic(item: str) -> str:
    text = re.sub(r"^[*•\-]+\s*", "", item).strip()
    if ":" in text:
        label, detail = text.split(":", 1)
        if 2 <= len(label.split()) <= 7 and len(detail.strip()) > 8:
            text = detail.strip()
    return text.rstrip(".?!")


def derive_semantics(payload: KBProductPayload, facts: dict) -> None:
    """Rule-first repair from factual curriculum; never touches commercial fields."""
    items = _curriculum_items(facts)
    corpus = " ".join(items).lower()
    name = payload.full_name
    outcomes, practices = [], []
    mappings = [
        (r"arduino", "Memahami konsep dasar Arduino untuk implementasi perangkat IoT.", "Menghubungkan sensor atau aktuator ke Arduino sesuai materi praktik."),
        (r"esp8266", "Mampu menggunakan ESP8266 untuk konektivitas perangkat IoT.", "Menggunakan ESP8266 untuk menghubungkan perangkat ke jaringan."),
        (r"sensor|actuator|aktuator", "Mampu mengintegrasikan sensor dan aktuator dalam prototipe IoT.", "Menguji pembacaan sensor dan kendali aktuator pada prototipe."),
        (r"web.*(?:control|kendali)|(?:control|kendali).*web", "Mampu membuat kontrol perangkat berbasis web.", "Membuat kontrol perangkat melalui antarmuka web."),
        (r"nmap|port scanning", "Mampu melakukan pemindaian port dalam ruang lingkup pengujian yang diotorisasi.", "Melakukan port scanning menggunakan Nmap pada lingkungan lab yang diotorisasi."),
        (r"reconnaissance|enumeration", "Mampu melakukan reconnaissance dan enumeration dasar secara etis.", "Melakukan reconnaissance pada target lab yang diotorisasi."),
        (r"vulnerabil", "Mampu mengidentifikasi kerentanan dasar dan menyusun rekomendasi perbaikan.", "Melakukan vulnerability assessment pada lingkungan lab."),
        (r"burp|metasploit|exploit", "Mampu menggunakan tools pentest dasar dalam lingkungan lab yang legal.", "Menguji eksploitasi dasar menggunakan tools yang dibahas pada lingkungan lab."),
        (r"capcut", "Mampu menggunakan CapCut untuk menyunting konten video.", "Membuat dan menyunting video menggunakan CapCut."),
        (r"cutting|alur video", "Mampu menyusun alur video melalui teknik cutting.", "Melakukan cutting dan menyusun alur video."),
        (r"teks|musik|transisi|efek", "Mampu menerapkan elemen teks, musik, transisi, dan efek pada video.", "Menambahkan teks, musik, transisi, atau efek pada video."),
        (r"webhook", "Mampu mengintegrasikan aplikasi eksternal melalui webhook.", "Membuat workflow yang menerima data melalui webhook."),
        (r"http request|\bapi\b", "Mampu menghubungkan workflow dengan layanan eksternal melalui API.", "Mengonfigurasi HTTP Request untuk mengakses API yang dibahas."),
        (r"google sheets", "Mampu mengotomatisasi pembacaan dan pembaruan data Google Sheets.", "Membuat workflow yang membaca atau memperbarui Google Sheets."),
        (r"telegram|whatsapp", "Mampu membuat otomasi pesan melalui Telegram atau WhatsApp.", "Membuat bot notifikasi pada Telegram atau WhatsApp."),
        (r"psk authentication|fat ap", "Mampu mengonfigurasi autentikasi PSK pada Fat AP.", "Mengonfigurasi basic access dan autentikasi PSK pada Fat AP."),
        (r"bridging|roaming", "Memahami konfigurasi bridging dan roaming pada WLAN.", "Menguji bridging atau roaming pada skenario WLAN."),
        (r"rf basics|802\.11|wlan", "Memahami konsep RF, protokol 802.11, dan komponen WLAN.", "Menganalisis parameter dasar RF dan konfigurasi WLAN pada lab."),
    ]
    for pattern, outcome, practice in mappings:
        if re.search(pattern, corpus, re.I):
            outcomes.append(outcome); practices.append(practice)
    if not outcomes:
        for item in items[:5]:
            topic = _topic(item)
            if topic:
                outcomes.append("Mampu " + topic[0].lower() + topic[1:] + ".")
    if not practices:
        for item in items:
            topic = _topic(item)
            if re.search(r"konfigur|implement|prakt|lab|membuat|menghubung|menggunakan|menambah|testing|anal", topic, re.I):
                practices.append("Melakukan praktik " + topic[0].lower() + topic[1:] + ".")
    payload.learning_outcomes = deduplicate(outcomes)[0][:5] or payload.learning_outcomes[:5]
    payload.practice_examples = deduplicate(practices)[0][:4] or payload.practice_examples[:4]
    # Conservative audience derivation only when source audience is generic.
    if not payload.target_audiences or all(any(p.fullmatch(x.audience.strip()) for p in GENERIC_PATTERNS) or x.audience.strip().endswith("?") for x in payload.target_audiences):
        category = payload.category.lower()
        candidates = (["Pemula di bidang IoT", "Mahasiswa teknologi informasi", "Pengembang perangkat IoT pemula"] if "iot" in category or "robot" in category else
                      ["Administrator jaringan", "Network engineer pemula", "Profesional IT yang mempelajari keamanan defensif"] if "cyber" in category else
                      ["Pemilik bisnis", "Manajer proyek", "Mahasiswa teknologi informasi", "Analis data"] if "automation" in name.lower() else
                      ["Content creator pemula", "Pemilik bisnis yang mengelola media sosial", "Profesional yang membangun personal branding"] if "content" in name.lower() else
                      ["Peserta pemula sesuai bidang training"])
        from app.resolver.models import TargetAudience
        payload.target_audiences = [TargetAudience(audience=x, problem_solved=f"Membantu peserta mempelajari dasar {name} sesuai cakupan kurikulum.") for x in candidates[:5]]


def sanitize_payload(result: ResolvedProduct, facts: dict) -> tuple[KBProductPayload, list[dict]]:
    payload = result.payload.model_copy(deep=True)
    changes = []
    before = payload.short_description
    factual_description=((facts.get("description") or {}).get("value") if isinstance(facts.get("description"),dict) else None) or before
    if not re.search(r"\b(training|pelatihan)\b", factual_description, re.I):
        factual_description=f"Training {payload.full_name} membahas {factual_description[0].lower()+factual_description[1:]}"
    payload.short_description = safe_truncate(factual_description)
    if payload.short_description != before:
        changes.append({"field":"short_description","action":"SAFE_TRUNCATION"})
    before = payload.repeat_policy
    payload.repeat_policy = clean_repeat_policy(before)
    if payload.repeat_policy != before:
        changes.append({"field":"repeat_policy","action":"REMOVE_FACILITY_CONTAMINATION"})
    for field in SEMANTIC_LIST_FIELDS:
        original = list(getattr(payload, field))
        values, removed = deduplicate(original)
        if field in ("learning_outcomes", "practice_examples"):
            values = [x for x in values if not CAREER_CLAIMS.search(x) and not RISKY_CLAIMS.search(x)]
        setattr(payload, field, values)
        if values != original:
            changes.append({"field":field,"action":"REMOVE_DUPLICATE_OR_UNSUPPORTED","removed":removed})
    derive_semantics(payload, facts)
    # Selling points remain evidence-based but must not duplicate repaired outcomes.
    payload.selling_points = [x for x in payload.selling_points if not any(p.fullmatch(x.strip()) for p in GENERIC_PATTERNS) and not any(similarity(x, y) >= .72 for y in payload.learning_outcomes) and not RISKY_CLAIMS.search(x)][:5]
    if len(payload.selling_points)<2:
        curriculum=_curriculum_items(facts)
        factual=[]
        if curriculum:factual.append(f"Kurikulum mencakup {', '.join(_topic(x) for x in curriculum[:3])}.")
        description=((facts.get("description") or {}).get("value") or "") if isinstance(facts.get("description"),dict) else ""
        if re.search(r"hands-on|praktik langsung|lab",description,re.I):factual.append("Pembelajaran menggunakan pendekatan praktik langsung sesuai materi training.")
        payload.selling_points=deduplicate([*payload.selling_points,*factual])[0][:5]
    return payload, changes


def evaluate(result: ResolvedProduct, payload: KBProductPayload, facts: dict, changes: list[dict] | None = None) -> QualityReport:
    warnings, errors = [], []
    checks: dict[str, Any] = {"semantic_separation":{"passed":True,"duplicates":[]},"claim_grounding":{"passed":True,"items":[]},"language_quality":{"passed":True},"duplication":{"passed":True,"removed":changes or []},"field_relevance":{"passed":True,"violations":[]},"commercial_safety":{"passed":True},"cross_field_consistency":{"passed":True}}
    facts_text = _facts_text(facts).lower()
    for field in SEMANTIC_LIST_FIELDS:
        values = getattr(payload, field)
        for value in values:
            if any(p.fullmatch(value.strip()) for p in GENERIC_PATTERNS):
                warnings.append(QualityIssue(code="GENERIC_LOW_INFORMATION",field=field,message="Konten terlalu generik.",value=value));checks["field_relevance"]["passed"]=False
            if RISKY_CLAIMS.search(value):
                warnings.append(QualityIssue(code="UNSUPPORTED_MARKETING_CLAIM",field=field,message="Klaim pemasaran berisiko tidak didukung.",value=value));checks["claim_grounding"]["passed"]=False
            topic = sorted(tokens(value) & tokens(facts_text))
            grounded = bool(topic)
            checks["claim_grounding"]["items"].append({"field":field,"value":value,"evidence_topics":topic[:8],"grounded":grounded})
            if not grounded:
                warnings.append(QualityIssue(code="UNGROUNDED_SEMANTIC_ITEM",field=field,message="Tidak ditemukan topik pendukung pada facts.",value=value));checks["claim_grounding"]["passed"]=False
    for left, right in (("learning_outcomes","practice_examples"),("learning_outcomes","selling_points"),("practice_examples","selling_points")):
        for a in getattr(payload,left):
            for b in getattr(payload,right):
                score = similarity(a,b)
                if score >= .82:
                    item={"fields":[left,right],"left":a,"right":b,"similarity":round(score,3)};checks["semantic_separation"]["duplicates"].append(item)
                    warnings.append(QualityIssue(code="CROSS_FIELD_DUPLICATION",field=f"{left}:{right}",message="Nilai sangat mirip di dua field.",value=item));checks["semantic_separation"]["passed"]=False
    for value in payload.practice_examples:
        if normalize(value).split() and normalize(value).split()[0] not in PRACTICE_VERBS:
            warnings.append(QualityIssue(code="FIELD_RELEVANCE",field="practice_examples",message="Practice tidak diawali verba aktivitas.",value=value));checks["field_relevance"]["passed"]=False
    for value in payload.learning_outcomes:
        if normalize(value).split() and normalize(value).split()[0] not in OUTCOME_VERBS:
            warnings.append(QualityIssue(code="FIELD_RELEVANCE",field="learning_outcomes",message="Outcome tidak menyatakan kemampuan/pemahaman.",value=value));checks["field_relevance"]["passed"]=False
    if FACILITY_TERMS.search(payload.repeat_policy):
        errors.append(QualityIssue(code="COMMERCIAL_FIELD_CONTAMINATION",field="repeat_policy",message="Repeat policy mengandung fasilitas.",blocking=True));checks["commercial_safety"]["passed"]=False
    for item in payload.next_classes:
        if item.canonical_source_url==payload.seo_url or normalize(item.training_name)==normalize(payload.full_name):
            errors.append(QualityIssue(code="INVALID_NEXT_CLASS",field="next_classes",message="Kelas lanjutan tidak boleh sama dengan produk.",blocking=True));checks["cross_field_consistency"]["passed"]=False
        if normalize(item.reason)=="katalog idn dalam keluarga training yang sama":
            warnings.append(QualityIssue(code="GENERIC_NEXT_CLASS_REASON",field="next_classes",message="Alasan progresi kelas terlalu generik.",value=item.reason));checks["cross_field_consistency"]["passed"]=False
    if not payload.short_description or len(payload.short_description) > 350 or re.search(r"\s\S{1,3}$",payload.short_description) and not payload.short_description.endswith(('.', '!', '?')):
        warnings.append(QualityIssue(code="DESCRIPTION_QUALITY",field="short_description",message="Deskripsi kosong, terlalu panjang, atau terpotong."));checks["language_quality"]["passed"]=False
    # Existing resolver validation errors that represent unsafe schema/claims block publishing.
    blocking_codes={"CATEGORY_OUTSIDE_KB_TAXONOMY","UNKNOWN_TRAINER_ID","INVALID_URL","INVALID_PRICE","UNSUPPORTED_EXAM_INCLUDED","OUTPUT_LANGUAGE_MISMATCH"}
    for code in result.warnings:
        if code in blocking_codes:
            errors.append(QualityIssue(code=code,message="Blocking resolver validation error.",blocking=True))
    deductions = {
        "claim_grounding":25,"field_relevance":20,"semantic_separation":15,
        "commercial_safety":15,"language_quality":10,"duplication":10,"cross_field_consistency":5,
    }
    score=100
    for name, weight in deductions.items():
        if not checks[name].get("passed", True): score-=weight
    score=max(0,score)
    readiness = PublishReadiness.BLOCKED if errors or score < 75 else PublishReadiness.READY if score >= 90 and result.product_status.value == "RESOLVED" else PublishReadiness.REVIEW_REQUIRED
    return QualityReport(slug=result.slug,product_status=result.product_status.value,completion=result.completion,publish_readiness=readiness,score=score,checks=checks,errors=errors,warnings=warnings)
