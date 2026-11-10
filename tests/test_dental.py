import io
from dataclasses import replace

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.dental import DentalError, parse_report
from app.dental_routes import pick_visit
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp

A, B, C = "1" * 13, "2" * 13, "3" * 13
HEAD_SUB = ["ลำดับ", "เลขประจำตัว\nประชาชน", "ชื่อ-นามสกุล", "วันที่ใช้สิทธิ", "วันที่ส่งเบิก", "ประเภท\nค่าบริการ",
            "สถานะรับแจ้ง", "จำนวนเงินที่\nขอเบิก"]
HEAD_PAY = ["ลำดับ", "เลขประจำตัว\nประชาชน", "ชื่อ-นามสกุล", "วันที่ใช้สิทธิ", "วันที่ส่งเบิก", "ประเภท\nค่าบริการ",
            "จำนวนเงินที่\nขอเบิก", "จำนวนเงินจ่าย", "เลขที่ใบสำคัญรับเงิน", "วันที่โอนเข้าบัญชี"]


def workbook(title, head, rows, total):
    book = openpyxl.Workbook()
    sheet = book.active
    for line in [[], [title], [], ["รหัสสถานพยาบาล (5 หลัก) : 10866  สถานพยาบาล : ทดสอบ"], ["สถานที่รับบริการ สถานพยาบาล"],
                 ["ตั้งแต่วันที่ 01 ตุลาคม 2569  ถึงวันที่ 01 พฤศจิกายน 2569"], [], head, *rows, ["รวมทั้งสิ้น", *total]]:
        sheet.append(line)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


SUB = workbook("รายงานการส่งเบิกค่าบริการทางการแพทย์ทันตกรรมของสถานพยาบาลประกันสังคม", HEAD_SUB, [
    ["1", A, "x", "01 ตุลาคม 2569", "01 ตุลาคม 2569", "อุด ขูด ถอน", "จ่ายแล้ว", "700"],
    ["2", B, "x", "05 ตุลาคม 2569", "05 ตุลาคม 2569", "อุด ขูด ถอน", "รอจ่าย/ส่งเลขคำสั่งจ่ายให้ Core", "350"],
    ["3", C, "x", "06 ตุลาคม 2569", "06 ตุลาคม 2569", "อุด ขูด ถอน", "ไม่อนุมัติรายการเบิก", "800"],
], ["1850"])
PAY = workbook("รายงานการจ่ายค่าบริการทางการแพทย์ทันตกรรมของสถานพยาบาลประกันสังคม", HEAD_PAY, [
    ["1", A, "x", "01 ตุลาคม 2569", "01 ตุลาคม 2569", "อุด ขูด ถอน", "700", "650", "2701691000000158MD", "28 ตุลาคม 2569"],
], ["700", "650"])


def test_reports_are_read():
    sub, pay = parse_report(SUB), parse_report(PAY)
    assert (sub.kind, sub.hcode, sub.period_start, sub.period_end, sub.total, len(sub.rows)) == (
        "submission", "10866", "2026-10-01", "2026-11-01", "1850.00", 3)
    assert sub.rows[1].status.startswith("รอจ่าย") and sub.rows[0].claim_amount == "700.00"
    row = pay.rows[0]
    assert (pay.kind, row.paid_amount, row.voucher, row.transfer_date) == ("payment", "650.00", "2701691000000158MD", "2026-10-28")
    assert not hasattr(row, "name")


def test_other_files_are_refused():
    book = openpyxl.Workbook(); book.active.append(["อื่น"]); buffer = io.BytesIO(); book.save(buffer)
    with pytest.raises(DentalError):
        parse_report(buffer.getvalue())
    with pytest.raises(DentalError):
        parse_report(b"not excel")


def test_visit_with_dental_work_wins():
    visits = [{"vn": "2", "vsttime": "08:00", "has_dental": 0}, {"vn": "3", "vsttime": "10:00", "has_dental": 1}]
    assert pick_visit(visits)["vn"] == "3" and pick_visit([{"vn": None}]) is None


