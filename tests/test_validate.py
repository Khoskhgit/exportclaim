from datetime import date

import pytest

from app import validate as v
from factories import drug, item, visit

Z = {"Z000"}


def codes(the_visit, items=None, z=Z, ready=True):
    items = [item()] if items is None else items
    return {i.code: i.level for i in v.check_visit(the_visit, items, ("11111",), z, ready)}


def test_clean_visit_has_no_issues():
    assert codes(visit()) == {}


@pytest.mark.parametrize("pdx,code,level", [
    ("", "PDX_EMPTY", v.ERROR),
    ("V01", "PDX_EXTERNAL", v.ERROR),
    ("Y99", "PDX_EXTERNAL", v.ERROR),
    ("Z999", "PDX_Z_NOT_ALLOWED", v.WARN_C),
])
def test_pdx_rules(pdx, code, level):
    assert codes(visit(pdx=pdx)).get(code) == level


def test_claimable_z_code_passes():
    assert "PDX_Z_NOT_ALLOWED" not in codes(visit(pdx="Z000"))


def test_pdx_not_in_nhso_table_warns():
    assert codes(visit(nhso_code=None))["PDX_NOT_FOUND"] == v.WARN


def test_z_check_is_skipped_when_no_z_table():
    assert "PDX_Z_NOT_ALLOWED" not in codes(visit(pdx="Z999"), z=set(), ready=False)


@pytest.mark.parametrize("changes,items,code", [
    ({"income": 0}, [], "INCOME_ZERO"),
    ({"income": 999}, None, "INCOME_MISMATCH"),
    ({}, [item(qty=2)], "INCOME_MISMATCH"),
    ({"rcpt_money": 10}, None, "PAYMENT_MISMATCH"),
    ({"clinic": "55"}, None, "CLINIC_INVALID"),
    ({"typeout_name": "Admit"}, None, "ADMITTED"),
    ({"an": "6900001"}, None, "HAS_AN"),
    ({"hospmain": ""}, None, "HOSPMAIN_EMPTY"),
    ({"hospmain": "99999"}, None, "HOSPMAIN_WRONG"),
    ({"cid": ""}, None, "CID_EMPTY"),
    ({"vsttime": None}, None, "END_TIME_EMPTY"),
    ({"hipdata_code": "UCS"}, None, "PAYPLAN_UNKNOWN"),
    ({}, [item(cscd_group=None)], "BILLMUAD_EMPTY"),
    ({}, [item(cscd_group="3")], "GROUP_MAP_WRONG"),
    ({}, [item(name=None)], "DRUG_NAME_EMPTY"),
    ({}, [item(hn="HN2")], "OTHER_HN"),
])
def test_error_rules(changes, items, code):
    assert codes(visit(**changes), items)[code] == v.ERROR


@pytest.mark.parametrize("changes,items,code", [
    ({"doctor": "สมชาย"}, None, "DOCTOR_INVALID"),
    ({"datein": None}, None, "DATEIN_EMPTY"),
    ({"datein": date(2027, 1, 1)}, None, "DATEIN_AFTER_VISIT"),
    ({"dateexp": date(2025, 1, 1)}, None, "DATEEXP_BEFORE_VISIT"),
    ({}, [item(drg_chrgitem_id=7, lab_chi="1", tmlt_code="X1")], "TMLT_INCOMPLETE"),
])
def test_warning_rules(changes, items, code):
    assert codes(visit(**changes), items)[code] == v.WARN


@pytest.mark.parametrize("the_visit,items,label", [
    (visit(), [item(nhso_adp_code="51120")], "Sleep Test"),
    (visit(pdx="C50"), [item(nhso_adp_code="C01X")], "มะเร็ง (SSOCAC)"),
    (visit(pdx="C50"), [item(ssocac_item=1)], "มะเร็ง (SSOCAC)"),
    (visit(), [item(nhso_adp_code="4018", nhso_adp_type_id=2)], "เตรียมเส้นฟอกไต"),
    (visit(vstdate=date(2026, 6, 1), typein_code="5"), [item()], "TeleMed"),
    (visit(vstdate=date(2022, 1, 1)), [item(nhso_adp_code="9902")], "โควิด"),
])
def test_special_cases_are_detected(the_visit, items, label):
    assert v.special_case(the_visit, items) == label


