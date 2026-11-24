import io
import time
from dataclasses import replace
from datetime import date, datetime

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import autopull, mms_web
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp

REP_HEAD = ["ลำดับ", "เลขประจำตัว\nประชาชน", "ชื่อ-นามสกุล", "วันที่ใช้สิทธิ", "วันที่ส่งเบิก", "ประเภท\nค่าบริการ",
            "สถานะรับแจ้ง", "จำนวนเงินที่\nขอเบิก"]
STM_HEAD = ["ลำดับ", "เลขประจำตัว\nประชาชน", "ชื่อ-นามสกุล", "วันที่ใช้สิทธิ", "วันที่ส่งเบิก", "ประเภท\nค่าบริการ",
            "จำนวนเงินที่\nขอเบิก", "จำนวนเงินจ่าย", "เลขที่ใบสำคัญรับเงิน", "วันที่โอนเข้าบัญชี"]


def report_xlsx(kind: str, start: date, end: date) -> bytes:
    def th(d):
        return f"{d.day:02d} {mms_web.THAI_MONTHS[d.month - 1]} {d.year + 543}"
    book = openpyxl.Workbook()
    ws = book.active
    title = "รายงานการจ่ายค่าบริการทางการแพทย์ทันตกรรมของสถานพยาบาลประกันสังคม" if kind == "stm" else \
        "รายงานการส่งเบิกค่าบริการทางการแพทย์ทันตกรรมของสถานพยาบาลประกันสังคม"
    ws.append([title])
    ws.append(["รหัสสถานพยาบาล (5 หลัก) : 10866  สถานพยาบาล : โรงพยาบาลทดสอบ"])
    ws.append([f"ตั้งแต่วันที่ {th(start)}  ถึงวันที่ {th(end)}"])
    if kind == "stm":
        ws.append(STM_HEAD)
        ws.append(["1", "1111111111111", "ทดสอบ หนึ่ง", th(start), th(start), "อุด ขูด ถอน", 700, 700, "V1", th(end)])
        ws.append(["2", "2222222222222", "ทดสอบ สอง", th(start), th(start), "อุด ขูด ถอน", "350", "300", "V1", th(end)])
        ws.append(["รวมทั้งสิ้น", None, None, None, None, None, 1050, 1000])
    else:
        ws.append(REP_HEAD)
        ws.append(["1", "1111111111111", "ทดสอบ หนึ่ง", th(start), th(start), "อุด ขูด ถอน", "จ่ายแล้ว", "700"])
        ws.append(["2", "2222222222222", "ทดสอบ สอง", th(start), th(start), "อุด ขูด ถอน", "รับแจ้ง", "350"])
        ws.append(["3", "3333333333333", "ทดสอบ สาม", th(end), th(end), "อุด ขูด ถอน", "รับแจ้ง", "1,150"])
        ws.append(["รวมทั้งสิ้น", "2200"])
    if end.day == 2:  # an empty report: a lone "1" row
        ws.delete_rows(5, ws.max_row)
        ws.append([1])
        ws.append(["รวมทั้งสิ้น"])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_month_ranges_and_codes():
    assert mms_web.month_ranges(date(2026, 11, 24), 2) == [(date(2026, 11, 1), date(2026, 11, 23)),
                                                         (date(2026, 10, 1), date(2026, 10, 31))]
    assert mms_web.month_ranges(date(2026, 11, 1), 2) == [(date(2026, 10, 1), date(2026, 10, 31))]  # nothing of today
    assert mms_web.month_ranges(date(2026, 3, 15), 2)[1] == (date(2026, 2, 1), date(2026, 2, 28))
    assert mms_web.hosp_code9("10866") == "001086600"
    assert mms_web.thai_date("01 ตุลาคม 2569") == "2026-10-01" and mms_web.thai_date("รวม") == ""


def test_parse_rep_and_stm():
    rep = mms_web.parse_report(report_xlsx("rep", date(2026, 10, 1), date(2026, 10, 31)))
    assert rep["kind"] == "rep" and (rep["start"], rep["end"]) == ("2026-10-01", "2026-10-31")
    assert [r["seq"] for r in rep["rows"]] == [1, 2, 3] and rep["claim"] == 2200
    assert rep["rows"][0] == {"seq": 1, "pid": "1111111111111", "service_date": "2026-10-01", "submit_date": "2026-10-01",
                              "service_type": "อุด ขูด ถอน", "status": "จ่ายแล้ว", "claim": 700.0, "paid": 0.0,
                              "voucher": "", "transfer_date": ""}
    assert all("name" not in r for r in rep["rows"])  # the patient name is not kept
    stm = mms_web.parse_report(report_xlsx("stm", date(2026, 10, 1), date(2026, 10, 31)))
    assert stm["kind"] == "stm" and stm["paid"] == 1000 and stm["rows"][1]["transfer_date"] == "2026-10-31"
    empty = mms_web.parse_report(report_xlsx("stm", date(2026, 11, 1), date(2026, 11, 2)))
    assert empty["rows"] == [] and empty["paid"] == 0
    with pytest.raises(mms_web.MmsWebError, match="อ่านไฟล์"):
        mms_web.parse_report(b"not a workbook")


