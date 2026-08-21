from app.merge.service import decide,dedup,semantic_metrics

def test_existing_rich_new_poor_keeps_existing():
    existing=["Memahami scope dan authorization.","Melakukan reconnaissance dan scanning dengan Nmap.","Melakukan enumeration HTTP, SMB, FTP, dan SSH.","Menganalisis CVE dan CVSS.","Melakukan exploitation pada lab.","Menyusun evidence, finding, report, dan retest."]
    new=["Mampu melakukan port scanning.","Mampu menggunakan tools pentest."]
    decision,reason,old,newm=decide("learning_outcomes",existing,new,"Basic Penetration Testing")
    assert decision=="KEEP_EXISTING" and "INFORMATION_COVERAGE_REGRESSION" in reason and old["topic_count"]>newm["topic_count"]
def test_existing_empty_new_good_fills_empty():
    assert decide("seo_url","","https://idn.id/training/x")[0]=="FILL_EMPTY"
def test_existing_good_new_additional_good_augments():
    existing=["Mengonfigurasi VLAN pada switch.","Menguji routing OSPF."]
    new=["Mengonfigurasi VLAN pada switch.","Menguji routing OSPF.","Menganalisis troubleshooting BGP."]
    assert decide("learning_outcomes",existing,new,"Network Training")[0]=="AUGMENT_EXISTING"
def test_current_commercial_fact_replaces_existing():
    assert decide("seo_url","https://old.idn.id/x","https://www.idn.id/training/x/")[0]=="REPLACE_WITH_NEW"
def test_empty_new_keeps_existing():
    assert decide("practice_examples",["Lab Nmap"],[])[0]=="KEEP_EXISTING"
def test_generic_description_does_not_replace_specific_existing():
    existing="Training Basic Pentest membahas scope, authorization, reconnaissance, Nmap, enumeration, vulnerability, exploitation lab, evidence, report, dan retest."
    new="Ethical hacker adalah seseorang yang mempelajari keamanan."
    assert decide("short_description",existing,new,"Basic Pentest")[0]=="KEEP_EXISTING"
def test_duplicate_augmentation_removed():
    assert dedup(["Konfigurasi VLAN","konfigurasi vlan!","Routing OSPF"])==["Konfigurasi VLAN","Routing OSPF"]
def test_shortened_internal_policy_is_kept():
    decision,_,_,_=decide("repeat_policy","Gratis ulang dua kali. Bukan retake exam.","Gratis ulang dua kali.")
    assert decision=="KEEP_EXISTING"
def test_semantic_metrics_penalize_generic_and_risky_claims():
    good=semantic_metrics(["Melakukan scanning Nmap pada lab."])["score"]
    bad=semantic_metrics(["Menguasai materi sesuai kurikulum dan pasti langsung kerja."])["score"]
    assert good>bad