def test_cancer_pdx_without_project_items_is_normal():
    assert v.special_case(visit(pdx="C50"), [item()]) == ""


def test_telemed_after_old_window_is_normal():
    assert v.special_case(visit(vstdate=date(2026, 8, 1), typein_code="5"), [item()]) == ""


def test_review_blocks_errors_and_special_cases():
    assert not v.review_visit(visit(pdx=""), [item()], ("11111",), Z, True)["selectable"]
    assert not v.review_visit(visit(), [item(nhso_adp_code="51120")], ("11111",), Z, True)["selectable"]


def test_review_does_not_preselect_warn_c_or_sent():
    warn_c = v.review_visit(visit(pdx="Z999"), [item()], ("11111",), Z, True)
    assert warn_c["selectable"] and not warn_c["default_selected"]
    sent = v.review_visit(visit(chi_status="A"), [item()], ("11111",), Z, True)
    assert sent["selectable"] and sent["already_sent"] and not sent["default_selected"]


def test_rejected_claim_can_be_resent_without_edit_flag():
    review = v.review_visit(visit(chi_status="C"), [item()], ("11111",), Z, True)
    assert review["status"] == "ติด C" and not review["already_sent"]


@pytest.mark.parametrize("changes,plan", [
    ({}, "80"), ({"hipdata_code": "SSI"}, "86"), ({"pttype_eclaim_id": "13"}, "81"),
    ({"pttype_eclaim_id": "19"}, "20"), ({"upp_code": "30"}, "30"), ({"hipdata_code": "X"}, "XX"),
    ({"hipdata_code": "SSS"}, "80"), ({"hipdata_code": "A2", "maininscl": "OFC"}, "10"),
])
def test_pay_plan(changes, plan):
    assert v.pay_plan(visit(**changes)) == plan


def test_end_time_uses_the_latest_time():
    from datetime import datetime
    assert v.end_datetime(visit(depart_out=datetime(2026, 10, 1, 11, 0))) == datetime(2026, 10, 1, 11, 0)


def test_hospmain_checked_for_sss_rights():
    issues = v.check_visit(visit(hipdata_code="SSS", maininscl="SSS", hospmain="99999"), [item()], ["11111"], set(), False)
    assert "HOSPMAIN_WRONG" in {i.code for i in issues}


@pytest.mark.parametrize("fields,reason", [
    ({"lab_chi": None, "tmlt_code": None}, "ไม่พบใน labitem"),
    ({"lab_chi": "0", "tmlt_code": "350110"}, "labitem.chi ไม่เป็น 1"),
    ({"lab_chi": "1", "tmlt_code": ""}, "ไม่มีรหัส TMLT"),
    ({"lab_chi": "1", "tmlt_code": "35X110"}, "รหัส TMLT มีตัว X"),
    ({"lab_chi": "1", "tmlt_code": "350110"}, ""),
])
def test_tmlt_problem_names_the_reason(fields, reason):
    assert v.tmlt_problem(item(drg_chrgitem_id=7, **fields)) == reason


def test_tmlt_problem_ignores_non_lab_items():
    assert v.tmlt_problem(item(drg_chrgitem_id=12)) == ""


def test_missing_usage_is_not_a_warning_because_export_fills_it():
    issues = v.check_visit(visit(income=50), [drug(usage_code=None, sum_price=50, unitprice=50)], ["11111"], set(), False)
    assert "USAGE_EMPTY" not in {i.code for i in issues}


def test_result_codes_put_causes_before_warnings():
    result = v.explain_result_codes("W07,S21,Q99", {"S21": "SvCharge", "W07": "TMT"})
    assert [r["code"] for r in result] == ["S21", "Q99", "W07"]
    assert result[0]["groups"] == ["B"] and not result[0]["warning"]
    assert result[1]["name"] == "ไม่มีคำอธิบายใน HOSxP"
    assert result[2]["warning"]


def test_no_result_codes():
    assert v.explain_result_codes(None, {}) == []
