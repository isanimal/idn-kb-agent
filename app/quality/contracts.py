"""Explicit semantic contracts used by deterministic validators and repair prompts."""

SEMANTIC_CONTRACTS = {
    "full_name": {"purpose":"Nama produk faktual; tidak boleh diinferensikan."},
    "short_name": {"purpose":"Nama singkat faktual atau kosong."},
    "category": {"purpose":"Nilai harus berasal dari taxonomy KB."},
    "seo_url": {"purpose":"URL landing page HTTP(S) yang valid."},
    "advertising_links": {"purpose":"URL iklan faktual; tidak boleh diciptakan."},
    "training_formats": {"purpose":"Format, durasi, jadwal, dan harga faktual; semantic AI tidak boleh mengubah."},
    "learning_outcomes": {
        "purpose": "Kemampuan atau pemahaman peserta setelah training.",
        "preferred_verbs": ("memahami", "menjelaskan", "mengidentifikasi", "menggunakan", "mengonfigurasi", "menerapkan", "membuat", "mengintegrasikan", "melakukan", "menganalisis", "mengelola", "troubleshooting"),
        "reject_topics": ("karier", "gaji", "penghasilan", "peluang kerja", "marketability", "fasilitas", "harga", "trainer", "pasti lulus"),
    },
    "practice_examples": {
        "purpose": "Latihan, lab, konfigurasi, implementasi, pengujian, atau aktivitas.",
        "preferred_verbs": ("melakukan", "membuat", "mengonfigurasi", "menghubungkan", "menguji", "menganalisis", "menggunakan", "menambahkan", "menyusun"),
        "reject_prefixes": ("memahami", "peluang karier", "training terbaik"),
    },
    "selling_points": {"purpose": "Alasan faktual training berguna atau menarik; bukan salinan outcome."},
    "target_audiences": {"purpose": "Kelompok peserta aktual dan kebutuhan yang dapat dipertanggungjawabkan."},
    "certifications": {"purpose":"Relasi sertifikasi/exam faktual tanpa eskalasi menjadi jaminan."},
    "tools": {"purpose":"Perangkat yang didukung evidence curriculum atau sumber produk."},
    "next_classes": {"purpose":"Produk katalog lain dengan progresi dan alasan spesifik."},
    "trainer_references": {"purpose":"Trainer dan internal ID harus merupakan exact registry match."},
    "repeat_policy": {"purpose": "Hanya kebijakan mengulang kelas."},
    "post_training_support": {"purpose": "Hanya dukungan setelah training."},
    "prerequisites": {"purpose":"Prasyarat eksplisit atau derivasi konservatif yang ditandai."},
    "short_description": {"purpose": "Deskripsi training spesifik, cakupan, dan kapabilitas utama."},
    "claims_to_avoid": {"purpose":"Batas klaim untuk komunikasi yang aman."},
    "additional_notes": {"purpose":"Catatan faktual tambahan, bukan tempat mengisi kekosongan."},
    "active": {"purpose":"Status operasional faktual; quality AI tidak boleh mengubah."},
}