class DentalHosxp(FakeHosxp):
    def dental_visits(self, cids, start, end):
        return [
            {"cid": A, "hn": "000001", "ptname": "ผู้ป่วย หนึ่ง", "vn": "691001080000", "vstdate": "2026-10-01",
             "vsttime": "08:00", "pttype_name": "ประกันสังคม", "has_dental": 1},
            {"cid": B, "hn": "000002", "ptname": "ผู้ป่วย สอง", "vn": None, "vstdate": None, "vsttime": None,
             "pttype_name": None, "has_dental": 0},
        ]


@pytest.fixture
def client(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "exports", hcode="10866")
    return TestClient(create_app(config, DentalHosxp(), Store(tmp_path / "s.sqlite3")))


def upload(client, name, data):
    return client.post(f"/api/dental/import?name={name}", content=data)


def test_import_both_reports_in_any_order(client, tmp_path):
    first = upload(client, "REPORT_02.xlsx", PAY).json()
    assert first["added"] == 1
    second = upload(client, "REPORT_01.xlsx", SUB).json()
    assert (second["added"], second["updated"]) == (2, 1)
    claims = {c["service_date"]: c for c in client.post("/api/dental/list", json={}).json()["claims"]}
    paid = claims["2026-10-01"]
    assert (paid["state"], paid["paid_amount"], paid["claim_amount"], paid["vn"], paid["has_dental"]) == (
        "paid", "650.00", "700.00", "691001080000", True)
    assert paid["cid"] == "xxxxxxxxx1111"
    assert claims["2026-10-05"]["state"] == "waiting" and claims["2026-10-05"]["vn"] == "" and claims["2026-10-05"]["in_hosxp"]
    assert claims["2026-10-06"]["state"] == "rejected" and not claims["2026-10-06"]["in_hosxp"]
    again = upload(client, "REPORT_01.xlsx", SUB).json()
    assert again["again"] and again["added"] == 0
    assert len(client.post("/api/dental/list", json={"start": "2026-10-02", "end": "2026-10-31"}).json()["claims"]) == 2
    assert list((tmp_path / "dental").glob("*_REPORT_01.xlsx"))


def test_report_of_another_hospital_is_refused(client):
    book = openpyxl.load_workbook(io.BytesIO(SUB))
    book.active["A4"] = "รหัสสถานพยาบาล (5 หลัก) : 10999  สถานพยาบาล : อื่น"
    buffer = io.BytesIO(); book.save(buffer)
    response = upload(client, "x.xlsx", buffer.getvalue())
    assert response.status_code == 400 and "10999" in response.json()["detail"]


def test_several_claims_on_one_day_are_kept_apart(client):
    sub = workbook("รายงานการส่งเบิกค่าบริการทางการแพทย์ทันตกรรมของสถานพยาบาลประกันสังคม", HEAD_SUB, [
        ["1", A, "x", "12 ตุลาคม 2569", "12 ตุลาคม 2569", "อุด ขูด ถอน", "จ่ายแล้ว", "350"],
        ["2", A, "x", "12 ตุลาคม 2569", "12 ตุลาคม 2569", "อุด ขูด ถอน", "ไม่อนุมัติรายการเบิก", "700"],
        ["3", A, "x", "12 ตุลาคม 2569", "12 ตุลาคม 2569", "อุด ขูด ถอน", "รอจ่าย/ส่งเลขคำสั่งจ่ายให้ Core", "700"],
    ], ["1750"])
    pay = workbook("รายงานการจ่ายค่าบริการทางการแพทย์ทันตกรรมของสถานพยาบาลประกันสังคม", HEAD_PAY, [
        ["1", A, "x", "12 ตุลาคม 2569", "12 ตุลาคม 2569", "อุด ขูด ถอน", "350", "350", "V1", "28 ตุลาคม 2569"],
    ], ["350", "350"])
    upload(client, "s.xlsx", sub)
    upload(client, "p.xlsx", pay)
    claims = client.post("/api/dental/list", json={}).json()["claims"]
    assert sorted((c["claim_amount"], c["state"]) for c in claims) == [
        ("350.00", "paid"), ("700.00", "rejected"), ("700.00", "waiting")]
