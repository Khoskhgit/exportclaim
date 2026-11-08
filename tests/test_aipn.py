import hashlib
import io
import zipfile
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import aipn
from app.main import create_app
from app.store import Store
from factories import settings

AN, AN2, AN_SSI, AN_ODS = "690000001", "690000002", "690000003", "690000004"


def adm(**overrides):
    values = dict(
        an=AN, hn="000000001", vn="690101000001", pttype="34", regdate=date(2026, 5, 17), regtime=timedelta(hours=10, minutes=27),
        dchdate=date(2026, 5, 20), dchtime=timedelta(hours=9, minutes=30), ods=None, cid="3100000000001",
        ptname="นายทดสอบ ผ่าน", clinic="01", dchstts_name="ทุเลา", dchtype_name="แพทย์อนุญาต",
        pttype_name="ประกันสังคม", hipdata_code="SSS", maininscl="SSS", pttype_eclaim_id="10", upp_code=None,
        copttype=None, hospmain="11111", income=Decimal("150"), pdx="I639", doctor="ว12345",
        auth_code="", auth_datetime=None, rcm_status=None, cancer_caseno=None,
    )
    values.update(overrides)
    return values


def it(**overrides):
    values = dict(
        an=AN, hn="000000001", icode="3001372", income="12", group1=12, item_type="", paidst="02", qty=1,
        unitprice=Decimal("1000"), sum_price=Decimal("1000"), rxdate=date(2026, 5, 18), rxtime=timedelta(hours=9),
        pttype="34", name="12บริการพยาบาล", strength=None, nhso_adp_code="55010", nhso_adp_type_id=None,
        sks_drug_code=None, tpu_code_list=None, is_drug=0, tmtid=None, catalog_price=None, in_catalog=0,
        tmlt_code=None, in_labitem=0, lab_price=None, maininscl="SSS", pttype_eclaim_id="10",
        ssocac_item=0, hosxp_noexport=0,
    )
    values.update(overrides)
    return values


def drug_item(**overrides):
    values = dict(icode="1500771", income="03", group1=3, name="OMEPRAZOLE", strength="20 mg.", nhso_adp_code=None,
                  unitprice=Decimal("1.5"), qty=3, sum_price=Decimal("4.5"), is_drug=1, tmtid="104539",
                  tpu_code_list="104539", in_catalog=1, catalog_price=Decimal("1.5"))
    values.update(overrides)
    return it(**values)


# ---------------------------------------------------------------- bill rows
def test_items_are_grouped_and_dated_by_last_rx():
    rows = aipn.bill_rows([it(rxdate=date(2026, 5, 17)), it(rxdate=date(2026, 5, 20))], [])
    assert len(rows) == 1
    assert rows[0].qty == 2 and rows[0].amount == 2000 and rows[0].servdate == date(2026, 5, 20)


def test_home_medication_goes_to_group_04_with_tmt():
    rows = aipn.bill_rows([drug_item(), drug_item(item_type="H", qty=25, sum_price=Decimal("37.5"))], [])
    assert [(r.income, r.billgroup, r.codesys, r.stdcode, r.cscode) for r in rows] == [
        ("03", "03", "TMT", "104539", ""), ("03", "04", "TMT", "104539", ""),
    ]


def test_lab_uses_tmlt_and_keeps_cscode():
    row = aipn.bill_rows([it(group1=7, income="07", nhso_adp_code="30201", tmlt_code="308039")], [])[0]
    assert (row.billgroup, row.cscode, row.codesys, row.stdcode) == ("07", "30201", "TMLT", "308039")


def test_patient_paid_is_claimcat_x_and_not_in_drg_charge():
    rows = aipn.bill_rows([it(), it(icode="3000002", paidst="03", sum_price=Decimal("50"), unitprice=Decimal("50"))], [])
    assert {r.icode: r.claimcat for r in rows} == {"3001372": "D", "3000002": "X"}
    assert aipn.amounts(rows)["drg_charge"] == "1000.00"


