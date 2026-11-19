import io
from datetime import date
from decimal import Decimal

import openpyxl
from fastapi.testclient import TestClient

from app import ntip
from app.main import create_app
from app.ntip_routes import NtipStore
from app.store import Store
from factories import settings
from test_api import FakeHosxp

CID = "1234567890123"


def visit(vn="v1", day="2025-11-05", hn="h1", born="1950-03-10", nationality="99", hipdata="UCS", **extra):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "pname": "นาย", "fname": "ทดสอบ", "lname": "ระบบ",
            "cid": CID, "sex": "1", "birthday": date.fromisoformat(born), "addrpart": "12", "moopart": "3", "chwpart": "27",
            "amppart": "01", "tmbpart": "05", "nationality": nationality, "hipdata_code": hipdata, "pttype": "72",
            "pttype_name": "บัตรทอง", **extra}


def data(visits, dx=None, cxr=None, labs=None, start="2025-11-01", end="2025-11-30"):
    return {"visits": visits, "dx": dx or {}, "cxr": cxr if cxr is not None else {v["vn"]: {"name": "Chest PA", "day": v["vstdate"]} for v in visits},
            "labs": labs or {}, "start": date.fromisoformat(start), "end": date.fromisoformat(end)}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_risk_is_guessed_from_diagnoses_then_age_then_nationality():
    assert ntip.guess_risk(["Z111", "B24"], 30, "99") == ("RST3", "B24")
    assert ntip.guess_risk(["E119"], 70, "99") == ("RST5", "E119")
    assert ntip.guess_risk(["Z111"], 70, "99") == ("RST10", "")
    assert ntip.guess_risk(["Z111"], 30, "57") == ("RST14", "")
    assert ntip.guess_risk(["Z111"], 30, "99") == ("", "")


def test_a_screening_row_fills_the_template_columns():
    (s,) = ntip.check_screening(data([visit()]), {})
    v = s.values
    assert list(v) == ntip.COLUMNS
    assert (v["RISK_TYPE"], v["TITLE_ID"], v["GENDER"], v["BORN"], v["AGE_Y"], v["AGE_M"]) == ("RST10", "นาย", "M", "19500310", "75", "7")
    assert (v["PROVINCE_ID"], v["AMPHUR_ID"], v["TAMBOL_ID"], v["PEOPLE_TYPE"], v["RACE_ID"]) == ("27", "2701", "270105", "ไทย", "0=ไทย")
    assert (v["INSCL_ID"], v["CONTACT_DATE"], v["CXR_DATE"], v["CXR_RESULT"], v["SYMPTOM_SCREEN"], v["DX"]) == (
        "สิทธิหลักประกันสุขภาพถ้วนหน้า", "20251105", "20251105", "Normal", "ไม่สงสัยวัณโรค (คะแนน <3)", "Normal")
    assert codes(s) == ([], [])


def test_picked_values_win_and_groups_need_their_fields():
    rows = ntip.check_screening(data([visit()]), {"v1": {"risk": "RST23"}})
    assert codes(rows[0])[0] == ["INDEX_CID"]
    (s,) = ntip.check_screening(data([visit()]), {"v1": {"risk": "RST23", "index_cid": "9" * 13}})
    assert s.values["TB_CID_INDEX"] == "9" * 13 and codes(s) == ([], [])
    (s,) = ntip.check_screening(data([visit()]), {"v1": {"risk": "RST30"}})
    assert set(codes(s)[0]) == {"ICD10", "HBA1C"}
    (s,) = ntip.check_screening(data([visit()]), {"v1": {"risk": "RST43"}})
    assert codes(s)[0] == ["PRISON"]
    (s,) = ntip.check_screening(data([visit()]), {"v1": {"cxr_result": "02"}})
    assert codes(s)[0] == ["CXR_ABNORMAL"]


def test_b24_takes_cd4_and_diabetes_takes_hba1c_from_labs():
    labs = {"h1": [{"day": date(2025, 10, 20), "code": 196, "name": "CD4 count", "result": "250"},
                   {"day": date(2025, 10, 20), "code": 198, "name": "HbA1C", "result": "8.4 %"}]}
    (s,) = ntip.check_screening(data([visit()], dx={"v1": ["B24"]}, labs=labs), {})
    assert (s.values["RISK_TYPE"], s.values["B24"]) == ("RST3", "250") and codes(s) == ([], [])
    (s,) = ntip.check_screening(data([visit()], dx={"v1": ["E119"]}, labs=labs), {"v1": {"risk": "RST30"}})
    assert (s.values["ICD10"], s.values["HbA1C"]) == ("E119", "8.4") and codes(s) == ([], [])


def test_repeat_screen_in_the_fiscal_year_is_counted_from_october():
    visits = [visit("v0", "2025-10-03"), visit("v1", "2025-11-05")]
    (s,) = ntip.check_screening(data(visits), {})
    assert s.nth == 2 and "REPEAT" in codes(s)[1]
    (s,) = ntip.check_screening(data(visits), {"v1": {"risk": "RST43", "prison_hosp": "19001"}})
    assert "REPEAT" not in codes(s)[1]


