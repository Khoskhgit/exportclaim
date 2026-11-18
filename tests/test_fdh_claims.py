import io

import openpyxl
from fastapi.testclient import TestClient

from app import fdh_claims
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp

HEAD = ["ลำดับที่", "รหัสการเคลม", "HN", "รหัสบริการ (SEQ)", "รหัสผู้ป่วยใน (AN)", "ประเภทผู้ป่วย", "วันเข้ารับบริการ",
        "วันที่รับการรักษา", "วันจำหน่ายออก", "การใช้สิทธิ", "วันที่ส่งเคลม", "สิทธิ", "สถานะรายการเคลม"]


def report(*rows):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "ClaimDetail"
    for line in [["", "รายละเอียดรายการเคลมของหน่วยบริการ"], ["หน่วยบริการ : ทดสอบ"], ["ออกรายงาน : วันที่ 18/11/2026"], [], HEAD, *rows]:
        sheet.append(line)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


ROW = ["1", "claim-1", "000044692", "690702065309", "", "OP", "02-07-2026 06:53", "", "", "1", "18-07-2026 21:45", "STP", "เหมาจ่ายรายหัว"]


def test_parse_reads_the_claims():
    (r,) = fdh_claims.parse(report(ROW))
    assert (r["claim_id"], r["seq"], r["kind"], r["service_at"], r["uuc"], r["sent_at"], r["fund"], r["status"]) == (
        "claim-1", "690702065309", "OPD", "2026-07-02 06:53", "1", "2026-07-18 21:45", "STP", "เหมาจ่ายรายหัว")


def test_other_files_are_refused():
    book = openpyxl.Workbook(); book.active.append(["อื่น"]); buffer = io.BytesIO(); book.save(buffer)
    try:
        fdh_claims.parse(buffer.getvalue())
    except fdh_claims.ClaimDetailError:
        return
    raise AssertionError("should be refused")


def test_import_updates_by_claim_id_and_shows_in_history(tmp_path):
    c = TestClient(create_app(settings(), hosxp=FakeHosxp(), store=Store(tmp_path / "s.sqlite3")))
    first = c.post("/api/repstm/import?name=x-ClaimDetail.xlsx", content=report(ROW))
    assert first.status_code == 200 and "STP 1 รายการ" in first.json()["summary"]
    again = c.post("/api/repstm/import?name=x-ClaimDetail.xlsx", content=report(ROW[:-1] + ["จ่ายแล้ว"]))
    assert "อัปเดต 1" in again.json()["summary"]
    listing = c.get("/api/repstm/list").json()
    assert listing["fdh"][0]["status"] == "จ่ายแล้ว" and listing["fdh"][0]["n"] == 1
    assert any("FDH ClaimDetail" in u["summary"] for u in listing["uploads"])
    assert c.post("/api/repstm/vn", json={"vn": "690702065309"}).json()["fdh"][0]["claim_id"] == "claim-1"
    store = fdh_claims.FdhClaimStore(Store(tmp_path / "s.sqlite3"))
    assert store.latest(["690702065309"])["690702065309"]["fund"] == "STP"