def test_claimcat_rate_makes_claimcat_t():
    cat = [{"icode": "3001372", "start_date": date(2026, 1, 1), "finish_date": date(2026, 12, 31), "bill_code": "X1",
            "claim_cat": "T", "claim_amount": "800", "income": "02", "rev_date": date(2022, 1, 1), "remark": ""}]
    row = aipn.bill_rows([it()], cat)[0]
    assert (row.claimcat, row.billgroup, row.claimup, row.claimamt, row.daterev) == ("T", "02", 800, 800, "2022-01-01")
    assert aipn.amounts([row])["xdrg_claim"] == "800.00"


def test_device_with_claimcat_d_moves_to_group_05():
    cat = [{"icode": "3001372", "start_date": date(2026, 1, 1), "finish_date": date(2026, 12, 31), "bill_code": "X1",
            "claim_cat": "T", "claim_amount": "0", "income": "02", "rev_date": None, "remark": "SSOCAC"}]
    assert aipn.bill_rows([it()], cat)[0].billgroup == "05"


def test_exclusions_and_hosxp_noexport():
    items = [it(), it(icode="3000002", hosxp_noexport=1)]
    assert [r.icode for r in aipn.bill_rows(items, [])] == ["3001372"]
    assert [r.icode for r in aipn.bill_rows(items, [], included={"3000002:"})] == ["3000002", "3001372"]
    assert aipn.bill_rows(items, [], excluded={"3001372:"}) == []


def test_items_without_name_or_named_ya_dot_are_skipped():
    rows = aipn.bill_rows([it(), it(icode="3", name=None), it(icode="4", name="ยา.ทดสอบ")], [])
    assert [r.icode for r in rows] == ["3001372"]


# ---------------------------------------------------------------- claim auth
@pytest.mark.parametrize("values,expected", [
    ({"hipdata_code": "SSI"}, "86"), ({"pttype_eclaim_id": "10"}, "80"), ({"pttype_eclaim_id": "13"}, "81"),
    ({"pttype_eclaim_id": "12"}, "85"), ({"pttype_eclaim_id": "12", "copttype": "35"}, "95"),
    ({"upp_code": "81"}, "81"), ({"pttype_eclaim_id": "99"}, "00"),
])
def test_pay_plan(values, expected):
    assert aipn.pay_plan(adm(**values)) == expected


def test_ssi_sends_empty_hmain_and_default_care_as_b():
    a = adm(hipdata_code="SSI", maininscl="SSI", hospmain="10699")
    assert aipn.hmain(a) == ""
    assert aipn.default_care_as(aipn.hmain(a), "10866", ("10699",)) == "B"
    assert aipn.default_care_as("10699", "10866", ("10699",)) == "B"
    assert aipn.default_care_as("10866", "10866", ("10866",)) == "M"
    assert aipn.default_care_as("12345", "10866", ("10699",)) == "A"


def test_auth_date_falls_back_to_admit():
    assert aipn.auth(adm(auth_code="PP1")) == ("PP1", "2026-05-17T10:27:00")
    assert aipn.auth(adm()) == ("", "0000-00-00T00:00:00")


# -------------------------------------------------------------------- checks
def codes(issues):
    return {i.code for i in issues}


def test_clean_admission_passes():
    issues, _ = aipn.check_admission(adm(income=Decimal("1000")), [it()], [], ("11111",))
    assert issues == []


def test_errors_name_the_items():
    items = [
        it(),
        drug_item(in_catalog=0, tmtid=None),
        it(icode="3000096", group1=7, income="07", in_labitem=0, name="07PT"),
    ]
    issues, detail = aipn.check_admission(adm(income=Decimal("1079.5"), pdx="", doctor=""), items, [], ())
    assert {"PDX_EMPTY", "DOCTOR_EMPTY", "DRUG_NOT_IN_CATALOG", "TMT_MISMATCH", "TMLT_INCOMPLETE"} <= codes(issues)
    assert detail["TMLT_INCOMPLETE"] == ["3000096 07PT"]
    assert detail["DRUG_NOT_IN_CATALOG"] == ["1500771 OMEPRAZOLE"]


def test_items_left_out_are_not_checked():
    bad = drug_item(in_catalog=0, tmtid=None, hosxp_noexport=1)
    issues, _ = aipn.check_admission(adm(income=Decimal("1004.5")), [it(), bad], [], ())
    assert issues == []
    issues, _ = aipn.check_admission(adm(income=Decimal("1004.5")), [it(), bad], [], (), included={"1500771:"})
    assert "DRUG_NOT_IN_CATALOG" in codes(issues)


