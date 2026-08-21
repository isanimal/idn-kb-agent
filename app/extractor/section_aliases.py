"""Conservative heading aliases based on observed IDN landing pages."""

import re

ALIASES = {
    "pengenalan training": "DESCRIPTION",
    "description": "DESCRIPTION", "deskripsi": "DESCRIPTION",
    "benefits": "BENEFITS", "benefit": "BENEFITS", "objectives": "BENEFITS",
    "fasilitas": "FACILITIES", "fasilitas peserta": "FACILITIES",
    "prerequisites": "PREREQUISITES", "prerequisite": "PREREQUISITES",
    "syarat mengikuti training": "PREREQUISITES",
    "target peserta": "TARGET_AUDIENCE", "siapa yang harus mengikuti": "TARGET_AUDIENCE",
    "trainer": "TRAINERS",
    "kurikulum": "CURRICULUM", "curriculum": "CURRICULUM",
    "outline": "CURRICULUM", "materi": "CURRICULUM", "materi training": "CURRICULUM",
    "materi training all in one": "CURRICULUM",
    "durasi training": "DURATION", "waktu training": "DURATION",
    "perangkat lab hands on": "TOOLS", "tools": "TOOLS",
    "contoh praktek": "PRACTICE", "praktik": "PRACTICE",
    "support": "SUPPORT", "support pasca training": "SUPPORT",
    "sertifikasi": "CERTIFICATIONS", "certification": "CERTIFICATIONS",
}


def normalize_heading(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def semantic_section(heading: str) -> str:
    normalized = normalize_heading(heading)
    if re.search(r"\brp\.?\s*\d|\b\d+(?:[.,]\d+)?\s*(?:juta|jt)\b", heading, re.I): return "PRICE_VALUE"
    if re.match(r"^(?:module|modul|day|chapter|topic)\s*\d+\b", normalized): return "CURRICULUM_UNIT"
    if re.match(r"^\d{1,2}(?:\.\d+)*[.)]?\s+", heading.strip()) or re.match(r"^hari\s*\d+", normalized): return "CURRICULUM_UNIT"
    if normalized.startswith("biaya investasi") or normalized.startswith("harga"):
        return "PRICE"
    if normalized.startswith("mengapa harus") or normalized.startswith("mengapa harus mempelajari"):
        return "WHY_TRAINING"
    if "fasilitas training" in normalized: return "SECTION_CONTAINER"
    if normalized in {"fasilitas lengkap", "fasilitas peserta", "fasilitas"}: return "FACILITIES"
    if "siapa yang cocok" in normalized or "direkomendasikan untuk" in normalized or normalized.startswith("program ini cocok"):
        return "TARGET_AUDIENCE"
    if "apa yang akan anda pelajari" in normalized or "silabus training" in normalized or "kurikulum training" in normalized or "materi training" in normalized or "unit kompetensi yang diujikan" in normalized:
        return "CURRICULUM"
    if "proyek praktis" in normalized or "praktik studi kasus" in normalized: return "PRACTICE"
    if "free mengulang" in normalized or "gratis mengulang" in normalized or "repeat training" in normalized: return "REPEAT_POLICY"
    if "dukungan pasca" in normalized or "sesi tanya jawab dan dukungan" in normalized or "after sales" in normalized: return "SUPPORT"
    if "trainer bersertifikasi" in normalized or "trainer tersertifikasi" in normalized or "instruktur berpengalaman" in normalized: return "TRAINERS"
    if normalized.startswith("apa itu sertifikasi") or "sertifikat dan pengakuan" in normalized: return "CERTIFICATIONS"
    if "offline" in normalized or "online class" in normalized: return "TRAINING_FORMAT"
    if normalized.startswith("kenapa") and ("istimewa" in normalized or "memilih" in normalized): return "BENEFITS"
    if normalized in {"open source dan mudah digunakan", "integrasi tanpa batas", "solusi untuk setiap bisnis"}: return "BENEFITS"
    if "testimoni" in normalized or "alumni" in normalized: return "TESTIMONIAL"
    if normalized in {"our clients", "our training clients"} or "training clients" in normalized: return "CLIENTS"
    if "jadwal training" in normalized or "cek jadwal" in normalized: return "SCHEDULE_WIDGET"
    if normalized.startswith("siap untuk") or normalized.startswith("tunggu apalagi") or "daftar sekarang" in normalized: return "CTA"
    if "dokumentasi" in normalized or "portfolio" in normalized: return "DOCUMENTATION"
    if normalized == "faq" or normalized.endswith("berlaku berapa lama") or normalized.startswith("apakah harus"): return "FAQ"
    if normalized.startswith("alur sertifikasi"): return "CERTIFICATION_PROCESS"
    if normalized in {"curiculum", "curricullum", "curicullum", "course outline"} or "berikut kurikulumnya" in normalized:
        return "CURRICULUM"
    if normalized in {"requirement", "student prerequisites", "prerequisites persiapan peserta"} or "perlu dipersiapkan" in normalized:
        return "PREREQUISITES"
    if normalized in {"profile trainer", "profil trainer"} or "asesor" in normalized or "certified trainer" in normalized:
        return "TRAINER_DETAIL"
    if any(term in normalized for term in ("beginner friendly", "expert instructor", "expert level", "materi terstruktur",
            "materi lengkap", "materi sesuai", "murah berkualitas", "hemat berkualitas", "terjangkau berkualitas",
            "legal resmi", "pendampingan", "dibimbing mentor", "standar kompetensi", "kesempatan mengulang exam",
            "lab komplit", "belajar 100 praktik", "tidak perlu bisa coding", "dari nol sampai jadi",
            "keunggulan utama", "apa yang akan kamu dapatkan", "mengapa anda harus mengikuti", "kenapa training ini penting")):
        return "MARKETING_BENEFIT"
    if any(term in normalized for term in ("fresh graduate", "mahasiswa", "career switcher", "karyawan isp", "staf it",
            "staf marketing", "teknisi jaringan", "pencari kerja", "web developer pemula", "network administrator pemula",
            "programmer pemula", "pelaku umkm", "freelancer", "profesional it", "training ini cocok untuk",
            "untuk siapa course")):
        return "TARGET_AUDIENCE_ITEM"
    if any(term in normalized for term in ("promo berlaku", "booking jadwal", "perhatian", "saatnya anda berhasil",
            "stop ragu", "kuasai ceh", "upgrade skill", "jangan lewatkan kesempatan")):
        return "CTA"
    if any(term in normalized for term in ("lokasi", "tempat pelaksanaan", "proses pendaftaran")): return "LOGISTICS"
    if normalized in {"menginap gratis", "materi lengkap update", "souvenir idn", "quizzes"} or normalized.startswith("sudah termasuk"):
        return "FACILITY_DETAIL"
    if any(term in normalized for term in ("kenapa sertifikasi", "kenapa harus belajar", "kenapa harus ambil", "lantas apa alasan",
            "manfaat mempelajari", "manfaat mengikuti", "pengembangan karir", "permintaan karir", "keahlian praktis",
            "kontribusi pada keamanan", "membuka peluang", "meningkatkan ", "diakui oleh industri")):
        return "MARKETING_BENEFIT"
    if normalized.startswith("j jadi ") or normalized.startswith("jadi ") or normalized.startswith("jadilah ") or normalized.startswith("tingkatkan skill"):
        return "HERO"
    if normalized.startswith("testimoni") or normalized.startswith("dokumentasi"):
        return "TESTIMONIAL_OR_DOCUMENTATION"
    return ALIASES.get(normalized, "UNKNOWN_SECTION")
