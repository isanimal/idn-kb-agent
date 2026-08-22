import inspect

from app.candidate import service
from app.resolver.models import KBProductPayload


def test_all_payload_fields_have_publisher_parity(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "MODELS", tmp_path)
    parity = service.parity_model()
    assert set(parity) == set(KBProductPayload.model_fields)
    assert all(item["implemented"] for item in parity.values())


def test_nonempty_unmapped_payload_field_blocks_parity():
    result = service.parity_check({"full_name": "Product", "future_field": "value"})
    assert result["missing"] == ["future_field"]


def test_advertising_links_are_canonically_deduplicated():
    rows = [
        {"url": "https://example.test/path/", "label": "one"},
        {"url": "https://example.test/path", "label": "duplicate"},
    ]
    assert service._canonical_rows(rows, "advertising_links") == [rows[0]]


def test_dynamic_removal_detection_uses_row_identity_not_full_value():
    before = [{"format": "Hybrid", "duration": "2 hari", "public_price_reference": ""}]
    after = [{"format": "Hybrid", "duration": "2 hari", "public_price_reference": "2.800.000"}]
    assert not service._has_removal("training_formats", before, after)
    assert service._has_removal("training_formats", before, [])


def test_target_and_trainer_identity_are_exact_and_stable():
    assert service._row_identity("target_audiences", {"audience": "Network Engineer"}) == "network engineer"
    assert service._row_identity("trainer_references", {"trainer_name": "A", "kb_trainer_id": "id-1"}) == "id-1"


def test_live_certification_schema_is_exact_and_exam_code_is_not_guessed():
    assert service.EXPECTED_LABELS["certifications"] == [
        "Nama sertifikat", "Level", "Biaya ujian referensi (Rp)", "Durasi exam",
        "Jumlah soal", "Skor lulus minimal", "Open book", "Kebijakan retake exam",
    ]
    source = inspect.getsource(service.dry_run)
    assert "CERTIFICATION_SCHEMA_AMBIGUOUS" in source
    assert 'x.get("exam_code")' in source


def test_unknown_tool_provider_is_never_guessed():
    source = inspect.getsource(service.dry_run)
    assert 'item.get("provided_by")=="UNKNOWN"' in source
    assert '"reason":"UNKNOWN_PROVIDER"' in source


def test_candidate_hash_is_deterministic_and_bound_to_baseline():
    route = {"kb_product_id": "id-1", "target_url": "https://kb.idn.id/kb/training/edit?id=id-1"}
    args = ("slug", route, {"full_name": "A"}, {"fields": []}, "baseline-a", "inventory-a")
    assert service.candidate_hash(*args) == service.candidate_hash(*args)
    assert service.candidate_hash(*args) != service.candidate_hash("slug", route, {"full_name": "A"}, {"fields": []}, "baseline-b", "inventory-a")


def test_dry_run_source_has_no_save_or_dynamic_delete_action():
    source = inspect.getsource(service.dry_run)
    assert 'name="Simpan"' not in source
    assert 'name="Hapus"' not in source
    assert 'name="Delete"' not in source


def test_blank_dynamic_rows_have_canonical_empty_representation():
    cases = {
        "advertising_links": [{"url":"","label":""}],
        "next_classes": [{"training_name":"— Pilih produk —","reason":""}],
        "tools": [{"name":"","provided_by":"Disiapkan IDN"}],
        "target_audiences": [{"audience":"","problem_solved":""}],
        "training_formats": [{"format":"Offline","duration":"","schedule":"","public_price_reference":"0","private_price_reference":"0"}],
        "certifications": [{"name":"","level":"","open_book":"— Belum diisi —"}],
    }
    for field,rows in cases.items():assert service.normalize_dynamic_state(field,rows)==[]


def test_partially_populated_dynamic_row_is_preserved():
    rows=[{"url":"https://example.com","label":""}]
    assert service.normalize_dynamic_state("advertising_links",rows)==rows


def test_meaningful_certification_survives_placeholder_normalization():
    rows=[{"name":"Sertifikat Penyelesaian Training dari ID-Networkers","level":"","open_book":"— Belum diisi —"}]
    assert service.normalize_dynamic_state("certifications",rows)==[{"name":"Sertifikat Penyelesaian Training dari ID-Networkers","level":"","open_book":""}]