def test_income_mismatch_and_ssi_without_hospmain():
    issues, _ = aipn.check_admission(adm(income=Decimal("5"), hospmain="", hipdata_code="SSI", maininscl="SSI"), [it()], [], ())
    assert "INCOME_MISMATCH" in codes(issues) and "HOSPMAIN_EMPTY" not in codes(issues)


def test_procedure_outside_admission_is_error():
    op = {"icd9": "9312", "doctor": "ว1", "opdate": date(2026, 6, 1), "optime": timedelta(hours=1),
          "enddate": date(2026, 6, 1), "endtime": timedelta(hours=2)}
    issues, detail = aipn.check_admission(adm(income=Decimal("1000")), [it()], [op], ())
    assert detail["OP_OUTSIDE"] == ["9312"]


def test_special_cases_are_blocked():
    r = aipn.review(adm(ods="001", income=Decimal("1000")), [it()], [], ())
    assert r["special"] == "ODS" and not r["selectable"]


# ----------------------------------------------------------------------- XML
def claim(**kw):
    a = adm(income=Decimal("1004.5"), auth_code="PP1", **kw)
    adt = {"an": AN, "hn": "000000001", "idtype": 0, "pidpat": "3100000000001", "title": "นาย", "namepat": "ทดสอบ ผ่าน",
           "dob": "1978-03-25", "sex": "1", "marry": 2, "changwat": "27", "ampur": "02", "nation": 99, "admtype": "O",
           "admsource": "O", "dtadm": "2026-05-17T10:27:00", "dtdisch": "2026-05-20T09:30:00", "leaveday": 0,
           "dischs": "2", "discht": "1", "bw": "65.000", "ward": "01", "clinic": "01"}
    dx = [{"subclass": 1, "code": "I639", "name": "Cerebral infarction", "doctor": "ว1", "codeset": "", "is_tm": 0}]
    ops = [
        {"icd9": "9312", "ext_code": "", "name": "PT", "doctor": "ว1", "opdate": date(2026, 5, 18), "optime": timedelta(hours=11),
         "enddate": date(2026, 5, 18), "endtime": timedelta(hours=11, minutes=30), "regdate": date(2026, 5, 17), "regtime": timedelta(hours=10)},
        {"icd9": "9312", "ext_code": "", "name": "PT again", "doctor": "ว1", "opdate": None, "optime": None,
         "enddate": None, "endtime": None, "regdate": date(2026, 5, 17), "regtime": timedelta(hours=10)},
    ]
    rows = aipn.bill_rows([it(), drug_item(), it(icode="9", sum_price=0, unitprice=0)], [])
    return aipn.Claim(adm=a, adt=adt, dx=dx, ops=ops, rows=rows, care_as="B")


def test_xml_layout_and_checksum():
    data = aipn.claim_xml(claim(), "11111", "รพ.ทดสอบ", datetime(2026, 7, 23, 9, 11, 24))
    head, rest = data.split(b"\r\n", 1)
    assert head == b'<?xml version="1.0" encoding="windows-874"?>'
    body, end = rest.rsplit(b"</CIPN>\r\n", 1)
    body += b"</CIPN>\r\n"
    assert end == f'<?EndNote HMAC="{hashlib.md5(body).hexdigest().upper()}"?>'.encode()
    text = data.decode("cp874")
    assert "<AuthCode>PP1</AuthCode>" in text and "<UPayPlan>80</UPayPlan>" in text and "<CareAs>B</CareAs>" in text
    assert '<IPDx Reccount="1">\r\n1|1|ICD-10|I639|Cerebral infarction|ว1|\r\n</IPDx>' in text
    assert '<IPOp Reccount="1">' in text  # duplicate procedure code is sent once
    assert '<BillItems Reccount="2">' in text  # zero amount rows are left out
    assert "<DRGCharge>1004.50</DRGCharge>" in text
    assert text.endswith('"?>') and "\n\n" not in text
    assert "<Coinsurance>\r\n</Coinsurance>\r\n</CIPN>\r\n" in text


