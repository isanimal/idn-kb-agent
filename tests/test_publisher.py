import pytest
from app.publisher.service import PublisherPreflightViolation,content_regression,decide_action,publisher_payload_path

def test_high_risk_conflict_is_preserved_for_dry_run():
    action,status=decide_action("repeat_policy","Existing full policy","Short policy",{"repeat_policy"})
    assert action=="PRESERVE_EXISTING" and status=="CONFLICT_PRESERVED"

def test_empty_payload_preserves_existing():
    assert decide_action("short_description","Existing","",set())==("PRESERVE_EXISTING","PRESERVE_EXISTING")

def test_safe_fill_and_update_are_local_actions():
    assert decide_action("seo_url","","https://idn.id/x",set())==("FILL_EMPTY","APPLIED_LOCALLY")
    assert decide_action("short_description","Old","New",set())==("UPDATE_VALUE","APPLIED_LOCALLY")

def test_high_risk_difference_never_overwrites_without_explicit_conflict_list():
    assert decide_action("active",True,False,set())==("PRESERVE_EXISTING","CONFLICT_PRESERVED")

def test_update_publisher_requires_merge_artifact(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PublisherPreflightViolation,match="MERGE_ARTIFACT_REQUIRED"):publisher_payload_path("missing","UPDATE_EXISTING")

def test_publisher_detects_semantic_content_downgrade():
    before="Scope authorization reconnaissance Nmap scanning enumeration HTTP SMB FTP SSH vulnerability CVE CVSS Metasploit Linux Windows evidence finding severity report retest"
    after="Nmap scanning"
    assert content_regression(before,after,"learning_outcomes")