class FakeMms:
    def __init__(self):
        self.asked, self.logins = [], 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self):
        self.logins += 1

    def report(self, kind, start, end):
        self.asked.append((kind, start, end))
        if kind == "stm" and sum(k == "stm" for k, _, _ in self.asked) == 2:  # the second STM fails
            raise mms_web.MmsWebError("รายงานสร้างไม่เสร็จในเวลา")
        return mms_web.Report(kind, start, end, "x.xlsx", report_xlsx(kind, start, end))


def wait(client, url):
    for _ in range(300):
        status = client.get(url).json()
        if not status["job"]["running"]:
            return status
        time.sleep(0.02)
    raise AssertionError("pull did not finish")


def test_pull_keeps_each_month_and_replaces_it(tmp_path):
    fake = FakeMms()
    config = replace(settings(), mms_user="u", mms_password="p", mms_hosp_code="001086600")
    client = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"), mms=lambda: fake))
    assert client.get("/api/repstm/mms").json()["ready"] is True
    assert client.post("/api/repstm/mms", json={"months": 2}).status_code == 200
    status = wait(client, "/api/repstm/mms")
    job = status["job"]
    ranges = mms_web.month_ranges(date.today(), 2)
    assert fake.logins == 1 and len(fake.asked) == 2 * len(ranges)
    assert job["imported"] == 2 * len(ranges) - (len(ranges) > 1) and job["errors"] == (len(ranges) > 1)
    months = {m["month"]: m for m in status["months"]}
    assert set(months) == {start.strftime("%Y-%m") for start, _ in ranges}
    assert all(m["rep"]["rows"] == 3 and m["rep"]["claim"] == 2200 for m in months.values())
    newest = months[ranges[0][0].strftime("%Y-%m")]
    assert newest["stm"]["paid"] == 1000 and newest["date_end"] == ranges[0][1].isoformat()
    kind_month = next((l["type"], l["month"]) for l in job["lines"] if l.get("kind") == "mms" and l["ok"])
    got = client.get(f"/api/repstm/mms/file?kind={kind_month[0]}&month={kind_month[1]}")
    assert got.status_code == 200 and got.content.startswith(b"PK")
    assert client.get("/api/repstm/mms/file?kind=rep&month=1999-01").status_code == 404
    assert client.post("/api/repstm/mms", json={"months": 2}).status_code == 200  # again: same months, replaced
    wait(client, "/api/repstm/mms")
    with Store(tmp_path / "s.sqlite3")._db() as db:
        assert db.execute("SELECT COUNT(*) FROM mms_dental_files WHERE kind='rep'").fetchone()[0] == len(ranges)
        assert db.execute("SELECT COUNT(*) FROM mms_dental_rows WHERE kind='rep'").fetchone()[0] == 3 * len(ranges)
        assert "name" not in {r[1] for r in db.execute("PRAGMA table_info(mms_dental_rows)")}


def test_pull_needs_settings(tmp_path):
    client = TestClient(create_app(replace(settings(), mms_user="", mms_password=""), FakeHosxp(),
                                   Store(tmp_path / "s.sqlite3")))
    assert client.get("/api/repstm/mms").json()["ready"] is False
    assert "MMS_USER" in client.post("/api/repstm/mms", json={}).json()["detail"]
    with pytest.raises(mms_web.MmsWebError, match="MMS_USER"):
        mms_web.MmsWeb("", "", "001086600").__enter__()


def test_summary_and_flex_include_mms():
    mms = {"months": 2, "total": 4, "imported": 3, "errors": 1, "lines": [
        {"kind": "mms", "ok": True, "type": "rep", "rep_no": "REP ส่งเบิก 11/2569", "text": "3 รายการ ขอเบิก 2,200.00 บาท"},
        {"kind": "mms", "ok": False, "type": "stm", "rep_no": "STM การจ่าย 10/2569", "text": "หมดเวลา"}]}
    text = autopull.summary(None, None, datetime(2026, 11, 25, 6, 0), 2570, None, mms)
    assert "[ทันตกรรม ประกันสังคม (MMS) ย้อนหลัง 2 เดือน]" in text and "ดึง 3/4 รายงาน" in text
    assert "- REP ส่งเบิก 11/2569: 3 รายการ ขอเบิก 2,200.00 บาท" in text and "STM การจ่าย 10/2569: หมดเวลา" in text
    card = str(autopull.flex(None, None, datetime(2026, 11, 25, 6, 0), 2570, None, mms))
    assert "ทันตกรรม ประกันสังคม (MMS)" in card and "REP ส่งเบิก 11/2569" in card