def test_field_text_removes_delimiters():
    assert aipn.field_text("a|b<c>&\r\n") == "a bน้อยกว่าcมากกว่า and"


def test_coinsurance_for_other_right():
    c = claim()
    c.copay = [{"pttype": "34", "total": None, "code": "SSS", "id": "14"}]
    text = aipn.claim_xml(c, "11111", "รพ", datetime(2026, 1, 1)).decode("cp874")
    assert "<InsTypeCode>SSEM72</InsTypeCode>" in text and "<InsTotal>1004.50</InsTotal>" in text


def test_build_zip_names(tmp_path):
    result = aipn.build_zip([claim()], "11111", "รพ", "10120", "ADJ", tmp_path, datetime(2026, 7, 23, 9, 11, 24))
    assert result.zip_path.name == "11111AIPN10120.ZIP"
    with zipfile.ZipFile(result.zip_path) as z:
        assert z.namelist() == [f"11111-AIPN-{AN}-20260723091124-ADJ.XML"]
        assert "AIPN-ADJ</DocSysID>" in z.read(z.namelist()[0]).decode("cp874")


@pytest.mark.parametrize("session", ["", "9999", "100000", "abcde"])
def test_session_must_be_five_digits(session):
    with pytest.raises(aipn.AipnError):
        aipn.normalise_session(session)


# ----------------------------------------------------------------------- API
class FakeAipnDb:
    def __init__(self):
        self.admissions = [
            adm(an=AN, income=Decimal("1000")),
            adm(an=AN2, income=Decimal("1000"), pdx=""),
            adm(an=AN_SSI, income=Decimal("1000"), hipdata_code="SSI", maininscl="SSI", rcm_status="ติด C"),
            adm(an=AN_ODS, income=Decimal("1000"), ods="001"),
        ]
        self.item_rows = [it(an=a["an"]) for a in self.admissions] + [it(an=AN, icode="3000002", sum_price=0, unitprice=0, hosxp_noexport=1)]

    def pttypes(self):
        return [{"pttype": "34", "name": "ประกันสังคม"}]

    def search(self, *args):
        return [dict(a) for a in self.admissions]

    def by_an(self, ans):
        return [dict(a) for a in self.admissions if a["an"] in ans]

    def items(self, ans):
        return [dict(i) for i in self.item_rows if i["an"] in ans]

    def claimcat(self, icodes):
        return []

    def adt(self, ans):
        return [{"an": a} for a in ans]

    def dx(self, ans):
        return [{"an": a, "subclass": 1, "code": "I10", "name": "HT", "doctor": "ว1", "codeset": "", "is_tm": 0} for a in ans]

    def ops(self, ans):
        return []

    def copay(self, ans):
        return []

    def missing_debt(self, ans):
        return set()


@pytest.fixture
def client(tmp_path):
    from test_api import FakeHosxp
    config = replace(settings(), export_dir=tmp_path / "exports")
    return TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"), FakeAipnDb()))


def rows(client):
    data = client.post("/api/aipn/search", json={"start": "2026-05-01", "end": "2026-05-31", "pttypes": ["34"]}).json()
    return {r["an"]: r for r in data["admissions"]}


def test_page_and_search(client):
    assert client.get("/aipn").status_code == 200
    found = rows(client)
    assert found[AN]["selectable"] and found[AN]["default_selected"]
    assert not found[AN2]["selectable"]
    assert found[AN_ODS]["special"] == "ODS"
    assert found[AN_SSI]["hmain"] == "" and found[AN_SSI]["pay_plan"] == "86"
    assert found[AN_SSI]["selectable"] and not found[AN_SSI]["default_selected"]


def test_exclusion_recalculates(client):
    r = client.post("/api/aipn/item", json={"an": AN, "item": "3001372:", "excluded": True}).json()
    assert r["send_amount"] == "0.00" and r["excluded_count"] == 2  # plus the item HOSxP excludes
    r = client.post("/api/aipn/item", json={"an": AN, "item": "3001372:", "excluded": False}).json()
    assert r["send_amount"] == "1000.00" and r["excluded_count"] == 1
    detail = client.post("/api/aipn/detail", json={"an": AN}).json()
    assert {i["key"]: i["excluded"] for i in detail["items"]} == {"3000002:": True, "3001372:": False}


