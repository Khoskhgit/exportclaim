import io
from datetime import date
from decimal import Decimal

import openpyxl
from fastapi.testclient import TestClient

from app.labwalkin_routes import thai_date
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp

CID = "1234567890123"


class LabHosxp(FakeHosxp):
    def __init__(self):
        super().__init__()
        self.calls = []

    def lab_walkin(self, start, end, hcode):
        self.calls.append((start, end, hcode))
        row = dict(vn="681001000001", icode="3000001", cid=CID, hn="000001", ptname="นายทดสอบ ผ่าน",
                   vstdate=date(2025, 10, 3), hospmain="11112", hospmain_name="รพ.สต.ทดสอบ",
                   lab_name="HbA1c", qty=1, unitcost=Decimal("150"), sum_price=Decimal("150"))
        return [row, {**row, "icode": "3000002", "lab_name": "Lipid", "sum_price": Decimal("200.50")},
                {**row, "vn": "681001000002", "hn": "000002", "hospmain": "11113", "hospmain_name": "รพ.สต.สอง"}]


def client(tmp_path):
    db = LabHosxp()
    return TestClient(create_app(settings(), hosxp=db, store=Store(tmp_path / "s.sqlite3"))), db


def test_search_masks_cid_and_sums_by_hospital(tmp_path):
    c, db = client(tmp_path)
    res = c.post("/api/labwalkin", json={"start": "2025-10-01", "end": "2025-10-31"})
    assert res.status_code == 200
    data = res.json()
    assert db.calls == [(date(2025, 10, 1), date(2025, 10, 31), "11111")]
    assert len(data["rows"]) == 3 and data["rows"][0]["cid"] == "x" * 9 + "0123"
    assert data["rows"][0]["hospmain_name"] == "11112 - รพ.สต.ทดสอบ"
    first = next(h for h in data["hospitals"] if h["hospmain"] == "11112")
    assert (first["visits"], first["people"], first["items"], Decimal(first["amount"])) == (1, 1, 2, Decimal("350.50"))


def test_excel_has_full_rows_and_summary(tmp_path):
    c, _ = client(tmp_path)
    res = c.post("/api/labwalkin/excel", json={"start": "2025-10-01", "end": "2025-10-31"})
    assert res.status_code == 200 and "LAB_WalkIn_20251001_20251031.xlsx" in res.headers["content-disposition"]
    book = openpyxl.load_workbook(io.BytesIO(res.content))
    rows = list(book["LAB Walk-in"].iter_rows(values_only=True))
    assert rows[2][0] == "VN" and rows[3][2] == CID and rows[3][5] == "03/10/2568"
    assert rows[-1][0] == "รวม" and rows[-1][11] == 500.5
    summary = list(book["สรุปตาม รพ.หลัก"].iter_rows(values_only=True))
    assert summary[-1] == ("รวม", 2, 2, 3, 500.5)


def test_bad_period_is_refused(tmp_path):
    c, _ = client(tmp_path)
    assert c.post("/api/labwalkin", json={"start": "", "end": "2025-10-31"}).status_code == 400
    assert c.post("/api/labwalkin", json={"start": "2025-11-01", "end": "2025-10-31"}).status_code == 400
    assert c.post("/api/labwalkin", json={"start": "2024-01-01", "end": "2025-10-31"}).status_code == 400


def test_page_and_menu(tmp_path):
    c, _ = client(tmp_path)
    page = c.get("/labwalkin")
    assert page.status_code == 200 and 'href="/labwalkin" aria-current="page"' in page.text


def test_thai_date():
    assert thai_date("2025-10-31") == "31/10/2568" and thai_date(None) == ""
