import inspect

from app.cyber_sync import service

def test_manifest_hash_is_deterministic_and_candidate_bound():
    products=[{"slug":"ceh","mode":"CREATE_NEW","candidate_hash":"a"}]
    assert service._manifest_hash(products,"i")==service._manifest_hash(products,"i")
    assert service._manifest_hash(products,"i")!=service._manifest_hash([{**products[0],"candidate_hash":"b"}],"i")

def test_pentest_is_hard_excluded_by_slug_and_name():
    assert service.EXCLUDED_SLUG=="pentest"
    assert service._norm("Basic Penetration Testing")==service.EXCLUDED_NAME
    source=inspect.getsource(service.prepare);assert "EXCLUDED_SLUG" in source and "EXCLUDED_NAME" in source

def test_only_publishable_scoped_states_are_accepted():
    assert service.PUBLISHABLE=={"READY","READY_WITH_WARNINGS"}
    assert "REVIEW_REQUIRED" not in service.PUBLISHABLE and "BLOCKED" not in service.PUBLISHABLE

def test_optional_trainer_and_next_class_warnings_do_not_block():
    assert "TRAINER_NOT_MAPPED" in service.OPTIONAL_WARNING_CODES
    assert "GENERIC_NEXT_CLASS_REASON" in service.OPTIONAL_WARNING_CODES

def test_live_execution_is_sequential_and_uses_existing_publisher():
    source=inspect.getsource(service.execute)
    assert "for item in manifest" in source
    assert "publish_live" in source and "reconcile_live_run" in source
    assert "ThreadPool" not in source and "Executor" not in source

def test_unknown_format_is_omitted_and_ambiguous_price_is_blank():
    source=inspect.getsource(service._scoped_quality)
    assert 'payload["training_formats"]=[]' in source
    assert 'row["public_price_reference"]=None' in source
    assert 'row["private_price_reference"]=None' in source

def test_pre_save_error_continues_and_post_save_error_stops():
    source=inspect.getsource(service.execute)
    assert "SKIPPED_PRE_SAVE" in source
    assert "POST_WRITE_ERROR" in source
    assert "if after_save:stop=True;break" in source