def test_export_blocks_errors_and_writes_zip(client):
    blocked = client.post("/api/aipn/export", json={"ans": [AN, AN2], "session": "10120"})
    assert blocked.status_code == 409 and blocked.json()["blocked"][0]["an"] == AN2
    client.post("/api/aipn/careas", json={"an": AN, "care_as": "A"})
    done = client.post("/api/aipn/export", json={"ans": [AN], "session": "10120"}).json()
    assert done["file"] == "11111AIPN10120.ZIP" and done["total"] == "1000.00"
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/aipn/exports/{done['id']}/download").content))
    content = z.read(z.namelist()[0]).decode("cp874")
    assert "<CareAs>A</CareAs>" in content
    assert rows(client)[AN]["status"] == "ส่งออกแล้ว"
    again = client.post("/api/aipn/export", json={"ans": [AN], "session": "10121"})
    assert again.status_code == 409 and again.json()["needs_confirm"]
    assert client.post("/api/aipn/export", json={"ans": [AN], "session": "10121", "confirm_resend": True}).status_code == 200
    assert client.post("/api/aipn/search", json={"an": AN}).json()["next_session"] == "10122"


def test_export_rejects_bad_session(client):
    r = client.post("/api/aipn/export", json={"ans": [AN], "session": "12"})
    assert r.status_code == 400 and "5 หลัก" in r.json()["detail"]


# ------------------------------------------------------------------- prices
def test_price_warning_shows_chi_price():
    lab = it(icode="3000175", name="07Albumin,urine", group1=7, income="07", in_labitem=1, tmlt_code="1",
             unitprice=Decimal("60"), sum_price=Decimal("60"), lab_price=Decimal("50"))
    issues, detail = aipn.check_admission(adm(income=Decimal("1060")), [it(), lab], [], ())
    assert detail["UNITPRICE_WRONG"] == ["3000175 07Albumin,urine: ราคา สกส. 50.00 บาท (ส่ง 60.00 บาท)"]


def test_edited_price_changes_claim_but_not_income_check():
    lab = it(icode="3000175", name="07Alb", group1=7, income="07", in_labitem=1, tmlt_code="1", qty=2,
             unitprice=Decimal("60"), sum_price=Decimal("120"), lab_price=Decimal("50"))
    items = aipn.apply_prices([it(), lab], {"3000175:": Decimal("50")})
    issues, _ = aipn.check_admission(adm(income=Decimal("1120")), items, [], ())
    assert issues == []  # price now matches สกส. and HOSxP income still matches HOSxP items
    row = next(r for r in aipn.bill_rows(items, []) if r.icode == "3000175")
    assert (row.unitprice, row.amount, row.claimup, row.claimamt) == (50, 100, 50, 100)


def test_price_api(client):
    r = client.post("/api/aipn/price", json={"an": AN, "item": "3001372:", "unitprice": "800"})
    assert r.status_code == 200 and r.json()["send_amount"] == "800.00"
    item = next(i for i in client.post("/api/aipn/detail", json={"an": AN}).json()["items"] if i["key"] == "3001372:")
    assert (item["unitprice"], item["hosxp_unitprice"], item["price_edited"]) == ("800.00", "1000.00", True)
    done = client.post("/api/aipn/export", json={"ans": [AN], "session": "10130"}).json()
    content = zipfile.ZipFile(io.BytesIO(client.get(f"/api/aipn/exports/{done['id']}/download").content))
    xml = content.read(content.namelist()[0]).decode("cp874")
    assert "|800.00|800.00|0.00|" in xml and "<DRGCharge>800.00</DRGCharge>" in xml
    r = client.post("/api/aipn/price", json={"an": AN, "item": "3001372:", "unitprice": None})
    assert r.json()["send_amount"] == "1000.00"


@pytest.mark.parametrize("value", ["abc", "-1", "1.234", "NaN"])
def test_price_api_rejects_bad_values(client, value):
    r = client.post("/api/aipn/price", json={"an": AN, "item": "3001372:", "unitprice": value})
    assert r.status_code == 400