def test_no_cxr_is_a_warning_and_tb_diagnosis_sets_dx():
    (s,) = ntip.check_screening(data([visit()], cxr={}, dx={"v1": ["A150"]}), {})
    assert (s.values["CXR_RESULT"], s.values["CXR_DATE"], s.values["DX"]) == ("", "", "TB")
    assert "NO_CXR" in codes(s)[1]


def test_xlsx_has_the_template_header_and_text_cells():
    (s,) = ntip.check_screening(data([visit()]), {})
    sheet = openpyxl.load_workbook(io.BytesIO(ntip.screening_xlsx([s])))["TEMPLATE-IMPORT"]
    assert [c.value for c in sheet[1]] == ntip.COLUMNS
    row = {h: c for h, c in zip(ntip.COLUMNS, sheet[2])}
    assert row["CID"].value == CID and row["CID"].number_format == "@" and row["BORN"].value == "19500310"


def test_choices_are_validated():
    assert ntip.valid_choice("risk", "RST3") == "RST3"
    for name, value in (("risk", "X"), ("index_cid", "123"), ("cd4", "12.5"), ("nope", "1")):
        try:
            ntip.valid_choice(name, value)
        except ntip.ChoiceError:
            continue
        raise AssertionError(name)


def lab(no, code, name, day, result="", hn="h1"):
    return {"order_no": no, "code": code, "name": name, "vn": f"vn{no}", "hn": hn, "day": date.fromisoformat(day),
            "result": result, "pname": "นาย", "fname": "ก", "lname": "ข", "cid": CID, "pttype": "72", "pttype_name": "บัตรทอง"}


def test_lab_methods_and_rates():
    assert ntip.method_of("TB LAMP").key == "lamp" and ntip.method_of("Sputum gene expert").rate == Decimal(500)
    assert ntip.method_of("Sputum AFB ครั้งที่ 1").key == "afb" and ntip.method_of("TB Culture").key == "culture"
    assert ntip.method_of("PCR for TB").key == "mtbmdr" and ntip.method_of("Urine culture") is None


def test_afb_over_three_and_molecular_over_one_are_not_claimable():
    rows = [lab(i, 163, "Sputum AFB ครั้งที่ 1", f"2025-11-0{i}", "No AFB observed") for i in range(1, 5)]
    rows += [lab(10, 777, "TB LAMP", "2025-11-05", "Negative"), lab(11, 417, "Sputum gene expert", "2025-11-06", "MTB Not detected")]
    out = {r.key: r for r in ntip.check_labs(rows, date(2025, 11, 1))}
    assert [out[f"{i}:163"].claimable for i in range(1, 5)] == [True, True, True, False]
    assert out["10:777"].claimable and not out["11:417"].claimable


def test_a_resistance_test_after_a_positive_lamp_is_allowed():
    rows = [lab(1, 777, "TB LAMP", "2025-11-05", "Positive"), lab(2, 417, "Sputum gene expert", "2025-11-06", "MTB detected Low")]
    first, second = ntip.check_labs(rows, date(2025, 11, 1))
    assert "LAMP_POSITIVE" in codes(first)[1] and second.claimable and "AFTER_LAMP" in codes(second)[1]


def test_counts_start_at_the_fiscal_year_and_culture_needs_a_technique():
    rows = [lab(1, 279, "TB Culture", "2025-10-02", "x"), lab(2, 279, "TB Culture", "2025-11-02", "x")]
    (row,) = ntip.check_labs(rows, date(2025, 11, 1), {"2:279": "liquid"})
    assert row.nth == 2 and not row.claimable and row.rate == Decimal(300)
    (row,) = ntip.check_labs(rows[1:], date(2025, 11, 1))
    assert row.claimable and row.rate is None and "TECHNIQUE" in codes(row)[1]


def test_second_line_needs_first_line_resistance():
    (row,) = ntip.check_labs([lab(1, 900, "MTB/XDR", "2025-11-02", "x")], date(2025, 11, 1))
    assert row.rate == Decimal(700) and "NO_RR" in codes(row)[1]


def test_store_and_routes(tmp_path):
    local = Store(tmp_path / "s.sqlite3")
    store = NtipStore(local)
    store.set_choice("v1", "risk", "RST3")
    store.set_technique("1:279", "solid")
    store.mark_exported(["v1"], "me")
    assert store.choices(["v1", "v2"]) == {"v1": {"risk": "RST3"}} and store.techniques() == {"1:279": "solid"}
    assert list(store.exported(["v1"])) == ["v1"]
    client = TestClient(create_app(settings(), hosxp=FakeHosxp(), store=local))
    assert client.get("/ntip").status_code == 200
    assert any(r["code"] == "RST3" for r in client.get("/api/ntip/options").json()["risks"])
    assert client.post("/api/ntip/choice", json={"vn": "v1", "name": "risk", "value": "RST10"}).json()["value"] == "RST10"
    assert client.post("/api/ntip/choice", json={"vn": "v1", "name": "risk", "value": "BAD"}).status_code == 400
    assert client.post("/api/ntip/choice", json={"vn": "v1", "name": "risk", "reset": True}).json()["ok"]
    assert client.post("/api/ntip/lab-choice", json={"key": "1:279", "technique": "gold"}).status_code == 400
