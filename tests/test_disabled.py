import io
from dataclasses import replace

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.disabled import DisabledError, day, parse_report
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp

A, B, C = "1" * 13, "2" * 13, "3" * 13
HEAD = ["ลำดับ", None, None, "เลขประจำตัวประชาชน", "ชื่อ-นามสกุล", None, "Admit Date", "Discharge Date", None, "วันที่ส่งเบิก",
        "ประเภท", None, None, "สถานะ", None, "จำนวนเงิน\nรวมที่ขอเบิก"]


def workbook(rows, total, hcode="001086600"):
    book = openpyxl.Workbook()
    sheet = book.active
    for line in [[], ["สำนักงานประกันสังคม", None, None, None, None, None, None, None, "หน้าที่"], [],
                 ["รายงานการส่งเบิกค่าบริการทางการแพทย์กรณีค่าบริการทางการแพทย์ผู้ทุพพลภาพ (ผู้ป่วยนอก/ใน)"],
                 [f"รหัสสถานพยาบาล {hcode} โรงพยาบาลทดสอบ"], ["ที่ตั้ง"], [None, "ตั้งแต่วันที่ 01/10/2569 ถึงวันที่ 22/11/2569"], [],
                 HEAD, *rows, ["รวมทั้งสิ้น", *([None] * 13), total]]:
        sheet.append(line)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def row(no, cid, admit, discharge, kind, amount, status="ส่งเบิกแล้ว"):
    return [no, None, None, cid, "x", None, admit, discharge, None, "14/11/2569", kind, None, None, status, None, amount]


SEND = workbook([row(1, A, "01/10/2569", "01/10/2569", "ผู้ป่วยนอก", 400),
                 row(2, B, "05/11/2569", "14/11/2569", "ผู้ป่วยใน", 21883),
                 row(3, C, "06/11/2569", "06/11/2569", "ผู้ป่วยนอก", 300, "ไม่อนุมัติ")], 22583)


def test_send_report_is_read():
    report = parse_report(SEND)
    assert (report.kind, report.hcode, report.period_start, report.period_end, report.total, len(report.rows)) == (
        "submission", "10866", "2026-10-01", "2026-11-22", "22583.00", 3)
    first, second = report.rows[0], report.rows[1]
    assert (first.cid, first.admit_date, first.kind, first.claim_amount, first.claim_date, first.status) == (
        A, "2026-10-01", "OPD", "400.00", "2026-11-14", "ส่งเบิกแล้ว")
    assert (second.kind, second.discharge_date) == ("IPD", "2026-11-14")
    assert not hasattr(first, "name")
    assert day("1 ตุลาคม 2569") == "2026-10-01" and day("31/02/2569") == ""


def test_other_files_are_refused():
    book = openpyxl.Workbook(); book.active.append(["รายงานการส่งเบิกค่าบริการทันตกรรม"]); buffer = io.BytesIO(); book.save(buffer)
    with pytest.raises(DisabledError):
        parse_report(buffer.getvalue())
    with pytest.raises(DisabledError):
        parse_report(b"not excel")


class DisabledHosxp(FakeHosxp):
    def disabled_visits(self, start, end, pttypes=(), cids=()):
        assert pttypes == ("33",)
        visit = lambda cid, hn, vn, an, admit, kind, money, pttype="33": {
            "cid": cid, "hn": hn, "ptname": "ผู้ป่วย", "vn": vn, "an": an, "admit_date": admit, "discharge_date": admit,
            "vsttime": "08:00", "pttype": pttype, "pttype_name": "ประกันสังคม (กรณีทุพพลภาพ)", "income": money, "right_money": money,
            "kind": kind}
        return [visit(A, "000001", "691001080000", "", "2026-10-01", "OPD", 400.0),
                visit(B, "000002", "691105090000", "680002197", "2026-11-05", "IPD", 18683.0),
                visit("9" * 13, "000009", "691110080000", "", "2026-11-10", "OPD", 250.0),
                visit("8" * 13, "000008", "691111080000", "", "2026-11-11", "OPD", 90.0, pttype="34")]


@pytest.fixture
def client(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "exports", hcode="10866")
    return TestClient(create_app(config, DisabledHosxp(), Store(tmp_path / "s.sqlite3")))


def test_claims_match_hosxp_and_unsent_visits_show(client, tmp_path):
    result = client.post("/api/disabled/import?name=RE_DISABLEDOPD_SEND.xlsx", content=SEND).json()
    assert (result["rows"], result["added"], result["kind"]) == (3, 3, "รายงานการส่งเบิก")
    listing = client.post("/api/disabled/list", json={"start": "2026-10-01", "end": "2026-11-22"}).json()
    rows = {(c["admit_date"], c["state"]): c for c in listing["claims"]}
    opd = rows[("2026-10-01", "waiting")]
    assert (opd["vn"], opd["right_ok"], opd["right_money"], opd["claim_amount"], opd["cid"]) == (
        "691001080000", True, "400.00", "400.00", "xxxxxxxxx1111")
    ipd = rows[("2026-11-05", "waiting")]
    assert (ipd["kind"], ipd["an"], ipd["right_money"], ipd["claim_amount"], ipd["discharge_date"]) == (
        "IPD", "680002197", "18683.00", "21883.00", "2026-11-14")
    assert rows[("2026-11-06", "rejected")]["vn"] == ""
    unsent = [c for c in listing["claims"] if c["state"] == "unclaimed"]
    assert [c["vn"] for c in unsent] == ["691110080000"]  # pttype 34 is not the disability right
    assert list((tmp_path / "disabled").glob("*_RE_DISABLEDOPD_SEND.xlsx"))
    other = client.post("/api/disabled/import?name=x.xlsx", content=workbook([row(1, A, "01/10/2569", "01/10/2569", "ผู้ป่วยนอก", 1)], 1,
                                                                             hcode="001099900"))
    assert other.status_code == 400


def test_menu_has_the_disability_page(client):
    page = client.get("/disabled")
    assert page.status_code == 200 and 'href="/disabled" aria-current="page"' in page.text
    assert 'href="/disabled"' in client.get("/").text
