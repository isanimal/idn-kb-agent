from app.publisher.service import decide_action

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
